"""Persistence boundary for resumed destination discovery tasks."""

from datetime import UTC, datetime
from time import perf_counter
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from travelpilot_core.agents.loop import AgentTaskContext
from travelpilot_infra.db.models.advisor import (
    AdvisorMessageModel,
    AdvisorRunEventModel,
    AdvisorSessionModel,
)
from travelpilot_infra.db.models.user import UserModel

from travelpilot_api.advisor.checkpoints import checkpoint_writer
from travelpilot_api.advisor.destination_discovery import run_destination_discovery
from travelpilot_api.advisor.interpretation import (
    _DESTINATION_CITIES,
    _prioritized_destination_queries,
)
from travelpilot_api.advisor.orchestration_state import (
    load_search_evidence_checkpoint,
    readonly_tool_decision_callbacks,
    search_evidence_checkpoint_writer,
    search_scope_ref,
)
from travelpilot_api.advisor.session_budget import (
    record_session_usage,
    remaining_session_output_tokens,
)
from travelpilot_api.advisor.sessions import read_session
from travelpilot_api.advisor.stage_execution import AdvisorTurnExecutor
from travelpilot_api.advisor.state import PendingAgentQuestion, pending_context_fingerprint
from travelpilot_api.advisor.task_state import AdvisorTaskSnapshot
from travelpilot_api.advisor.tools import map_evidence_source, rank_map_places
from travelpilot_api.advisor.turn_plan import AdvisorTurnPlan, AdvisorTurnStage
from travelpilot_api.schemas.advisor import AdvisorSessionRead
from travelpilot_api.settings import settings


async def destination_evidence_callbacks(
    *, db, owner_id, session_id, maps, task_id: str | None,
    context_revision: int,
    queries: list[tuple[str, str]],
    approved_cities: list[str] | None = None,
):
    """Load and persist validated map observations for one bounded discovery task."""
    if not task_id:
        return {}, None
    expected_source = map_evidence_source(maps)
    cached: dict[tuple[str, int], dict] = {}
    cities = approved_cities or list(_DESTINATION_CITIES)
    for city in cities:
        for index, (_label, keyword) in enumerate(queries[:2]):
            scope_ref = search_scope_ref(
                city, keyword, context_revision, expected_source,
            )
            snapshot = await load_search_evidence_checkpoint(
                db, owner_id=owner_id, session_id=session_id,
                task_id=task_id, scope_ref=scope_ref,
                expected_source=expected_source,
            )
            if snapshot is not None:
                cached[(city, index)] = snapshot

    async def save(output: dict) -> None:
        city, index = output.get("city"), output.get("interest_index")
        if (
            not isinstance(city, str) or city not in cities
            or isinstance(index, bool) or not isinstance(index, int)
            or not 0 <= index < min(2, len(queries))
        ):
            return
        scope_ref = search_scope_ref(
            city, queries[index][1], context_revision, expected_source,
        )
        writer = search_evidence_checkpoint_writer(
            owner_id=owner_id, session_id=session_id, task_id=task_id,
            scope_ref=scope_ref, expected_source=expected_source,
        )
        await writer(output)

    return cached, save


async def execute_destination_stage(
    *, runner, runner_kwargs: dict, checkpoint=None,
):
    """Run the existing destination Agent loop as a shared Advisor stage.

    The city/interest tool loop remains domain-specific and allowlisted. This
    wrapper gives its lifecycle the same plan, outcome, and checkpoint contract
    as search, weather, route, and schedule stages.
    """
    task_context = AgentTaskContext(
        task_type="DISCOVERY",
        goal=str(runner_kwargs.get("user_goal", "目的地探索"))[:1200] or "目的地探索",
        success_criteria=["基于地图证据返回目的地候选，或向用户追问"],
        allowed_tools=["search_destination_places"],
        approval_boundary="READ_ONLY",
    )
    plan = AdvisorTurnPlan(
        primary_task_type="DISCOVERY",
        stages=(AdvisorTurnStage("SEARCH_WEATHER", task_context),),
    )
    executor = AdvisorTurnExecutor(plan, checkpoint=checkpoint)
    result_box = {}

    async def run():
        result = await runner(**runner_kwargs)
        result_box["result"] = result
        loop = result.loop
        return {
            "status": loop.status,
            "decision_source": result.decision_source,
            "decision_summary": result.decision_summary[:240],
            "search_count": len(result.runtime.calls),
            "verified_candidate_count": sum(
                len(item.get("places", [])) for item in result.searches
                if isinstance(item, dict) and isinstance(item.get("places"), list)
            ),
        }

    execution = await executor.execute(
        requested_stages={"SEARCH_WEATHER"},
        preferred_order=["SEARCH_WEATHER"],
        runners={"SEARCH_WEATHER": run},
    )
    result = result_box.get("result")
    if result is None:
        trace = execution["traces"].get("SEARCH_WEATHER", {})
        raise RuntimeError(str(trace.get("error_code", "DESTINATION_STAGE_FAILED")))
    return result, execution


async def start_destination_recommendation_from_chat(
    *, db: AsyncSession, row: AdvisorSessionModel, user: UserModel, maps,
    user_goal: str, idempotency_key: str | None,
    output_token_budget: int, extraction_total_tokens: int = 0,
    extraction_output_tokens: int = 0, session_reader=None,
) -> AdvisorSessionRead:
    """Run free-city discovery from a natural-language Advisor request."""
    started_at = perf_counter()
    if settings.maps_provider != "amap":
        raise HTTPException(
            status_code=503,
            detail="城市推荐需要配置真实地图服务；当前模拟地图不能用于城市推荐",
        )
    context = dict(row.context or {})
    planning_inputs = context.get("planning_inputs", {})
    hard_constraints = [
        str(item)[:240] for item in planning_inputs.get("hard_constraints", [])[:12]
    ] if isinstance(planning_inputs, dict) and isinstance(
        planning_inputs.get("hard_constraints", []), list
    ) else []
    memories = context.get("personal_memories", [])
    personal_preferences = [
        str(item.get("text", "")).strip()[:240]
        for item in memories[:10]
        if isinstance(item, dict) and item.get("constraint_type") == "PREFERENCE"
        and isinstance(item.get("text"), str) and item["text"].strip()
    ] if isinstance(memories, list) else []
    personal_hard_limits = [
        str(item.get("text", "")).strip()[:240]
        for item in memories[:10]
        if isinstance(item, dict) and item.get("constraint_type") == "HARD_LIMIT"
        and isinstance(item.get("text"), str) and item["text"].strip()
    ] if isinstance(memories, list) else []
    hard_constraints.extend(personal_hard_limits)
    queries = _prioritized_destination_queries(
        user_goal[-800:],
        "\n".join([row.preference_text[-1000:], *personal_preferences])[:1800],
        hard_constraints,
    )
    has_explicit_interest = bool(queries)
    if not queries:
        queries = [("自然与公园", "公园"), ("地方美食", "美食")]
    requested_city = context.get("destination_discovery_city")
    approved_cities = (
        [requested_city]
        if isinstance(requested_city, str) and requested_city.strip()
        else list(_DESTINATION_CITIES)
    )

    recover_decision = None
    persist_decision = None
    discovery_checkpoint = None
    lifecycle_checkpoint = None
    if idempotency_key:
        recover_decision, persist_decision = readonly_tool_decision_callbacks(
            db, owner_id=user.id, session_id=row.id, task_id=idempotency_key,
            context_revision=int(context.get("revision", 0)),
        )
        discovery_checkpoint = checkpoint_writer(
            owner_id=user.id, session_id=row.id, task_id=idempotency_key,
            phase="destination_discovery", context_revision=int(context.get("revision", 0)),
        )
        lifecycle_checkpoint = checkpoint_writer(
            owner_id=user.id, session_id=row.id, task_id=f"destination:{idempotency_key}",
            phase="turn_stage", context_revision=int(context.get("revision", 0)),
        )
    cached_searches, search_observation_callback = await destination_evidence_callbacks(
        db=db, owner_id=user.id, session_id=row.id, maps=maps,
        task_id=f"destination:{idempotency_key}" if idempotency_key else None,
        context_revision=int(context.get("revision", 0)), queries=queries,
        approved_cities=approved_cities,
    )
    goal = (
        f"{user_goal[:800]}\n已记录的旅行偏好：{row.preference_text[-800:]}\n"
        + (f"已确认的个人偏好：{'；'.join(personal_preferences)}\n" if personal_preferences else "")
        + (f"已确认的个人限制：{'；'.join(personal_hard_limits)}" if personal_hard_limits else "")
    )[:1600]
    discovery, stage_execution = await execute_destination_stage(
        runner=run_destination_discovery,
        runner_kwargs={
            "maps": maps, "queries": queries, "approved_cities": approved_cities,
            "user_goal": goal,
            "api_key": settings.llm_api_key, "model": settings.llm_model,
            "base_url": settings.llm_base_url,
            "token_budget": min(
                settings.llm_max_tokens_per_turn,
                output_token_budget,
                remaining_session_output_tokens(
                    context, settings.llm_max_output_tokens_per_session,
                ),
            ),
            "cached_searches": cached_searches,
            "search_observation_callback": search_observation_callback,
            "checkpoint": discovery_checkpoint,
            "load_decision_checkpoint": recover_decision,
            "save_decision_checkpoint": persist_decision,
        },
        checkpoint=lifecycle_checkpoint,
    )
    city_results = {
        city: {"city": city, "matched_interests": [], "places": [], "place_count": 0}
        for city in approved_cities
    }
    for result in discovery.searches:
        if not result["places"]:
            continue
        target = city_results[result["city"]]
        target["matched_interests"].append(result["interest"])
        target["place_count"] += len(result["places"])
        target["places"].extend(result["places"])
    failures = sum(call.get("status") != "SUCCEEDED" for call in discovery.runtime.calls)
    ranked = sorted(
        (item for item in city_results.values() if item["places"]),
        key=lambda item: (-len(item["matched_interests"]), -item["place_count"], item["city"]),
    )[:3]
    retrieved_at = datetime.now(UTC).isoformat()
    for item in ranked:
        unique_places = {place["provider_id"]: place for place in item["places"]}
        item["places"] = rank_map_places(list(unique_places.values()))[:4]
        item["reasons"] = [
            f"地图检索到 {item['place_count']} 个相关地点，覆盖 {len(item['matched_interests'])} 类偏好："
            + "、".join(item["matched_interests"])
        ]
        if any(place.get("popularity_score") is not None for place in item["places"]):
            item["reasons"].append("地点评分来自地图服务；不代表全网热度")
        item["provider"] = item["places"][0]["provider"]
        item["retrieved_at"] = retrieved_at
    search_count = len(discovery.runtime.calls)
    total_pairs = len(approved_cities) * min(2, len(queries))
    question = discovery.loop.pending_question
    if question:
        reply = question + " 你可以补充偏好，或取消本次目的地探索。"
    elif ranked:
        basis = "你提到的兴趣" if has_explicit_interest else "自然与美食两类初步探索"
        reply = f"我根据{basis}检索了城市候选。以下结果来自地图地点证据，不代表已比较城市间交通、费用或季节适宜度。"
        if failures:
            reply += f"另有 {failures} 项地图搜索失败，未用于候选排序。"
        if search_count < total_pairs:
            reply += "Agent 根据已核验结果提前收敛，排名只代表本次已检索范围。"
    elif failures:
        reply = f"已尝试 {search_count} 项地图搜索，但暂时没有可展示的核验结果。你可以补充偏好后重试。"
    else:
        reply = "地图服务没有找到可核验的匹配地点。可以补充偏好，或直接填写目的地。"

    usage = discovery.runtime.execution_metrics
    extraction_output_tokens = max(0, extraction_output_tokens)
    extraction_input_tokens = max(0, extraction_total_tokens - extraction_output_tokens)
    total_input_tokens = usage["input_tokens"] + extraction_input_tokens
    total_output_tokens = usage["output_tokens"] + extraction_output_tokens
    total_tokens = total_input_tokens + total_output_tokens
    if total_input_tokens or total_output_tokens:
        record_session_usage(context, total_input_tokens, total_output_tokens)
    task_id = uuid4().hex[:32]
    task_snapshot = AdvisorTaskSnapshot(
        task_id=task_id,
        task_type="DISCOVERY",
        status="WAITING_USER" if question else (
            "PARTIAL" if failures or discovery.loop.status != "SUCCEEDED" else "COMPLETED"
        ),
        goal=goal[:1200],
        context_revision=int(context.get("revision", 0)),
        turn_number=1,
        answers=[],
        question=question,
        action_summary=discovery.decision_summary[:240],
    ).model_dump(mode="json")
    payload = {
        "event": "DESTINATION_RECOMMENDATIONS",
        "recommendations": ranked,
        "source": "AMap Web Service",
        "retrieved_at": retrieved_at,
        "search_count": search_count,
        "failed_search_count": failures,
        "search_scope_complete": search_count == total_pairs and failures == 0,
        "follow_up_question": question,
        "agent_task": task_snapshot,
        "agent_trace": {
            "status": discovery.loop.status,
            "decision_source": discovery.decision_source,
            "decision_summary": discovery.decision_summary,
            "transitions": discovery.loop.transitions,
            "execution_metrics": usage,
            "stage_statuses": [
                {"stage": stage, "status": stage_status}
                for stage, stage_status in stage_execution["statuses"].items()
            ],
        },
        "stage_execution": {
            "order": stage_execution["execution_order"],
            "statuses": stage_execution["statuses"],
        },
        "comparison_limits": {
            "season_not_compared": not (row.start_date and row.end_date),
            "travel_from_origin_not_compared": not bool(
                isinstance(planning_inputs, dict) and planning_inputs.get("origin")
            ),
            "ranking_basis": "map_interest_match_count",
        },
        "pending_task_type": "DISCOVERY" if question else None,
        "pending_task_kind": "DESTINATION_RECOMMENDATION" if question else None,
        "pending_task": goal if question else None,
        "pending_question": question,
        "pending_task_id": task_id,
        "pending_turn_number": 1 if question else None,
        "pending_answers": [],
        "context_revision": int(context.get("revision", 0)) if question else None,
        "llm_tokens": total_tokens,
        "llm_input_tokens": total_input_tokens,
        "llm_output_tokens": total_output_tokens,
        **({"response_to_idempotency_key": idempotency_key} if idempotency_key else {}),
    }
    db.add(AdvisorMessageModel(
        session_id=row.id, owner_id=user.id, role="assistant", content=reply, payload=payload,
    ))
    db.add(AdvisorRunEventModel(
        owner_id=user.id, session_id=row.id, phase="agent",
        operation="chat_destination_discovery",
        status=task_snapshot["status"],
        duration_ms=max(0, int((perf_counter() - started_at) * 1000)),
        token_usage=total_tokens,
        metadata_json={
            "candidate_count": len(ranked), "search_count": search_count,
            "failed_search_count": failures,
            "decision_source": discovery.decision_source,
            "loop_status": discovery.loop.status,
        },
    ))
    context["last_agent_task"] = task_snapshot
    if question:
        context["pending_agent_question"] = {
            "task": goal, "question": question,
            "context_revision": int(context.get("revision", 0)),
            "context_fingerprint": pending_context_fingerprint(context),
            "task_type": "DISCOVERY", "task_id": task_id,
            "answers": [], "turn_number": 1,
            "task_kind": "DESTINATION_RECOMMENDATION",
        }
    else:
        context.pop("pending_agent_question", None)
        context.pop("destination_discovery_city", None)
    row.context = context
    row.updated_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(row)
    return await (session_reader or read_session)(db, row)


async def resume_destination_recommendation(
    *, db: AsyncSession, row: AdvisorSessionModel, user: UserModel, maps,
    answer: str, pending: PendingAgentQuestion, resumed: PendingAgentQuestion,
    idempotency_key: str | None = None,
    discovery_runner=None,
    session_reader=None,
) -> AdvisorSessionRead:
    """Continue a clarified free-exploration task in its original map-search loop."""
    if settings.maps_provider != "amap":
        raise HTTPException(
            status_code=503,
            detail="目的地推荐需要配置真实地图服务；当前模拟地图不能用于城市推荐",
        )
    started_at = perf_counter()
    row.preference_text = (row.preference_text + "\n" + answer.strip()[:500]).strip()[-4000:]
    context = dict(row.context or {})
    planning_inputs = context.get("planning_inputs", {})
    hard_constraints = [
        str(item)[:240] for item in planning_inputs.get("hard_constraints", [])[:12]
    ] if isinstance(planning_inputs, dict) and isinstance(planning_inputs.get("hard_constraints", []), list) else []
    memories = context.get("personal_memories", [])
    personal_preferences = [
        str(item.get("text", "")).strip()[:240]
        for item in memories[:10]
        if isinstance(item, dict)
        and item.get("constraint_type") == "PREFERENCE"
        and isinstance(item.get("text"), str)
        and item["text"].strip()
    ] if isinstance(memories, list) else []
    personal_hard_limits = [
        str(item.get("text", "")).strip()[:240]
        for item in memories[:10]
        if isinstance(item, dict)
        and item.get("constraint_type") == "HARD_LIMIT"
        and isinstance(item.get("text"), str)
        and item["text"].strip()
    ] if isinstance(memories, list) else []
    hard_constraints.extend(personal_hard_limits)
    discovery_preferences = "\n".join([
        row.preference_text[-1000:], *personal_preferences,
    ])[:1800]
    queries = _prioritized_destination_queries(
        answer.strip()[:500], discovery_preferences, hard_constraints,
    )
    has_explicit_interest = bool(queries)
    if not queries:
        queries = [("自然与公园", "公园"), ("地方美食", "美食")]
    requested_city = context.get("destination_discovery_city")
    approved_cities = [requested_city] if isinstance(requested_city, str) and requested_city.strip() else list(_DESTINATION_CITIES)
    cached_searches, search_observation_callback = await destination_evidence_callbacks(
        db=db, owner_id=user.id, session_id=row.id, maps=maps,
        task_id=f"destination:{idempotency_key}" if idempotency_key else None,
        context_revision=int(context.get("revision", 0)), queries=queries,
        approved_cities=approved_cities,
    )
    recover_decision = None
    persist_decision = None
    discovery_checkpoint = None
    if idempotency_key:
        recover_decision, persist_decision = readonly_tool_decision_callbacks(
            db, owner_id=user.id, session_id=row.id, task_id=idempotency_key,
            context_revision=int(context.get("revision", 0)),
        )
        discovery_checkpoint = checkpoint_writer(
            owner_id=user.id, session_id=row.id, task_id=idempotency_key,
            phase="destination_discovery",
            context_revision=int(context.get("revision", 0)),
        )
    runner_kwargs = {
        "maps": maps,
        "queries": queries,
        "approved_cities": approved_cities,
        "user_goal": (
            f"{pending.task}\n已记录的旅行偏好：{row.preference_text[-1000:]}\n"
            + (f"已确认的个人偏好：{'；'.join(personal_preferences)}\n" if personal_preferences else "")
            + (f"已确认的个人限制：{'；'.join(personal_hard_limits)}\n" if personal_hard_limits else "")
            + f"本轮补充：{answer[:500]}"
        ),
        "api_key": settings.llm_api_key,
        "model": settings.llm_model,
        "base_url": settings.llm_base_url,
        "token_budget": min(
            settings.llm_max_tokens_per_turn,
            remaining_session_output_tokens(context, settings.llm_max_output_tokens_per_session),
        ),
        "cached_searches": cached_searches,
        "search_observation_callback": search_observation_callback,
        "checkpoint": discovery_checkpoint,
        "load_decision_checkpoint": recover_decision,
        "save_decision_checkpoint": persist_decision,
    }
    lifecycle_checkpoint = checkpoint_writer(
        owner_id=user.id, session_id=row.id,
        task_id=f"destination:{idempotency_key or pending.task_id or uuid4()}",
        phase="turn_stage",
        context_revision=int(context.get("revision", 0)),
    ) if idempotency_key else None
    discovery, stage_execution = await execute_destination_stage(
        runner=discovery_runner or run_destination_discovery,
        runner_kwargs=runner_kwargs,
        checkpoint=lifecycle_checkpoint,
    )
    city_results = {
        city: {"city": city, "matched_interests": [], "places": [], "place_count": 0}
        for city in approved_cities
    }
    for result in discovery.searches:
        target = city_results[result["city"]]
        if result["places"]:
            target["matched_interests"].append(result["interest"])
            target["place_count"] += len(result["places"])
            target["places"].extend(result["places"])
    failures = sum(call.get("status") != "SUCCEEDED" for call in discovery.runtime.calls)
    ranked = sorted(
        (item for item in city_results.values() if item["places"]),
        key=lambda item: (-len(item["matched_interests"]), -item["place_count"], item["city"]),
    )[:3]
    retrieved_at = datetime.now(UTC).isoformat()
    for item in ranked:
        unique_places = {place["provider_id"]: place for place in item["places"]}
        item["places"] = rank_map_places(list(unique_places.values()))[:4]
        item["reasons"] = [
            f"地图检索到 {item['place_count']} 个相关地点，覆盖 {len(item['matched_interests'])} 类偏好："
            + "、".join(item["matched_interests"])
        ]
        if any(place.get("popularity_score") is not None for place in item["places"]):
            item["reasons"].append("地点评分来自地图服务；不代表全网热度")
        item["provider"] = item["places"][0]["provider"]
        item["retrieved_at"] = retrieved_at
    search_count = len(discovery.runtime.calls)
    total_pairs = len(approved_cities) * min(2, len(queries))
    if discovery.loop.pending_question:
        reply = discovery.loop.pending_question + " 你可以继续补充偏好，或取消本次目的地探索。"
    elif ranked:
        basis = "你补充的兴趣" if has_explicit_interest else "自然与美食两类初步探索"
        reply = f"我根据{basis}继续检索了候选目的地。以下是已核验地图地点；交通、费用和季节适宜度尚未比较。"
        if failures:
            reply += f"另有 {failures} 项地图搜索失败，未用于候选排序。"
        if search_count < total_pairs:
            reply += "本次仅覆盖部分城市和偏好组合，排名只代表已检索范围。"
    elif failures:
        reply = f"完成了 {search_count} 项地图搜索，但有 {failures} 项失败且没有可展示的核验地点。你可以稍后重试或直接填写目的地。"
    else:
        reply = "地图服务未找到可核验的匹配地点。可以再补充偏好，或直接填写目的地。"
    usage = discovery.runtime.execution_metrics
    if usage["input_tokens"] or usage["output_tokens"]:
        record_session_usage(context, usage["input_tokens"], usage["output_tokens"])
    task_id = pending.task_id or str(uuid4())
    task_turn = resumed.turn_number
    task_answers = list(resumed.answers)
    question = discovery.loop.pending_question
    status = "WAITING_USER" if question else (
        "PARTIAL" if failures or discovery.loop.status != "SUCCEEDED" else "COMPLETED"
    )
    task_snapshot = AdvisorTaskSnapshot(
        task_id=task_id,
        task_type="DISCOVERY",
        status=status,
        goal=pending.task[:1200],
        context_revision=int(context.get("revision", 0)),
        turn_number=task_turn,
        answers=task_answers,
        question=question,
        action_summary=discovery.decision_summary[:240],
    ).model_dump(mode="json")
    payload = {
        "event": "DESTINATION_RECOMMENDATIONS",
        "recommendations": ranked,
        "source": "AMap Web Service",
        "retrieved_at": retrieved_at,
        "search_count": search_count,
        "failed_search_count": failures,
        "search_scope_complete": search_count == total_pairs and failures == 0,
        "follow_up_question": question,
        "agent_task": task_snapshot,
        "agent_trace": {
            "status": discovery.loop.status,
            "decision_source": discovery.decision_source,
            "decision_summary": discovery.decision_summary,
            "transitions": discovery.loop.transitions,
            "execution_metrics": usage,
            "stage_statuses": [
                {"stage": stage, "status": stage_status}
                for stage, stage_status in stage_execution["statuses"].items()
            ],
        },
        "stage_execution": {
            "order": stage_execution["execution_order"],
            "statuses": stage_execution["statuses"],
        },
        "pending_task_type": "DISCOVERY" if question else None,
        "pending_task_kind": "DESTINATION_RECOMMENDATION" if question else None,
        "pending_task": pending.task if question else None,
        "pending_question": question,
        "pending_task_id": task_id if question else None,
        "pending_turn_number": task_turn,
        "pending_answers": task_answers,
        "context_revision": int(context.get("revision", 0)) if question else None,
        "llm_tokens": usage["total_tokens"],
        "llm_input_tokens": usage["input_tokens"],
        "llm_output_tokens": usage["output_tokens"],
        **({"response_to_idempotency_key": idempotency_key} if idempotency_key else {}),
    }
    db.add(AdvisorMessageModel(
        session_id=row.id, owner_id=user.id, role="assistant", content=reply, payload=payload,
    ))
    db.add(AdvisorRunEventModel(
        owner_id=user.id, session_id=row.id, phase="agent",
        operation="resume_destination_recommendation",
        status="NEEDS_USER_INPUT" if question else status,
        duration_ms=max(0, int((perf_counter() - started_at) * 1000)),
        token_usage=usage["total_tokens"],
        metadata_json={
            "candidate_count": len(ranked), "search_count": search_count,
            "failed_search_count": failures, "decision_source": discovery.decision_source,
            "loop_status": discovery.loop.status,
        },
    ))
    context["last_agent_task"] = task_snapshot
    if question:
        context["pending_agent_question"] = {
            "task": pending.task, "question": question,
            "context_revision": int(context.get("revision", 0)),
            "context_fingerprint": pending_context_fingerprint(context),
            "task_type": "DISCOVERY", "task_id": task_id,
            "answers": task_answers, "turn_number": task_turn,
            "task_kind": "DESTINATION_RECOMMENDATION",
        }
    else:
        context.pop("pending_agent_question", None)
        context.pop("destination_discovery_city", None)
        context.pop("destination_discovery_city", None)
    row.context = context
    row.updated_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(row)
    return await (session_reader or read_session)(db, row)
