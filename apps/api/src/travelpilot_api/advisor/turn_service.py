"""Application orchestration for one Advisor message turn."""

import re
from datetime import UTC, datetime
from time import perf_counter
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from travelpilot_core.agents.advisor_reply import compose_advisor_reply_with_diagnostics
from travelpilot_core.agents.extraction import fallback_extract
from travelpilot_core.ports.tools import ToolFailure
from travelpilot_infra.db.models.advisor import (
    AdvisorMessageModel,
    AdvisorRunEventModel,
    AdvisorSessionModel,
)
from travelpilot_infra.db.models.user import UserModel
from travelpilot_infra.db.models.workflow import UserMemorySettingsModel
from travelpilot_infra.providers.maps.errors import MapsProviderError

from travelpilot_api.advisor.budgets import remaining_turn_output_tokens
from travelpilot_api.advisor.candidate_identity import provider_candidate_id
from travelpilot_api.advisor.candidate_selection import selected_ids_after_food_search
from travelpilot_api.advisor.checkpoints import checkpoint_writer
from travelpilot_api.advisor.context import (
    draft_meal_follow_up_options,
    planning_state,
    refresh_trip_snapshot,
    sync_personal_memory_snapshot,
    trip_action_items,
    trip_completeness_audit,
)
from travelpilot_api.advisor.conversation import (
    recent_conversation_context,
    recent_verified_candidate_context,
)
from travelpilot_api.advisor.destination_discovery import run_destination_discovery
from travelpilot_api.advisor.destination_service import (
    resume_destination_recommendation,
    start_destination_recommendation_from_chat,
)
from travelpilot_api.advisor.evidence_reuse import (
    pending_evidence_scope_matches,
    pending_evidence_scope_status,
    reusable_route_from_pending_turn,
    reusable_weather_from_pending_turn,
)
from travelpilot_api.advisor.guide_evidence import (
    guide_references_for_place,
    load_trip_guide_references,
)
from travelpilot_api.advisor.intent_checkpoint import (
    delete_intent_checkpoint,
    intent_checkpoint_scope_ref,
    load_intent_checkpoint,
    save_intent_checkpoint,
)
from travelpilot_api.advisor.interpretation import (
    _active_personal_memories,
    _apply_existing_adjustments,
    _candidate_planning_category,
    _explicit_schedule_hint,
    _extract_advisor_intent,
    _is_schedule_recommendation_request,
    _normalized_place_name,
    event_replan_prefill,
    should_use_deterministic_schedule_extraction,
)
from travelpilot_api.advisor.orchestration_state import (
    direct_route_checkpoint_writer,
    direct_route_scope_ref,
    load_direct_route_checkpoint,
    load_readonly_tool_decision,
    load_route_evidence_checkpoint,
    load_search_evidence_checkpoint,
    load_turn_stage_checkpoint,
    load_weather_evidence_checkpoint,
    readonly_tool_decision_callbacks,
    readonly_tool_decision_writer,
    resolve_stage_order,
    route_evidence_checkpoint_writer,
    search_evidence_checkpoint_writer,
    search_scope_ref,
    weather_evidence_checkpoint_writer,
    weather_scope_ref,
)
from travelpilot_api.advisor.request_planning import (
    extract_explicit_destination_city,
    is_schedule_proposal_edit_followup,
    plan_advisor_request,
    should_start_destination_recommendation_from_chat,
)
from travelpilot_api.advisor.route_query import run_advisor_route_query
from travelpilot_api.advisor.scheduling import propose_existing_trip_schedule
from travelpilot_api.advisor.search_loop import run_advisor_search_loop
from travelpilot_api.advisor.session_budget import (
    handle_budget_continuation,
    queue_budget_continuation,
    record_session_usage,
    remaining_session_output_tokens,
    session_output_limit,
    session_usage,
)
from travelpilot_api.advisor.sessions import read_session
from travelpilot_api.advisor.stage_execution import AdvisorTurnExecutor
from travelpilot_api.advisor.state import cancelled_task_reply, pending_context_fingerprint
from travelpilot_api.advisor.task_state import AdvisorTaskSnapshot, resolve_task_status
from travelpilot_api.advisor.task_taxonomy import (
    accepts_driving_route_confirmation,
    is_preparation_request,
    is_route_query_request,
    requests_unsupported_route_mode,
    unsupported_route_mode_question,
)
from travelpilot_api.advisor.tools import advisor_tool_runtime, map_evidence_source
from travelpilot_api.advisor.turn_lifecycle import resolve_pending_turn
from travelpilot_api.advisor.turn_observation import build_stage_selection_observation
from travelpilot_api.advisor.turn_orchestration import (
    choose_stage_order,
    resolve_turn_stage_order,
)
from travelpilot_api.advisor.turn_persistence import (
    persist_failed_turn_usage as _persist_failed_turn_usage,
)
from travelpilot_api.advisor.turn_persistence import (
    save_budget_control_message as _save_budget_control_message_impl,
)
from travelpilot_api.advisor.turn_persistence import (
    turn_usage_snapshot as _turn_usage_snapshot,
)
from travelpilot_api.advisor.turn_plan import build_advisor_turn_plan
from travelpilot_api.advisor.turn_policy import (
    advisor_reply_source,
    can_compose_grounded_reply,
    grounded_general_trip_tip,
    grounded_reply_candidates,
    unsupported_booking_action,
)
from travelpilot_api.advisor.turn_reporting import build_turn_agent_report
from travelpilot_api.advisor.turn_responses import (
    append_route_outcome as _append_route_outcome,
)
from travelpilot_api.advisor.turn_responses import (
    append_weather_context as _append_weather_context,
)
from travelpilot_api.advisor.turn_responses import (
    schedule_proposal_reply as _schedule_proposal_reply,
)
from travelpilot_api.advisor.turn_responses import (
    transport_route_summary as _transport_route_summary,
)
from travelpilot_api.advisor.turn_responses import (
    weather_day_summary as _weather_day_summary,
)
from travelpilot_api.advisor.weather_advice import daily_weather_advice
from travelpilot_api.schemas.advisor import AdvisorSessionRead
from travelpilot_api.settings import settings


async def _resume_destination_recommendation(
    *, db: AsyncSession, row: AdvisorSessionModel, user: UserModel, maps,
    answer: str, pending, resumed, idempotency_key: str | None = None,
) -> AdvisorSessionRead:
    """Compatibility adapter while destination persistence lives in its service."""
    return await resume_destination_recommendation(
        db=db,
        row=row,
        user=user,
        maps=maps,
        answer=answer,
        pending=pending,
        resumed=resumed,
        idempotency_key=idempotency_key,
        discovery_runner=run_destination_discovery,
        session_reader=read_session,
    )


async def _memory_suggestions_enabled(db: AsyncSession, owner_id) -> bool:
    getter = getattr(db, "get", None)
    if getter is None:
        return False
    settings_row = await getter(UserMemorySettingsModel, owner_id)
    return bool(settings_row and settings_row.enabled)


async def _save_budget_control_message(*args, **kwargs) -> AdvisorSessionRead:
    """Keep the session reader injectable at the turn-service boundary."""
    return await _save_budget_control_message_impl(
        *args, **kwargs, session_reader=read_session,
    )


def _reviewable_memory_suggestion(composed, *, enabled: bool, user_message: str):
    if composed is None or not enabled or composed.memory_suggestion is None:
        return None
    suggestion = composed.memory_suggestion
    if suggestion.evidence.strip() not in user_message:
        return None
    return suggestion.model_dump()




def _missing_meal_preference_question(content: str, candidate_count: int) -> str | None:
    normalized = content.casefold()
    if candidate_count < 1 or any(
        term in normalized for term in ("午餐", "晚餐", "早餐", "用餐", "吃饭", "饭点")
    ):
        return None
    return "行程排程前还想确认用餐空档：需要每天预留约 1 小时午餐吗？也可以指定某一天，或先不安排用餐。"

async def execute_advisor_turn(
    db: AsyncSession,
    row: AdvisorSessionModel,
    content: str,
    maps,
    weather,
    idempotency_key: str | None,
    user: UserModel,
    *,
    resume_existing_user_message: bool = False,
) -> AdvisorSessionRead:
    started_at = perf_counter()
    session_id, owner_id = row.id, row.owner_id
    submitted_content = content
    extraction = None
    composed_result = None
    reply_tokens = 0
    reply_output_tokens = 0
    search_runtime = None
    schedule_runtime = None
    transport_result = None
    await refresh_trip_snapshot(db, row)
    context, _ = sync_personal_memory_snapshot(
        dict(row.context or {}), await _active_personal_memories(db, user.id),
    )
    explicitly_selected_candidate_ids = {
        str(value) for value in context.pop("selection_confirmation_candidate_ids", [])
        if isinstance(value, str)
    }
    memory_suggestions_enabled = await _memory_suggestions_enabled(db, user.id)
    row.context = context
    invalidated_by_context_update = bool(context.pop("pending_task_invalidated", False))
    row.context = context
    turn_output_budget = settings.llm_max_tokens_per_turn
    if settings.llm_api_key and settings.llm_model:
        action, queued_goal, _ = handle_budget_continuation(
            context, content, per_turn_grant=settings.llm_max_tokens_per_turn,
        )
        if action == "CONTINUE":
            content = queued_goal or content
        elif action == "CANCEL":
            return await _save_budget_control_message(
                db, row, user, context, submitted_content,
                "已取消本次顾问请求，旅行数据未改变。",
                {"response_source": "TEMPLATE", "budget_continuation": {"required": False}},
                idempotency_key=idempotency_key,
                resume_existing_user_message=resume_existing_user_message,
            )
        elif action == "PROMPT":
            return await _save_budget_control_message(
                db, row, user, context, submitted_content,
                "本次对话已达到模型输出额度。若要继续刚才的任务，请明确点击下方按钮；这会增加一轮额度。",
                {"response_source": "TEMPLATE", "budget_continuation": {
                    "required": True, "action_label": "继续本次对话", "prompt": "继续本次对话",
                    "grant_output_tokens": settings.llm_max_tokens_per_turn,
                    "used_output_tokens": session_usage(context)["output_tokens"],
                    "session_output_limit": session_output_limit(context, settings.llm_max_output_tokens_per_session),
                }},
                idempotency_key=idempotency_key,
                resume_existing_user_message=resume_existing_user_message,
            )
        remaining = remaining_session_output_tokens(
            context, settings.llm_max_output_tokens_per_session,
        )
        turn_output_budget = min(settings.llm_max_tokens_per_turn, remaining)
        if turn_output_budget < 128:
            queue_budget_continuation(context, content)
            return await _save_budget_control_message(
                db, row, user, context, submitted_content,
                "本次对话已达到模型输出额度。若要继续刚才的任务，请明确点击下方按钮；这会增加一轮额度。",
                {"response_source": "TEMPLATE", "budget_continuation": {
                    "required": True, "action_label": "继续本次对话", "prompt": "继续本次对话",
                    "grant_output_tokens": settings.llm_max_tokens_per_turn,
                    "used_output_tokens": session_usage(context)["output_tokens"],
                    "session_output_limit": session_output_limit(context, settings.llm_max_output_tokens_per_session),
                }},
                idempotency_key=idempotency_key,
                resume_existing_user_message=resume_existing_user_message,
            )
    recent_messages = list((await db.scalars(
        select(AdvisorMessageModel)
        .where(
            AdvisorMessageModel.session_id == row.id,
            AdvisorMessageModel.owner_id == user.id,
        )
        .order_by(AdvisorMessageModel.created_at.desc())
        .limit(12)
    )).all())
    if resume_existing_user_message and idempotency_key:
        recent_messages = [
            message for message in recent_messages
            if not (
                message.role == "user"
                and message.idempotency_key == idempotency_key
            )
        ]
    recent_messages.reverse()
    conversation_history = recent_conversation_context(recent_messages)
    recent_candidate_evidence = recent_verified_candidate_context(recent_messages)
    previous_assistant = next(
        (message for message in reversed(recent_messages) if message.role == "assistant"), None,
    )
    previous_payload = previous_assistant.payload if previous_assistant else None
    pending_turn = resolve_pending_turn(
        context=context,
        previous_payload=previous_payload,
        content=content,
        invalidated_by_context_update=invalidated_by_context_update,
    )
    pending_question = pending_turn.pending_question
    continuing_task = pending_turn.continuing_task
    schedule_proposal_followup = is_schedule_proposal_edit_followup(
        previous_payload if isinstance(previous_payload, dict) else None,
        content,
    )
    turn_goal = pending_turn.turn_goal
    invalidated_pending_task = pending_turn.invalidated
    cancelled_pending_task = pending_turn.cancelled
    cancelled_task_type = pending_turn.cancelled_task_type
    if invalidated_pending_task or cancelled_pending_task:
        context.pop("destination_discovery_city", None)
    transport_route_requested = is_route_query_request(turn_goal)
    unsupported_booking = unsupported_booking_action(turn_goal)
    checkpoint_task_id = idempotency_key or f"{row.id}:{content}"
    intent_checkpoint_recovered = False
    deterministic_schedule_extraction = False
    intent_scope_ref = None
    prior_stage_checkpoint = None
    if idempotency_key:
        prior_stage_checkpoint = await load_turn_stage_checkpoint(
            db,
            owner_id=user.id,
            session_id=row.id,
            task_id=checkpoint_task_id,
            context_revision=int(context.get("revision", 0)),
        )
    stage_queue_checkpoint = checkpoint_writer(
        owner_id=user.id,
        session_id=row.id,
        task_id=checkpoint_task_id,
        phase="turn_stage",
        context_revision=int(context.get("revision", 0)),
    )
    if not resume_existing_user_message:
        db.add(
            AdvisorMessageModel(
                session_id=row.id,
                owner_id=user.id,
                role="user",
                content=submitted_content,
                idempotency_key=idempotency_key,
            )
        )
    if (
        pending_question
        and not invalidated_pending_task
        and not cancelled_pending_task
        and continuing_task
        and continuing_task.task_kind == "DESTINATION_RECOMMENDATION"
    ):
        return await _resume_destination_recommendation(
            db=db, row=row, user=user, maps=maps, answer=content,
            pending=continuing_task, resumed=pending_question,
            idempotency_key=idempotency_key,
        )
    try:
        if transport_route_requested or unsupported_booking:
            # Route endpoint selection has its own bounded Agent decision. Avoid
            # spending a separate extraction call on the same short route request.
            # Unsupported transactions also need no place extraction; they stop
            # before all map, route, schedule and external action stages.
            extraction, model_fallback = fallback_extract(turn_goal), False
        elif schedule_proposal_followup or should_use_deterministic_schedule_extraction(
            content=content,
            has_trip=row.trip_id is not None,
            saved_candidates=row.candidates,
            output_token_budget=turn_output_budget,
        ):
            # The user has already named saved stops and clearly asked to
            # schedule them. Keep a 128-token budget for Agent decisions/tools.
            extraction = fallback_extract(content)
            model_fallback = False
            deterministic_schedule_extraction = True
        else:
            if idempotency_key and settings.llm_api_key and settings.llm_model:
                intent_scope_ref = intent_checkpoint_scope_ref(
                    goal=turn_goal,
                    conversation_context=conversation_history,
                    personal_memories=context.get("personal_memories", []),
                    context_revision=int(context.get("revision", 0)),
                    token_budget=turn_output_budget,
                    model=settings.llm_model,
                    base_url=settings.llm_base_url,
                )
                extraction = await load_intent_checkpoint(
                    db,
                    owner_id=user.id,
                    session_id=row.id,
                    task_id=checkpoint_task_id,
                    scope_ref=intent_scope_ref,
                    goal=turn_goal,
                )
                intent_checkpoint_recovered = extraction is not None
            if intent_checkpoint_recovered:
                model_fallback = False
            else:
                extraction, model_fallback = await _extract_advisor_intent(
                    turn_goal, token_budget=turn_output_budget,
                    conversation_context=conversation_history,
                    personal_memories=context.get("personal_memories", []),
                )
                if (
                    intent_scope_ref is not None
                    and not model_fallback
                    and extraction.output_tokens > 0
                ):
                    await save_intent_checkpoint(
                        owner_id=user.id,
                        session_id=row.id,
                        task_id=checkpoint_task_id,
                        scope_ref=intent_scope_ref,
                        extraction=extraction,
                        goal=turn_goal,
                        context_revision=int(context.get("revision", 0)),
                    )
        request_decision = plan_advisor_request(
            goal=turn_goal,
            extraction=extraction,
            continuing_task=bool(continuing_task) or schedule_proposal_followup,
            continuing_task_type=(
                continuing_task.task_type if continuing_task
                else "SCHEDULE" if schedule_proposal_followup else None
            ),
            current_message=content,
            previous_payload=previous_payload if isinstance(previous_payload, dict) else None,
            unsupported_booking=bool(unsupported_booking),
            transport_route_requested=transport_route_requested,
            has_trip=row.trip_id is not None,
            current_plan_stops=(context.get("current_plan") or {}).get("stops", []),
            saved_candidates=row.candidates,
        )
        if (
            getattr(row, "mode", None) == "discover"
            and not row.trip_id and not row.destination
        ):
            explicit_city = extract_explicit_destination_city(turn_goal)
            if explicit_city:
                row.destination = explicit_city
                context = dict(row.context or {})
                context["revision"] = int(context.get("revision", 0)) + 1
                row.context = context
        requested_category = request_decision.requested_category
        requested_meal_type = request_decision.requested_meal_type
        stated_interest_queries = list(request_decision.stated_interest_queries)
        recommendation_requested = request_decision.recommendation_requested
        guide_references_by_name = (
            await load_trip_guide_references(db, row.trip_id)
            if row.trip_id and (recommendation_requested or requested_category in {"FOOD", "LODGING"})
            else {}
        )
        event_replan_type = request_decision.event_replan_type
        event_replan_handoff = request_decision.event_replan_handoff
        transport_route_requested = request_decision.transport_route_requested
        if not event_replan_handoff and (
            stated_interest_queries
            or extraction.pace_adjustment != "none"
            or any(term in content for term in ("偏好", "喜欢", "不喜欢", "不要", "希望", "不想"))
        ):
            row.preference_text = (row.preference_text + "\n" + content).strip()[-4000:]
        if should_start_destination_recommendation_from_chat(
            mode=getattr(row, "mode", None),
            trip_id=row.trip_id,
            destination=row.destination,
            recommendation_requested=recommendation_requested,
            requested_category=requested_category,
            compound_search_categories=request_decision.compound_search_categories,
        ):
            return await start_destination_recommendation_from_chat(
                db=db, row=row, user=user, maps=maps, user_goal=turn_goal,
                idempotency_key=idempotency_key,
                output_token_budget=remaining_turn_output_tokens(
                    turn_output_budget, extraction.output_tokens,
                ),
                extraction_total_tokens=extraction.token_usage,
                extraction_output_tokens=extraction.output_tokens,
                session_reader=read_session,
            )
        schedule_intent_requested = bool(not unsupported_booking and not event_replan_handoff and (
            extraction.intent == "schedule"
            or request_decision.schedule_requested
            or _is_schedule_recommendation_request(turn_goal)
            or pending_question is not None and pending_question.task_type == "SCHEDULE"
            or schedule_proposal_followup
        ))
        context, adjustments = _apply_existing_adjustments(
            row, content, extraction, conversation_context=conversation_history,
            # A stable profile preference such as "I like a relaxed pace" is
            # useful planning context, but it must not silently mutate this
            # trip's pace setting. Apply a pace change only inside an explicit
            # schedule task or while answering its clarification question.
            allow_pace_adjustment=(
                not event_replan_handoff
                and (
                    _is_schedule_recommendation_request(turn_goal)
                    or bool(continuing_task and continuing_task.task_type == "SCHEDULE")
                )
            ),
        )
        if requested_category == "FOOD" and row.trip_id and not schedule_intent_requested:
            # Keep confirmed itinerary stops and an explicit food choice made in
            # this turn; discard unrelated temporary picks from earlier searches.
            context["selected_candidate_ids"] = selected_ids_after_food_search(
                row.candidates, explicitly_selected_candidate_ids,
            )
            context["revision"] = int(context.get("revision", 0)) + 1
        found = []
        preselected_candidate_ids = []
        searched_requests = 0
        search_failures = []
        weather_failures = []
        preparation_requested = (
            is_preparation_request(turn_goal)
            or request_decision.preparation_requested
        )
        weather_requested = any(
            term in turn_goal for term in ("天气", "下雨", "带伞", "穿什么", "穿衣", "天气预报")
        ) or request_decision.weather_requested or bool(
            preparation_requested and row.destination and row.start_date and row.end_date
        )
        unsupported_route_mode = requests_unsupported_route_mode(turn_goal)
        if continuing_task and accepts_driving_route_confirmation(
            continuing_task.question, content,
        ):
            # Keep route-mode inference grounded in the user's original request.
            # The pending question may mention other supported modes as examples.
            turn_goal = continuing_task.task
            unsupported_route_mode = False
        search_runtime = advisor_tool_runtime(
            maps,
            weather=weather if weather_requested else None,
            city=row.destination,
            start_date=row.start_date,
            end_date=row.end_date,
        )
        search_runtime.token_budget = max(
            0, remaining_turn_output_tokens(
                turn_output_budget, extraction.output_tokens, search_runtime,
            )
        )
        request_plan = list(request_decision.search_requests)
        if unsupported_booking:
            weather_requested = False
            preparation_requested = False
        turn_plan = build_advisor_turn_plan(
            goal=turn_goal,
            planning_category=requested_category,
            trip_available=row.trip_id is not None,
            destination_available=bool(row.destination),
            dates_available=bool(row.start_date and row.end_date),
            schedule_requested=bool(not invalidated_pending_task and schedule_intent_requested),
            weather_requested=weather_requested,
            preparation_requested=preparation_requested,
            discovery_requested=recommendation_requested,
            search_requested=bool(request_plan),
            route_requested=transport_route_requested,
            available_search_tools=sorted(search_runtime.tools),
            known_facts=[
                fact for fact, present in (
                    ("destination", bool(row.destination)),
                    ("trip_dates", bool(row.start_date and row.end_date)),
                    ("saved_places", bool(row.candidates)),
                    ("confirmed_stops", bool((context.get("current_plan") or {}).get("stops"))),
                ) if present
            ],
            missing_facts=[
                fact for fact, missing in (
                    ("destination", not bool(row.destination)),
                    ("trip_dates", not bool(row.start_date and row.end_date)),
                    ("saved_places", not bool(row.candidates)),
                ) if missing
            ],
        )
        turn_executor = AdvisorTurnExecutor(
            turn_plan,
            checkpoint=stage_queue_checkpoint,
            initial_transition=(prior_stage_checkpoint or {}).get("next_transition", 1) - 1,
            recovery=prior_stage_checkpoint,
        )
        inferred_task_type = turn_plan.primary_task_type
        search_stage = turn_plan.stage("SEARCH_WEATHER")
        schedule_stage = turn_plan.stage("SCHEDULE")
        route_stage = turn_plan.stage("ROUTE")
        schedule_recommendation_requested = schedule_stage is not None
        search_loop_result = None
        search_loop_question = None
        transport_result = None
        transport_trace = None
        reused_weather_evidence = "NOT_REQUESTED"
        reused_route_evidence = "NOT_REQUESTED"
        stage_execution_order = []
        route_stage_executed = False

        async def execute_route_stage() -> dict | None:
            nonlocal transport_result, transport_trace, search_loop_question, route_stage_executed
            nonlocal reused_route_evidence
            if transport_route_requested and not row.trip_id:
                search_loop_question = "请先在顶部选择或创建一段旅行；路线查询会限定在该旅行已保存并核验过的地点之间。"
                transport_trace = {
                    "decision_source": "RULE",
                    "decision_summary": "当前是自由探索会话，路线查询需要先选择一段旅行",
                    "status": "NEEDS_USER_INPUT",
                    "transitions": [],
                }
            elif route_stage is not None:
                if unsupported_route_mode:
                    search_loop_question = unsupported_route_mode_question()
                    transport_trace = {
                        "decision_source": "RULE",
                        "decision_summary": "请求的出行方式尚未接入，先向用户确认是否接受驾车路线",
                        "status": "NEEDS_USER_INPUT",
                        "transitions": [],
                    }
                else:
                    cached_route = None
                    route_observation_callback = None
                    route_decision_loader = None
                    route_decision_saver = None
                    if idempotency_key:
                        route_context_revision = int(context.get("revision", 0))
                        route_source = map_evidence_source(maps)
                        route_candidate_ids = [
                            str(item.get("id")) for item in row.candidates[:40]
                            if item.get("id")
                        ]
                        route_scope = direct_route_scope_ref(
                            turn_goal, route_candidate_ids,
                            route_context_revision, route_source,
                        )
                        recovered_route = await load_direct_route_checkpoint(
                            db, owner_id=user.id, session_id=row.id,
                            task_id=checkpoint_task_id, scope_ref=route_scope,
                            expected_source=route_source,
                        )
                        if recovered_route is not None:
                            cached_route = recovered_route
                        route_observation_callback = direct_route_checkpoint_writer(
                            owner_id=user.id, session_id=row.id,
                            task_id=checkpoint_task_id, scope_ref=route_scope,
                            expected_source=route_source,
                        )

                        async def route_decision_loader(
                            *, observation_ref, decision_number, approved_actions,
                        ):
                            return await load_readonly_tool_decision(
                                db, owner_id=user.id, session_id=row.id,
                                task_id=checkpoint_task_id,
                                context_revision=route_context_revision,
                                observation_ref=observation_ref,
                                decision_number=decision_number,
                                approved_actions=approved_actions,
                            )

                        async def route_decision_saver(
                            *, observation_ref, decision_number, approved_actions, tool, action_ref,
                        ):
                            writer = readonly_tool_decision_writer(
                                owner_id=user.id, session_id=row.id,
                                task_id=checkpoint_task_id,
                                context_revision=route_context_revision,
                                observation_ref=observation_ref,
                                decision_number=decision_number,
                                approved_actions=approved_actions,
                            )
                            await writer({"tool": tool, "action_ref": action_ref})
                    if cached_route is None and continuing_task and not invalidated_pending_task:
                        pending_revision = (
                            continuing_task.context_revision
                            if continuing_task.context_revision is not None
                            else int(context.get("revision", 0))
                        )
                        reused_route_evidence = "NO_MATCHING_VERIFIED_EVIDENCE"
                        if pending_evidence_scope_matches(
                            previous_payload if isinstance(previous_payload, dict) else None,
                            task_id=continuing_task.task_id,
                            context_revision=pending_revision,
                        ):
                            reused_route_evidence = "SCOPE_MATCH_BUT_ROUTE_REJECTED"
                        cached_route = reusable_route_from_pending_turn(
                            previous_payload if isinstance(previous_payload, dict) else None,
                            task_id=continuing_task.task_id,
                            context_revision=pending_revision,
                            user_goal=turn_goal,
                            places=row.candidates,
                            source=map_evidence_source(maps),
                        )
                        if cached_route is not None:
                            reused_route_evidence = "PENDING_TASK_REUSED"
                    elif cached_route is not None:
                        reused_route_evidence = "CHECKPOINT_REUSED"
                    transport_result = await run_advisor_route_query(
                        maps=maps,
                        places=row.candidates,
                        user_goal=turn_goal,
                        api_key=settings.llm_api_key,
                        model=settings.llm_model,
                        base_url=settings.llm_base_url,
                        token_budget=remaining_turn_output_tokens(
                            turn_output_budget,
                            extraction.output_tokens,
                            search_runtime,
                            schedule_runtime,
                        ),
                        task_context=route_stage.task_context,
                        cached_route=cached_route,
                        route_observation_callback=route_observation_callback,
                        load_decision_checkpoint=route_decision_loader,
                        save_decision_checkpoint=route_decision_saver,
                        checkpoint=checkpoint_writer(
                            owner_id=user.id, session_id=row.id, task_id=checkpoint_task_id,
                            phase="route_query",
                            context_revision=int(context.get("revision", 0)),
                        ),
                    )
                    transport_trace = transport_result.trace
                    if transport_result.loop.pending_question:
                        search_loop_question = transport_result.loop.pending_question
            route_stage_executed = True
            stage_execution_order.append("ROUTE")
            return transport_trace

        async def run_route_stage_for_executor() -> dict | None:
            return await execute_route_stage()

        # One executor will own the complete stage graph for this request.
        turn_orchestration = {
            "order": [stage.name for stage in turn_plan.stages],
            "decision_source": "RULE",
            "decision_summary": "按本轮任务依赖执行已授权阶段",
            "fallback_reason": None,
        }
        search_agent_started_at = perf_counter()
        turn_stage_runners = {}
        if route_stage is not None:
            turn_stage_runners["ROUTE"] = run_route_stage_for_executor
        if search_stage is not None:
            weather_checkpoint = None
            cached_weather = None
            cached_searches = {}
            search_checkpoint = None
            map_source = map_evidence_source(maps)
            context_revision = int(context.get("revision", 0))
            if idempotency_key:
                for request in request_plan:
                    keyword = request["keyword"]
                    scope_ref = search_scope_ref(
                        row.destination or "", keyword, context_revision, map_source,
                    )
                    recovered = await load_search_evidence_checkpoint(
                        db, owner_id=user.id, session_id=row.id,
                        task_id=checkpoint_task_id, scope_ref=scope_ref,
                        expected_source=map_source,
                    )
                    if recovered is not None:
                        cached_searches[keyword.casefold()] = recovered

                async def save_search_evidence(request, output):
                    scope = search_scope_ref(
                        row.destination or "", request["keyword"],
                        context_revision, map_source,
                    )
                    writer = search_evidence_checkpoint_writer(
                        owner_id=user.id, session_id=row.id, task_id=checkpoint_task_id,
                        scope_ref=scope, expected_source=map_source,
                    )
                    await writer(output)

                search_checkpoint = save_search_evidence

                async def recover_search_decision(
                    *, observation_ref, decision_number, approved_actions,
                ):
                    return await load_readonly_tool_decision(
                        db, owner_id=user.id, session_id=row.id,
                        task_id=checkpoint_task_id,
                        context_revision=context_revision,
                        observation_ref=observation_ref,
                        decision_number=decision_number,
                        approved_actions=approved_actions,
                    )

                async def save_search_decision(
                    *, observation_ref, decision_number, approved_actions, tool, action_ref,
                ):
                    writer = readonly_tool_decision_writer(
                        owner_id=user.id, session_id=row.id,
                        task_id=checkpoint_task_id,
                        context_revision=context_revision,
                        observation_ref=observation_ref,
                        decision_number=decision_number,
                        approved_actions=approved_actions,
                    )
                    await writer({"tool": tool, "action_ref": action_ref})
            else:
                recover_search_decision = None
                save_search_decision = None
            source = getattr(weather, "source", "Open-Meteo")
            if (
                idempotency_key and weather_requested and row.destination
                and row.start_date and row.end_date
            ):
                revision = int(context.get("revision", 0))
                scope_ref = weather_scope_ref(
                    row.destination, row.start_date.isoformat(), row.end_date.isoformat(),
                    revision, source,
                )
                cached_weather = await load_weather_evidence_checkpoint(
                    db, owner_id=user.id, session_id=row.id,
                    task_id=checkpoint_task_id, scope_ref=scope_ref,
                    city=row.destination, expected_source=source,
                )
                weather_checkpoint = weather_evidence_checkpoint_writer(
                    owner_id=user.id, session_id=row.id, task_id=checkpoint_task_id,
                    scope_ref=scope_ref, expected_source=source,
                )
            if weather_requested:
                reused_weather_evidence = "NO_CONTINUING_TASK"
                if cached_weather is not None:
                    reused_weather_evidence = "CHECKPOINT_REUSED"
                elif continuing_task and not invalidated_pending_task:
                    pending_revision = (
                        continuing_task.context_revision
                        if continuing_task.context_revision is not None
                        else int(context.get("revision", 0))
                    )
                    reused_weather_evidence = "NO_MATCHING_VERIFIED_EVIDENCE"
                    if pending_evidence_scope_matches(
                        previous_payload if isinstance(previous_payload, dict) else None,
                        task_id=continuing_task.task_id,
                        context_revision=pending_revision,
                    ):
                        reused_weather_evidence = "SCOPE_MATCH_BUT_WEATHER_REJECTED"
                    cached_weather = reusable_weather_from_pending_turn(
                        previous_payload if isinstance(previous_payload, dict) else None,
                        task_id=continuing_task.task_id,
                        context_revision=pending_revision,
                        city=row.destination,
                        start_date=row.start_date,
                        end_date=row.end_date,
                        source=source,
                    )
                    if cached_weather is not None:
                        reused_weather_evidence = "PENDING_TASK_REUSED"
            async def execute_search_weather_stage() -> dict | None:
                nonlocal search_loop_result
                search_loop_result = await run_advisor_search_loop(
                    runtime=search_runtime,
                    requests=request_plan,
                    city=row.destination,
                    user_goal=turn_goal,
                    task_type=search_stage.task_context.task_type,
                    task_context=search_stage.task_context,
                    weather_requested=weather_requested,
                    weather_source=getattr(weather, "source", "Open-Meteo"),
                    cached_weather=cached_weather,
                    weather_observation_callback=weather_checkpoint,
                    cached_searches=cached_searches,
                    search_observation_callback=search_checkpoint,
                    load_decision_checkpoint=recover_search_decision,
                    save_decision_checkpoint=save_search_decision,
                    api_key=settings.llm_api_key,
                    model=settings.llm_model,
                    base_url=settings.llm_base_url,
                    token_budget=remaining_turn_output_tokens(
                        turn_output_budget,
                        extraction.output_tokens,
                        search_runtime,
                        transport_result.runtime if transport_result else None,
                    ),
                    checkpoint=checkpoint_writer(
                        owner_id=user.id, session_id=row.id, task_id=checkpoint_task_id,
                        phase="search_weather",
                        context_revision=int(context.get("revision", 0)),
                    ),
                )
                return search_loop_result.trace

            turn_stage_runners["SEARCH_WEATHER"] = execute_search_weather_stage
        weather_result = None
        schedule_recommendation_error = None
        schedule_agent_trace = None
        schedule_agent_started_at = perf_counter()
        schedule_runtime = None
        schedule_suggestions = []
        schedule_breaks = []
        proposed_warnings = []
        schedule_candidate_count = 0
        async def integrate_turn_stage_result(stage_name, _trace):
            nonlocal search_loop_question, weather_result, searched_requests
            nonlocal search_failures, weather_failures
            if stage_name != "SEARCH_WEATHER":
                return
            searched_requests = sum(
                call["tool"] == "search_places" for call in search_runtime.calls
            )
            search_failures = [
                call["error_code"] for call in search_runtime.calls
                if call["tool"] == "search_places" and call["status"] != "SUCCEEDED"
            ]
            weather_failures = [
                call["error_code"] for call in search_runtime.calls
                if call["tool"] == "get_trip_weather" and call["status"] != "SUCCEEDED"
            ]
            pending_questions = [
                question for question in (
                    search_loop_question,
                    search_loop_result.pending_question if search_loop_result else None,
                )
                if isinstance(question, str) and question.strip()
            ]
            if weather_requested and (not row.start_date or not row.end_date):
                pending_questions.append(
                    "要查询这段旅行的天气预报，请先在旅行信息中补充开始和结束日期；"
                    "我不会替你假设日期。"
                )
            search_loop_question = "\n".join(dict.fromkeys(pending_questions)) or None
            weather_result = search_loop_result.weather if search_loop_result else None
            if weather_result:
                weather_result = {
                    **weather_result,
                    "days": [
                        {**day, "advice": daily_weather_advice(day)}
                        for day in weather_result.get("days", [])
                    ],
                }
            for request, search_result in (
                search_loop_result.results if search_loop_result else []
            ):
                confidence = request.get("confidence", 0.55)
                suggestion = request.get("suggestion")
                select_by_default = bool(request.get("select_by_default"))
                for place in search_result["places"][:5]:
                    provider_category = str(place["category"] or "")
                    planning_category = _candidate_planning_category(
                        provider_category, place["name"],
                    )
                    if requested_category and planning_category != requested_category:
                        continue
                    candidate = {
                        "id": provider_candidate_id(
                            session_id=row.id,
                            provider=place["provider"],
                            provider_id=place["provider_id"],
                        ),
                        "provider": place["provider"],
                        "provider_id": place["provider_id"],
                        "name": place["name"],
                        "address": place["address"],
                        "category": place["category"],
                        "planning_category": planning_category,
                        "meal_type": requested_meal_type if planning_category == "FOOD" else None,
                        "latitude": place["latitude"],
                        "longitude": place["longitude"],
                        "city_code": place.get("city_code"),
                        "duration_min": place["duration_min"],
                        "opening_hours": place["opening_hours"],
                        "confidence": confidence,
                        "suggestion": suggestion,
                        "popularity_score": place.get("popularity_score"),
                        "verification_status": search_result["status"],
                        "verified_at": search_result["retrieved_at"],
                        "guide_references": guide_references_for_place(
                            place["name"], guide_references_by_name,
                        ),
                    }
                    existing_candidate = next((
                        item for item in row.candidates
                        if item["provider"] == candidate["provider"]
                        and item["provider_id"] == candidate["provider_id"]
                    ), None)
                    if existing_candidate:
                        if not existing_candidate.get("planning_category"):
                            existing_candidate["planning_category"] = planning_category
                        if requested_meal_type and planning_category == "FOOD":
                            existing_candidate["meal_type"] = requested_meal_type
                        existing_candidate["guide_references"] = list({
                            item["source_id"]: item
                            for item in [
                                *existing_candidate.get("guide_references", []),
                                *candidate.get("guide_references", []),
                            ]
                        }.values())[:3]
                        found.append(existing_candidate)
                    else:
                        row.candidates = [*row.candidates, candidate]
                        found.append(candidate)
                        if select_by_default:
                            preselected_candidate_ids.append(candidate["id"])
                        if row.trip_id and select_by_default:
                            context["selected_candidate_ids"] = sorted({
                                *context.get("selected_candidate_ids", []),
                                candidate["id"],
                            })
                            context["revision"] = int(context.get("revision", 0)) + 1
            if search_loop_result is not None:
                stage_execution_order.append("SEARCH_WEATHER")
            if recommendation_requested and found:
                def sort_key(item):
                    return (
                        item.get("popularity_score") is not None,
                        item.get("popularity_score") or 0,
                    )
                found.sort(key=sort_key, reverse=True)
                row.candidates = sorted(row.candidates, key=sort_key, reverse=True)

        async def select_next_turn_stage(eligible_stages, statuses, _traces):
            """Choose from ready stages using the latest bounded turn evidence."""
            completed_stage_observations = {}
            if statuses.get("SEARCH_WEATHER"):
                completed_stage_observations["search_weather"] = {
                    "candidate_count": len(found),
                    "search_failures": len(search_failures),
                    "weather_available": bool(weather_result and weather_result.get("days")),
                    "weather_failures": len(weather_failures),
                }
            if statuses.get("ROUTE"):
                completed_stage_observations["route"] = {
                    "route_available": bool(transport_result and transport_result.route),
                    "route_failures": int(
                        statuses.get("ROUTE") != "SUCCEEDED"
                    ),
                }
            if statuses.get("SCHEDULE"):
                completed_stage_observations["schedule"] = {
                    "candidate_count": schedule_candidate_count,
                    "warning_count": len(proposed_warnings),
                }
            stage_decision_observation = build_stage_selection_observation(
                user_goal=turn_goal,
                destination_available=bool(row.destination),
                dates_available=bool(row.start_date and row.end_date),
                saved_place_count=len(row.candidates),
                confirmed_stop_count=len(
                    (context.get("current_plan") or {}).get("stops", [])
                ),
                completed_stage_observations=completed_stage_observations,
                plan=turn_plan,
            )
            return await resolve_turn_stage_order(
                db,
                owner_id=user.id,
                session_id=row.id,
                task_id=checkpoint_task_id,
                context_revision=int(context.get("revision", 0)),
                stages=eligible_stages,
                observation=stage_decision_observation,
                retryable=bool(idempotency_key),
                runtime=search_runtime,
                api_key=settings.llm_api_key,
                model=settings.llm_model,
                base_url=settings.llm_base_url,
                token_budget=remaining_turn_output_tokens(
                    turn_output_budget, extraction.output_tokens, search_runtime,
                    transport_result.runtime if transport_result else None,
                ),
                selector=choose_stage_order,
                resolver=resolve_stage_order,
            )
        async def execute_schedule_stage() -> dict | None:
            nonlocal schedule_recommendation_error, schedule_agent_trace
            nonlocal schedule_runtime, schedule_suggestions, schedule_breaks, proposed_warnings
            nonlocal schedule_candidate_count, preselected_candidate_ids
            nonlocal schedule_agent_started_at
            schedule_agent_started_at = perf_counter()
            schedule_target = None
            if len(extraction.places) == 1:
                mention_name = _normalized_place_name(extraction.places[0].name)
                exact = [
                    candidate for candidate in found
                    if _normalized_place_name(candidate["name"]) == mention_name
                ]
                if len(exact) == 1:
                    schedule_target = exact[0]
            elif len(found) == 1:
                schedule_target = found[0]
            if schedule_target is not None:
                explicit_day, explicit_time = _explicit_schedule_hint(
                    content, row.start_date, row.end_date,
                )
                if explicit_day:
                    schedule_target["explicit_day"] = explicit_day
                if explicit_time:
                    schedule_target["explicit_start_time"] = explicit_time
            route_evidence_loader = None
            route_evidence_saver = None
            schedule_decision_loader = None
            schedule_decision_saver = None
            if idempotency_key:
                async def route_evidence_loader(**scope):
                    return await load_route_evidence_checkpoint(
                        db,
                        owner_id=user.id,
                        session_id=row.id,
                        task_id=checkpoint_task_id,
                        **scope,
                    )

                route_evidence_saver = route_evidence_checkpoint_writer(
                    owner_id=user.id,
                    session_id=row.id,
                    task_id=checkpoint_task_id,
                )
                schedule_decision_loader, schedule_decision_saver = readonly_tool_decision_callbacks(
                    db,
                    owner_id=user.id,
                    session_id=row.id,
                    task_id=checkpoint_task_id,
                    context_revision=int(context.get("revision", 0)),
                )
            recommendation = await propose_existing_trip_schedule(
                context=context,
                candidates=row.candidates,
                user_goal=turn_goal,
                destination=row.destination,
                start_date=row.start_date,
                end_date=row.end_date,
                pace_level=row.pace_level,
                maps=maps,
                api_key=settings.llm_api_key,
                model=settings.llm_model,
                base_url=settings.llm_base_url,
                token_budget=remaining_turn_output_tokens(
                    turn_output_budget,
                    extraction.output_tokens,
                    search_runtime,
                    schedule_runtime,
                    transport_result.runtime if transport_result else None,
                ),
                task_context=schedule_stage.task_context if schedule_stage else None,
                checkpoint=checkpoint_writer(
                    owner_id=user.id, session_id=row.id, task_id=checkpoint_task_id,
                    phase="schedule",
                    context_revision=int(context.get("revision", 0)),
                ),
                route_evidence_loader=route_evidence_loader,
                route_evidence_saver=route_evidence_saver,
                load_decision_checkpoint=schedule_decision_loader,
                save_decision_checkpoint=schedule_decision_saver,
            )
            schedule_suggestions = recommendation.suggestions
            schedule_breaks = recommendation.reserved_breaks
            schedule_recommendation_error = recommendation.error
            schedule_agent_trace = recommendation.trace
            proposed_warnings = recommendation.warnings
            schedule_runtime = recommendation.runtime
            schedule_candidate_count = recommendation.candidate_count
            if recommendation.selected_candidate_ids:
                preselected_candidate_ids = recommendation.selected_candidate_ids
            stage_execution_order.append("SCHEDULE")
            return schedule_agent_trace

        async def execute_route_stage_for_queue() -> dict | None:
            if not route_stage_executed:
                await execute_route_stage()
            return transport_trace

        if schedule_recommendation_requested and schedule_stage is not None:
            turn_stage_runners["SCHEDULE"] = execute_schedule_stage
        planned_stage_names = {stage.name for stage in turn_plan.stages}
        current_decision_start = len(turn_executor.selection_history)
        await turn_executor.execute_adaptive(
            requested_stages=planned_stage_names,
            runners=turn_stage_runners,
            selector=select_next_turn_stage,
            after_stage=integrate_turn_stage_result,
        )
        current_decisions = turn_executor.selection_history[current_decision_start:]
        if current_decisions:
            turn_orchestration = current_decisions[0]
        compound_candidates = list(found)
        if not compound_candidates and continuing_task and requested_category:
            compound_candidates = [
                candidate for candidate in row.candidates
                if candidate.get("planning_category") == requested_category
                and not candidate.get("in_current_plan")
                and candidate.get("verification_status") in {"VERIFIED", "SIMULATED"}
                and (
                    requested_meal_type is None
                    or candidate.get("meal_type") == requested_meal_type
                )
            ][:8]
            # Trip snapshot refresh may rebuild candidates from persisted plan and
            # wishlist rows, while search-only results remain in the previous
            # assistant turn. Reuse that bounded evidence for the active
            # multi-turn task instead of silently dropping its recommendations.
            if not compound_candidates and isinstance(previous_payload, dict):
                previous_category = previous_payload.get("planning_category")
                previous_meal_type = previous_payload.get("meal_type")
                previous_verified = previous_payload.get("verified_candidates")
                if (
                    previous_category == requested_category
                    and (requested_meal_type is None or previous_meal_type == requested_meal_type)
                    and isinstance(previous_verified, list)
                ):
                    compound_candidates = [
                        candidate for candidate in previous_verified
                        if isinstance(candidate, dict)
                        and candidate.get("verification_status") in {"VERIFIED", "SIMULATED"}
                        and isinstance(candidate.get("name"), str)
                    ][:8]
        if search_loop_question and transport_result and transport_result.route:
            reply = f"{_transport_route_summary(transport_result.route)} 另外，{search_loop_question}"
        elif search_loop_question:
            reply = search_loop_question
            if compound_candidates and requested_category == "FOOD":
                meal_label = {
                    "BREAKFAST": "早餐", "LUNCH": "午餐", "DINNER": "晚餐",
                    "SNACK": "小吃/加餐",
                }.get(requested_meal_type, "用餐")
                reply += (
                    f"\n同时已按{meal_label}需求核验到餐饮候选："
                    f"{'、'.join(item['name'] for item in compound_candidates)}。"
                )
            elif compound_candidates and requested_category == "LODGING":
                reply += (
                    f"\n同时已按住宿需求核验到候选："
                    f"{'、'.join(item['name'] for item in compound_candidates)}。"
                )
        elif weather_requested and weather_result is not None and not found and not schedule_suggestions:
            if weather_result.get("days"):
                daily = "；".join(
                    _weather_day_summary(day)
                    for day in weather_result["days"]
                )
                tips = "；".join(
                    dict.fromkeys(
                        item["text"]
                        for day in weather_result["days"]
                        for item in day.get("advice", [])
                        if item.get("text")
                    )
                )
                route_summary = (
                    _transport_route_summary(transport_result.route)
                    if transport_result and transport_result.route
                    else ""
                )
                reply = f"{route_summary}\n\n" if route_summary else ""
                reply += (
                    f"{row.destination} 行程天气参考：{daily}。{tips}"
                    f"数据来自 {weather_result.get('source')}，更新时间 {weather_result.get('fetched_at')}。"
                )
                if preparation_requested:
                    reply += "通用随身清单：身份证件、手机与充电器、常用药、舒适鞋和个人洗漱用品；请按上面的逐日天气建议增减衣物或雨具。"
                if weather_result.get("message"):
                    reply += f"{weather_result['message']}"
            else:
                reply = weather_result.get("message") or "当前日期没有可用的天气预报；请在出发前查看临近预报。"
                reply += f" 数据来源：{weather_result.get('source')}。"
        elif weather_requested and not row.start_date and not found and not schedule_suggestions:
            reply = "要查询天气预报，请先补充旅行日期；我不会替你假设日期。"
        elif weather_requested and weather_failures and not found and not schedule_suggestions:
            reply = "天气服务暂时不可用，本轮没有生成天气结论。你可以稍后重试；出行前请查看临近预报。"
            if preparation_requested:
                reply += "通用随身清单可先准备身份证件、手机与充电器、常用药、舒适鞋和个人洗漱用品；衣物与雨具请等天气服务恢复后再按预报调整。"
        elif preparation_requested and not weather_requested and not found and not schedule_suggestions:
            reply = (
                "通用随身清单可先准备身份证件、手机与充电器、常用药、舒适鞋和个人洗漱用品。"
                "如果告诉我目的地与旅行日期，我可以再结合真实天气提醒穿衣、带伞或准备室内备选；我不会假设日期。"
            )
        elif schedule_suggestions:
            version = (context.get("current_plan") or {}).get("version", "当前")
            route_note = (
                "已核验的地图交通时间"
                if schedule_agent_trace
                and schedule_agent_trace.get("route_evidence") in {"VERIFIED", "SIMULATED"}
                else "本地距离估算（路线尚未核验）"
            )
            reply = f"已根据当前计划 v{version} 和{route_note}生成调整预览。检查后点击“确认并应用到行程”即可更新正式计划。"
            if weather_result and weather_result.get("days"):
                reply += " 天气参考：" + "；".join(
                    _weather_day_summary(day) for day in weather_result["days"]
                )
                reply += f"。来源：{weather_result.get('source')}，更新时间：{weather_result.get('fetched_at')}。"
            if compound_candidates and requested_category == "FOOD":
                meal_label = {
                    "BREAKFAST": "早餐", "LUNCH": "午餐", "DINNER": "晚餐",
                    "SNACK": "小吃/加餐",
                }.get(requested_meal_type, "用餐")
                reply += (
                    f"\n另外，按{meal_label}需求核验到餐饮候选："
                    f"{'、'.join(item['name'] for item in compound_candidates[:8])}。"
                    "选择候选并确认时间后，可将它加入行程调整预览。"
                )
            elif compound_candidates and requested_category == "LODGING":
                reply += (
                    f"\n另外，按住宿需求核验到候选："
                    f"{'、'.join(item['name'] for item in compound_candidates[:8])}。"
                    "选择候选后可另行加入旅行草案。"
                )
        elif transport_result and transport_result.route:
            reply = _transport_route_summary(transport_result.route)
            if weather_requested or preparation_requested:
                reply = _append_weather_context(
                    reply,
                    weather_result=weather_result,
                    weather_requested=weather_requested,
                    preparation_requested=preparation_requested,
                    weather_failures=weather_failures,
                )
        elif transport_route_requested and row.trip_id:
            reply = (
                "暂时无法核验这条路线。请确认地点已加入当前旅行，或稍后重试地图服务；"
                "我没有用直线距离冒充驾车时间。"
            )
        elif invalidated_pending_task:
            reply = "旅行信息在我追问期间发生了变化，刚才的待办规划已作废，避免基于旧行程生成建议。请重新描述你希望调整的安排。"
        elif cancelled_pending_task:
            reply = cancelled_task_reply(cancelled_task_type)
        elif event_replan_handoff:
            event_labels = {
                "USER_FATIGUE": "当前疲劳",
                "WEATHER_CHANGE": "天气变化",
                "TIME_DELAY": "途中延误",
                "POI_CANCELLED": "地点临时无法前往",
                "RESERVATION_CHANGE": "预约时间变化",
            }
            change_label = event_labels.get(event_replan_type or "", "行程变化")
            reply = (
                f"我识别到你想处理{change_label}。为避免普通排程器重排整段旅行，"
                "我会转到途中调整预览：请核对生效时间及相关地点/延误/预约信息，再查看受影响安排和变更差异；"
                "固定预约会保留，只有你确认后才会保存新计划。"
            )
        elif unsupported_booking:
            reply = unsupported_booking[1]
        elif schedule_recommendation_requested:
            reply = (
                schedule_recommendation_error
                or "我读取了当前旅行，但暂时无法生成时间建议；请检查日期和地图路线服务后重试。"
            )
        elif found:
            names = "、".join(c["name"] for c in found[:8])
            if requested_category == "FOOD":
                meal_label = {"BREAKFAST": "早餐", "LUNCH": "午餐", "DINNER": "晚餐", "SNACK": "小吃/加餐"}.get(requested_meal_type, "用餐")
                reply = f"我按{meal_label}需求筛选了餐饮地点：{names}。这里只展示餐饮候选；选择后请确认用餐日期和时间，再生成待确认安排。"
            elif requested_category == "LODGING":
                reply = f"我按住宿需求筛选了酒店或民宿候选：{names}。这里只展示住宿地点；选择后可加入旅行草案。"
            else:
                reply = f"地图服务核对到这些候选：{names}。请先选择想去的地点，并为每个地点确认日期和开始时间。"
            if recommendation_requested and not requested_category:
                reply += "候选按地图服务返回顺序整理；只有地图提供评分时才会按评分辅助排序，不能把它当作全网热度。你更偏爱自然风景、历史文化、美食还是亲子活动？"
            if weather_requested or preparation_requested:
                reply = _append_weather_context(
                    reply,
                    weather_result=weather_result,
                    weather_requested=weather_requested,
                    preparation_requested=preparation_requested,
                    weather_failures=weather_failures,
                )
            if search_failures:
                reply += " 部分地图查询暂时失败，以上候选仅来自成功的查询。"
        elif not row.destination and row.mode == "discover":
            has_preferences = bool(row.preference_text.strip())
            planning_inputs = (row.context or {}).get("planning_inputs", {})
            missing_city_compare = []
            if not str(planning_inputs.get("origin", "")).strip():
                missing_city_compare.append("出发地")
            if not row.start_date or not row.end_date:
                missing_city_compare.append("旅行日期")
            if not has_preferences:
                reply = "可以从一点偏好开始，不用一次想完整。你更想看自然风景、吃本地美食、逛历史街区，还是去海边放松？也可以直接说预算或同行人。"
            else:
                reply = "我先记下了你的想法。"
                if missing_city_compare:
                    reply += f"要根据地图证据比较城市，还需要{'和'.join(missing_city_compare)}。"
                reply += "你可以补充这些条件，或者先告诉我想考虑的城市，我就按偏好搜索具体地点。"
        elif not row.destination:
            reply = "我已记下你的想法。先告诉我想去的城市或区域；如果还没确定，可以说说偏好的风景、吃住预算和旅行天数，我会先问最关键的一项，再一起缩小范围。"
        elif searched_requests and search_failures:
            reply = "地图服务暂时不可用，这轮未能核验地点。你的偏好已保留，可以稍后重试。"
        elif searched_requests:
            reply = "地图服务没有找到匹配候选。可以补充更具体的地点名称或偏好关键词。"
        elif adjustments:
            reply = "已记录你对当前计划的调整。先检查行程调整预览；确认后会更新正式计划。"
        elif extraction.intent == "general":
            reply = "我可以结合当前旅行安排回答一般问题。你更关心天气准备、交通、预算、用餐住宿，还是每天的行程节奏？"
        else:
            model_note = (
                "已使用配置的模型抽取地点名称"
                if settings.llm_api_key and settings.llm_model
                else "当前未配置模型，使用本地规则识别明确提到的地点"
            )
            reply = f"{model_note}。请告诉我具体地点名称；偏好会保存在会话中，但地点需经地图核验后才能进入草案。"
        if transport_route_requested and (weather_requested or preparation_requested):
            reply = _append_route_outcome(
                reply,
                route=transport_result.route if transport_result else None,
                requested=True,
                failed=bool(
                    transport_result
                    and any(
                        item.get("tool") == "get_trip_route" and item.get("status") == "FAILED"
                        for item in transport_result.loop.transitions
                    )
                ),
            )
        if adjustments:
            reply += " " + "；".join(adjustments) + "。"
        if (
            row.trip_id
            and row.context
            and row.context.get("confirmed_memories")
            and not adjustments
        ):
            reply += (
                f" 会话已载入 {len(row.context['confirmed_memories'])} 条已确认旅行记忆供你查看。"
            )
        reply_candidates = grounded_reply_candidates(
            current_candidates=found,
            prior_candidates=recent_candidate_evidence,
            searched_requests=searched_requests,
            search_failures=search_failures,
            requested_category=requested_category,
            requested_meal_type=requested_meal_type,
        )
        reply_fallback_reason = None
        composed_result = None
        if not event_replan_handoff and can_compose_grounded_reply(
            schedule_recommendation_requested=schedule_recommendation_requested,
            cancelled_pending_task=cancelled_pending_task,
            invalidated_pending_task=invalidated_pending_task,
            search_loop_question=search_loop_question,
            weather_result=weather_result,
            transport_result=transport_result,
            transport_route_requested=transport_route_requested,
            requested_category=requested_category,
            preparation_requested=preparation_requested,
            verified_candidates_available=bool(reply_candidates),
            unsupported_booking_requested=unsupported_booking is not None,
        ):
            composed_result = await compose_advisor_reply_with_diagnostics(
                {
                    "current_message": turn_goal,
                    "recent_conversation": conversation_history,
                    "destination": row.destination or None,
                    "dates": {
                        "start": row.start_date.isoformat() if row.start_date else None,
                        "end": row.end_date.isoformat() if row.end_date else None,
                    },
                    "budget": str(row.budget) if row.budget is not None else None,
                    "pace_level": row.pace_level,
                    "preferences": row.preference_text[-1200:],
                    "confirmed_personal_memories": (row.context or {}).get("personal_memories", []),
                    "memory_suggestions_enabled": memory_suggestions_enabled,
                    "planning_inputs": (row.context or {}).get("planning_inputs", {}),
                    "current_plan": context.get("current_plan"),
                    "selected_places": [
                        {"name": item.get("name"), "planning_category": item.get("planning_category")}
                        for item in row.candidates
                        if item.get("id") in set(context.get("selected_candidate_ids", []))
                    ],
                    "known_trip_places": [
                        {"name": item.get("name")}
                        for item in row.candidates[:20]
                        if isinstance(item.get("name"), str)
                    ],
                    "trip_completeness_audit": trip_completeness_audit(row, context),
                    "trip_mode": row.mode,
                    "destination_selected_this_turn": bool(
                        row.mode == "discover"
                        and row.destination
                        and any(
                            message.role == "assistant"
                            and isinstance(message.payload, dict)
                            and message.payload.get("event") == "DESTINATION_RECOMMENDATIONS"
                            for message in recent_messages[-3:]
                        )
                        and row.destination in content
                    ),
                    "has_existing_trip": row.trip_id is not None,
                    "planning_stage": "destination_discovery" if not row.destination else "trip_planning",
                    "current_day_count": (
                        (row.end_date - row.start_date).days + 1
                        if row.start_date and row.end_date else None
                    ),
                    "general_travel_advice_allowed": True,
                    "schedule_recommendation_requested": schedule_recommendation_requested,
                    "missing_required": context.get("planning_state", {}).get(
                        "missing_required", []
                    ),
                    "adjustments_applied": adjustments,
                    "verified_candidates": [
                        {
                            "name": item["name"],
                            "address": item.get("address", ""),
                            "category": item.get("category", ""),
                            "planning_category": item.get("planning_category"),
                            "meal_type": item.get("meal_type"),
                            "popularity_score": item.get("popularity_score"),
                            "provider": item["provider"],
                            "verification_status": item["verification_status"],
                            "evidence_scope": item.get("evidence_scope", "CURRENT_TURN"),
                            "guide_references": item.get("guide_references", []),
                        }
                        for item in reply_candidates
                    ],
                    "search_requests_without_results": searched_requests if not found else 0,
                    "search_failures": search_failures,
                    "weather_forecast": weather_result,
                    "requested_category": requested_category,
                    "requested_meal_type": requested_meal_type,
                    "reply_scope": (
                        "Only synthesize from these verified candidates and the supplied trip facts. "
                        "Do not add other place categories or unverified menu, price, opening, route, "
                        "booking, or popularity claims."
                    ) if found else None,
                },
                api_key=settings.llm_api_key,
                model=settings.llm_model,
                base_url=settings.llm_base_url,
                token_budget=remaining_turn_output_tokens(
                    turn_output_budget,
                    extraction.output_tokens,
                    search_runtime,
                    schedule_runtime,
                    transport_result.runtime if transport_result else None,
                ),
            )
        composed = composed_result.reply if composed_result else None
        memory_suggestion = _reviewable_memory_suggestion(
            composed, enabled=memory_suggestions_enabled, user_message=content,
        )
        reply_tokens = composed_result.total_tokens if composed_result else 0
        reply_output_tokens = composed_result.output_tokens if composed_result else 0
        reply_fallback_reason = composed_result.error_code if composed_result else (
            "EXTRACTION_FAILED" if model_fallback else None
        )
        if reply_fallback_reason == "UNVERIFIED_PERSISTENCE_CLAIM":
            draft_is_current = bool(
                row.draft
                and row.draft.get("context_revision", (row.context or {}).get("revision", 0))
                == (row.context or {}).get("revision", 0)
            )
            reply = (
                "这份草案还没有写入正式行程。请检查右侧预览并点击“确认并应用到行程”完成保存。"
                if draft_is_current
                else "我还没有保存行程：当前没有可确认的草案。先选定地点和时间并生成预览，再确认保存。"
            )
        broad_trip_advice_request = (
            any(term in turn_goal for term in ("建议", "怎么安排", "如何规划", "怎么规划"))
            and any(term in turn_goal for term in ("旅行", "行程", "旅游", "游玩"))
            and not recommendation_requested
            and not request_decision.search_requests
            and not schedule_recommendation_requested
            and not weather_requested
            and not transport_route_requested
            and requested_category is None
        )
        if (
            composed is None
            and broad_trip_advice_request
            and reply_fallback_reason in {
                "INVALID_JSON_OR_SCHEMA", "OUTPUT_TOKEN_LIMIT", "EMPTY_RESPONSE",
            }
        ):
            grounded_tip = grounded_general_trip_tip(
                row.destination,
                [
                    str(item.get("name")) for item in row.candidates[:8]
                    if isinstance(item, dict) and isinstance(item.get("name"), str)
                ],
            )
            if grounded_tip:
                reply = grounded_tip
        search_question_from_model = bool(
            search_loop_result
            and search_loop_result.pending_question
            and search_loop_result.decision_source == "MODEL"
        )
        route_question_from_model = bool(
            transport_result
            and transport_result.loop.pending_question
            and transport_result.decision_source == "MODEL"
        )
        response_source = advisor_reply_source(
            composed_reply=composed is not None,
            model_generated_question=(
                search_question_from_model or route_question_from_model
            ),
        )
        if composed:
            reply = composed.reply
            if adjustments:
                # Preserve the deterministic state change when the model drafts
                # its natural-language explanation.
                reply += "\n" + "；".join(adjustments)
            if composed.follow_up_question:
                reply += "\n" + composed.follow_up_question
            if (
                recommendation_requested
                and not stated_interest_queries
                and not composed.follow_up_question
            ):
                reply += "\n你更偏爱自然风景、历史文化、美食还是亲子活动？"
        if schedule_suggestions:
            reply = _schedule_proposal_reply(
                candidate_count=len(schedule_suggestions),
                route_evidence=(schedule_agent_trace or {}).get("route_evidence"),
                transport_route_requested=transport_route_requested,
                route=transport_result.route if transport_result else None,
                route_question=search_loop_question,
                suggestions=schedule_suggestions,
                candidates=row.candidates,
                start_date=row.start_date,
                reserved_breaks=schedule_breaks,
            )
            if compound_candidates and requested_category == "FOOD":
                meal_label = {
                    "BREAKFAST": "早餐", "LUNCH": "午餐", "DINNER": "晚餐",
                    "SNACK": "小吃/加餐",
                }.get(requested_meal_type, "用餐")
                reply += (
                    f"\n另外，按{meal_label}需求核验到餐饮候选："
                    f"{'、'.join(item['name'] for item in compound_candidates[:8])}。"
                    "选择候选并确认时间后，可将它加入行程调整预览。"
                )
            elif compound_candidates and requested_category == "LODGING":
                reply += (
                    "\n另外，按住宿需求核验到候选："
                    f"{'、'.join(item['name'] for item in compound_candidates[:8])}。"
                    "选择候选后可另行加入旅行草案。"
                )
        elif (
            composed_result is not None and not composed
            and extraction.intent == "general"
            and reply_fallback_reason != "UNVERIFIED_PERSISTENCE_CLAIM"
        ):
            if reply_fallback_reason == "NOT_CONFIGURED":
                reply += " 当前未配置智能回答模型；本轮没有发起地点搜索或更改行程。"
            else:
                reply += " 当前智能回答暂时不可用；本轮没有发起地点搜索或更改行程。"
        elif model_fallback and not composed:
            reply += " 模型暂时不可用，本轮使用本地规则识别明确提及的地点；请核对地图候选。"
        # Model prose can improve the wording, but it must not erase verified
        # weather/route evidence or hide a provider outage from the user.
        if weather_requested or preparation_requested:
            reply = _append_weather_context(
                reply,
                weather_result=weather_result,
                weather_requested=weather_requested,
                preparation_requested=preparation_requested,
                weather_failures=weather_failures,
            )
        if transport_route_requested and (weather_requested or preparation_requested):
            reply = _append_route_outcome(
                reply,
                route=transport_result.route if transport_result else None,
                requested=True,
                failed=bool(
                    transport_result
                    and any(
                        item.get("tool") == "get_trip_route" and item.get("status") == "FAILED"
                        for item in transport_result.loop.transitions
                    )
                ),
            )
        usage = _turn_usage_snapshot(
            extraction, reply_tokens, reply_output_tokens,
            search_runtime, schedule_runtime,
            transport_result.runtime if transport_result else None,
        )
        llm_input_tokens = usage["input_tokens"]
        llm_output_tokens = usage["output_tokens"]
        llm_total_tokens = usage["total_tokens"]
        session_usage_snapshot = record_session_usage(context, llm_input_tokens, llm_output_tokens)
    except ToolFailure as exc:
        await db.rollback()
        try:
            await _persist_failed_turn_usage(
                db, session_id=session_id, owner_id=owner_id,
                extraction=extraction, reply_tokens=reply_tokens,
                reply_output_tokens=reply_output_tokens,
                search_runtime=search_runtime, schedule_runtime=schedule_runtime,
                route_runtime=transport_result.runtime if transport_result else None,
            )
        except SQLAlchemyError:
            await db.rollback()
        if exc.code == "TOOL_TIMEOUT":
            raise HTTPException(504, detail="地图搜索超时，请稍后重试") from exc
        raise HTTPException(503, detail="地图服务暂时不可用，请稍后重试") from exc
    except MapsProviderError as exc:
        await db.rollback()
        try:
            await _persist_failed_turn_usage(
                db, session_id=session_id, owner_id=owner_id,
                extraction=extraction, reply_tokens=reply_tokens,
                reply_output_tokens=reply_output_tokens,
                search_runtime=search_runtime, schedule_runtime=schedule_runtime,
                route_runtime=transport_result.runtime if transport_result else None,
            )
        except SQLAlchemyError:
            await db.rollback()
        raise HTTPException(503, detail="地图服务暂时不可用，请稍后重试") from exc
    except RuntimeError as exc:
        await db.rollback()
        try:
            await _persist_failed_turn_usage(
                db, session_id=session_id, owner_id=owner_id,
                extraction=extraction, reply_tokens=reply_tokens,
                reply_output_tokens=reply_output_tokens,
                search_runtime=search_runtime, schedule_runtime=schedule_runtime,
                route_runtime=transport_result.runtime if transport_result else None,
            )
        except SQLAlchemyError:
            await db.rollback()
        raise HTTPException(502, detail="地点信息解析暂时失败，请重试或直接写出地点名称") from exc
    if turn_orchestration:
        turn_orchestration = {
            **turn_orchestration,
            "decision_history": turn_executor.selection_history,
        }
    turn_report = build_turn_agent_report(
        plan=turn_plan,
        executor=turn_executor,
        stage_traces={
            "SEARCH_WEATHER": search_loop_result.trace if search_loop_result else None,
            "SCHEDULE": schedule_agent_trace,
            "ROUTE": transport_trace,
        },
        orchestration=turn_orchestration,
        supplemental_execution_order=stage_execution_order,
    )
    agent_trace = turn_report["agent_trace"]
    if agent_trace is not None:
        agent_trace = {
            **agent_trace,
            "evidence_reuse": {
                "weather": reused_weather_evidence,
                "route": reused_route_evidence,
                "pending_scope": pending_evidence_scope_status(
                    previous_payload if isinstance(previous_payload, dict) else None,
                    task_id=continuing_task.task_id if continuing_task else None,
                    context_revision=int(context.get("revision", 0)),
                ),
            },
        }
    missing_planned_stage_names = turn_report["missing_stage_names"]
    schedule_preference_question = None
    schedule_stage_failed = any(
        item.get("stage") == "SCHEDULE" and item.get("status") == "FAILED"
        for item in turn_report["stage_execution_report"].get("stage_statuses", [])
    )
    if (
        schedule_recommendation_requested
        and schedule_stage_failed
        and not search_failures
        and not weather_failures
    ):
        schedule_preference_question = _missing_meal_preference_question(
            content,
            max(
                len(preselected_candidate_ids),
                len(context.get("selected_candidate_ids", [])),
            ),
        )
        if schedule_preference_question:
            schedule_recommendation_error = schedule_preference_question
            schedule_agent_trace = {
                **(schedule_agent_trace or {}),
                "status": "NEEDS_USER_INPUT",
                "pending_question": schedule_preference_question,
                "decision_summary": "排程前确认是否预留用餐时间",
            }
            agent_trace = {
                **(agent_trace or {"transitions": []}),
                "status": "NEEDS_USER_INPUT",
                "decision_summary": "排程前确认是否预留用餐时间",
            }
    generic_follow_up_question = (
        composed.follow_up_question.strip()[:500]
        if composed and composed.follow_up_question and composed.follow_up_question.strip()
        else None
    )
    if search_loop_question and agent_trace is None:
        agent_trace = {
            "decision_source": "RULE",
            "decision_summary": "天气预报缺少旅行日期，先向用户询问必要信息",
            "status": "NEEDS_USER_INPUT",
            "transitions": [],
        }
    elif generic_follow_up_question:
        agent_trace = {
            **(agent_trace or {"transitions": []}),
            "decision_source": (agent_trace or {}).get("decision_source", "MODEL"),
            "decision_summary": "顾问提出一个后续问题，等待用户补充后继续原任务",
            "status": "NEEDS_USER_INPUT",
            "pending_question": generic_follow_up_question[:500],
        }
    pending_question_text = (
        schedule_recommendation_error
        if schedule_recommendation_requested and schedule_agent_trace
        and schedule_agent_trace.get("status") == "NEEDS_USER_INPUT"
        else search_loop_question or generic_follow_up_question
    )
    if schedule_preference_question:
        reply += "\n\n" + schedule_preference_question
    has_pending_question = bool(pending_question_text and agent_trace)
    if (
        has_pending_question and pending_question_text
        and reply.strip() != pending_question_text
        and pending_question_text in reply
    ):
        # The question already has its own structured card below; avoid echoing
        # it verbatim in the prose and presenting the same prompt twice.
        reply = re.sub(r"\s+", " ", reply.replace(pending_question_text, " ")).strip()
    if continuing_task:
        pending_task_type = continuing_task.task_type
    elif (
        schedule_recommendation_requested and schedule_agent_trace
        and schedule_agent_trace.get("status") == "NEEDS_USER_INPUT"
    ):
        pending_task_type = "SCHEDULE"
    elif transport_trace and transport_trace.get("status") == "NEEDS_USER_INPUT":
        pending_task_type = "TRANSPORT"
    elif search_loop_question and search_stage:
        pending_task_type = search_stage.task_context.task_type
    else:
        pending_task_type = inferred_task_type
    task_id = (
        continuing_task.task_id if continuing_task and continuing_task.task_id
        else str(uuid4())
    )
    task_goal = continuing_task.task if continuing_task else content
    task_answers = (
        list(pending_question.answers) if pending_question
        else list(continuing_task.answers) if continuing_task
        else []
    )
    task_turn_number = (
        pending_question.turn_number if pending_question
        else continuing_task.turn_number if continuing_task
        else 1
    )
    task_status = resolve_task_status(
        stale=invalidated_pending_task,
        cancelled=cancelled_pending_task,
        waiting_for_user=has_pending_question,
        proposed=bool(schedule_suggestions),
        has_partial_results=bool(found or weather_result),
        tool_failed=bool(
            search_failures or weather_failures or schedule_recommendation_error
            or missing_planned_stage_names or schedule_stage_failed
        ),
        loop_statuses=[
            trace.get("status", "")
            for trace in (schedule_agent_trace, transport_trace)
            if trace is not None
        ] + ([search_loop_result.loop.status] if search_loop_result else []),
    )
    task_snapshot = AdvisorTaskSnapshot(
        task_id=task_id,
        task_type=pending_task_type,
        status=task_status,
        goal=task_goal[:1200],
        context_revision=int(context.get("revision", 0)),
        turn_number=task_turn_number,
        answers=task_answers,
        question=pending_question_text if has_pending_question else None,
        confirmation_options=(
            [option.model_dump() for option in composed.follow_up_options]
            if composed and composed.follow_up_question and pending_question_text == composed.follow_up_question.strip()
            else []
        ),
        action_summary=str((agent_trace or {}).get("decision_summary") or "")[:240],
    ).model_dump(mode="json")
    context["last_agent_task"] = task_snapshot
    db.add(
        AdvisorMessageModel(
            session_id=row.id,
            owner_id=user.id,
            role="assistant",
            content=reply,
            payload={
                **({"response_to_idempotency_key": idempotency_key} if idempotency_key else {}),
                "candidate_ids": preselected_candidate_ids,
                "adjustments_applied": adjustments,
                "intent": extraction.intent,
                "agent_action": (
                    "HANDOFF_TO_EVENT_REPLAN"
                    if event_replan_handoff
                    else "UNSUPPORTED_ACTION"
                    if unsupported_booking
                    else "SCHEDULE_PLAN"
                    if schedule_suggestions
                    else "SCHEDULE_BLOCKED"
                    if schedule_recommendation_requested
                    else "GET_WEATHER"
                    if weather_requested
                    else "RECOMMEND_PLACES"
                    if recommendation_requested and searched_requests
                    else "SEARCH_PLACES"
                    if searched_requests
                    else "UPDATE_DRAFT_SELECTION"
                    if adjustments
                    else "ASK_CLARIFICATION"
                ),
                "planning_category": requested_category,
                "unsupported_action_code": unsupported_booking[0] if unsupported_booking else None,
                "meal_type": requested_meal_type,
                "verified_candidates": [
                    {
                        "name": item["name"],
                        "address": item["address"],
                        "category": item["category"],
                        "planning_category": item.get("planning_category"),
                        "meal_type": item.get("meal_type"),
                        "popularity_score": item.get("popularity_score"),
                        "provider": item["provider"],
                        "verification_status": item["verification_status"],
                        "evidence_scope": "CURRENT_TURN",
                        "guide_references": item.get("guide_references", []),
                    }
                    for item in found[:8]
                ],
                "weather_forecast": ({
                    "status": weather_result.get("status"),
                    "city": weather_result.get("city"),
                    "source": weather_result.get("source"),
                    "attribution": weather_result.get("attribution"),
                    "fetched_at": weather_result.get("fetched_at"),
                    "message": weather_result.get("message"),
                    "days": [
                        {
                            key: day.get(key)
                            for key in (
                                "date", "description", "weather_code",
                                "temperature_min_c", "temperature_max_c",
                                "precipitation_probability_max",
                            )
                            if day.get(key) is not None
                        }
                        for day in weather_result.get("days", [])[:16]
                        if isinstance(day, dict)
                    ],
                } if isinstance(weather_result, dict) else None),
                "action": "SCHEDULE_PROPOSAL" if schedule_suggestions else None,
                "schedule_suggestions": schedule_suggestions,
                "schedule_breaks": schedule_breaks if schedule_suggestions else [],
                "schedule_warnings": proposed_warnings if schedule_suggestions else [],
                "draft_follow_up_options": draft_meal_follow_up_options(
                    schedule_suggestions, row.candidates, row.start_date, row.end_date,
                ),
                "transport_route": transport_result.route if transport_result else None,
                "agent_trace": agent_trace,
                "turn_orchestration": turn_orchestration,
                "schedule_recommendation_requested": schedule_recommendation_requested,
                "event_replan_handoff": ({
                    "event_type": event_replan_type,
                    "action_label": "设置时间并查看途中调整预览",
                    "prefill": event_replan_prefill(turn_goal, event_replan_type),
                } if event_replan_handoff else None),
                "pending_task_type": pending_task_type if has_pending_question else None,
                "pending_task": (task_goal)
                if has_pending_question else None,
                "pending_question": pending_question_text if has_pending_question else None,
                "context_revision": int(context.get("revision", 0)) if has_pending_question else None,
                "context_fingerprint": pending_context_fingerprint(context) if has_pending_question else None,
                "pending_task_id": task_id if has_pending_question else None,
                "pending_answers": task_answers if has_pending_question else [],
                "pending_turn_number": task_turn_number if has_pending_question else 1,
                "agent_task": task_snapshot,
                "trip_action_items": trip_action_items(row, context),
                "response_source": response_source,
                "memory_suggestion": memory_suggestion,
                "response_fallback_reason": reply_fallback_reason,
                "extraction_source": "DETERMINISTIC"
                if deterministic_schedule_extraction else "TEMPLATE"
                if transport_route_requested or model_fallback or not settings.llm_api_key or not settings.llm_model
                else "RECOVERED_CHECKPOINT" if intent_checkpoint_recovered else "MODEL",
                "extraction_checkpoint_recovered": intent_checkpoint_recovered,
                "model_fallback_reason": "EXTRACTION_FAILED" if model_fallback else None,
                "llm_model": settings.llm_model if settings.llm_api_key else None,
                "llm_tokens": llm_total_tokens,
                "llm_input_tokens": llm_input_tokens,
                "llm_output_tokens": llm_output_tokens,
                "llm_session_output_tokens": session_usage_snapshot["output_tokens"],
                "llm_session_output_limit": session_output_limit(
                    context, settings.llm_max_output_tokens_per_session,
                ),
                "tool_calls": [
                    {key: call[key] for key in ("tool", "status", "latency_ms", "error_code")}
                    for call in [
                        *search_runtime.calls,
                        *(schedule_runtime.calls if schedule_runtime else []),
                        *(transport_result.runtime.calls if transport_result else []),
                    ]
                ],
            },
        )
    )
    for call in [*search_runtime.calls, *(transport_result.runtime.calls if transport_result else [])]:
        db.add(
            AdvisorRunEventModel(
                owner_id=user.id,
                session_id=row.id,
                phase="tool",
                operation=call["tool"],
                status=call["status"],
                duration_ms=call["latency_ms"],
                error_code=call["error_code"],
                metadata_json={
                    "result_count": len((call.get("output_data") or {}).get("places", [])),
                    "forecast_day_count": len((call.get("output_data") or {}).get("days", [])),
                    "forecast_status": (call.get("output_data") or {}).get("status"),
                    "route_distance_m": (call.get("output_data") or {}).get("distance_m"),
                    "route_duration_min": (call.get("output_data") or {}).get("duration_min"),
                },
            )
        )
    for call in schedule_runtime.calls if schedule_runtime else []:
        db.add(
            AdvisorRunEventModel(
                owner_id=user.id,
                session_id=row.id,
                phase="tool",
                operation=call["tool"],
                status=call["status"],
                duration_ms=call["latency_ms"],
                error_code=call["error_code"],
                metadata_json={
                    "point_count": len((call.get("input_data") or {}).get("points", []))
                },
            )
        )
    if agent_trace:
        db.add(
            AdvisorRunEventModel(
                owner_id=user.id,
                session_id=row.id,
                phase="agent",
                operation=("route_evidence_decision" if schedule_agent_trace else "place_route_decision" if transport_trace else "advisor_readonly_decision"),
                status=agent_trace.get("status", "UNKNOWN"),
                duration_ms=max(0, int((perf_counter() - (schedule_agent_started_at if schedule_agent_trace else search_agent_started_at)) * 1000)),
                metadata_json={
                    "decision_source": agent_trace.get("decision_source"),
                    "decision_summary": agent_trace.get("decision_summary"),
                    "route_evidence": agent_trace.get("route_evidence"),
                    "route_source": agent_trace.get("route_source"),
                    "transitions": agent_trace.get("transitions", []),
                    "execution_metrics": agent_trace.get("execution_metrics", transport_result.runtime.execution_metrics if transport_result else search_runtime.execution_metrics if search_loop_result else {}),
                },
            )
        )
    db.add(
        AdvisorRunEventModel(
            owner_id=user.id,
            session_id=row.id,
            phase="interpretation",
            operation="send_message",
            status=response_source,
            duration_ms=max(0, int((perf_counter() - started_at) * 1000)),
            token_usage=llm_total_tokens,
            metadata_json={
                "candidate_count": len(found),
                "search_count": searched_requests,
                "failed_search_count": len(search_failures),
                "model_fallback": model_fallback,
                "llm_model": settings.llm_model if settings.llm_api_key else None,
                "llm_input_tokens": llm_input_tokens,
                "llm_output_tokens": llm_output_tokens,
            },
        )
    )
    context["planning_state"] = planning_state(row)
    if has_pending_question:
        context["pending_agent_question"] = {
            "task": task_goal,
            "question": pending_question_text,
            "context_revision": int(context.get("revision", 0)),
            "context_fingerprint": pending_context_fingerprint(context),
            "task_type": pending_task_type,
            "task_id": task_id,
            "answers": task_answers,
            "turn_number": task_turn_number,
            "task_kind": continuing_task.task_kind if continuing_task else None,
        }
    row.context = context
    row.updated_at = datetime.now(UTC)
    if idempotency_key and intent_scope_ref is not None:
        await delete_intent_checkpoint(
            db,
            owner_id=user.id,
            session_id=row.id,
            task_id=checkpoint_task_id,
        )
    await db.commit()
    await db.refresh(row)
    return await read_session(db, row)
