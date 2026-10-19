"""Verify idempotent Advisor message durability against the configured PostgreSQL database."""

import argparse
import asyncio
import hashlib
import os
import subprocess
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import delete, select
from travelpilot_api.advisor.candidate_identity import provider_candidate_id
from travelpilot_api.advisor.orchestration_state import (
    direct_route_checkpoint_writer,
    direct_route_scope_ref,
    load_direct_route_checkpoint,
    load_readonly_tool_decision,
    load_route_evidence_checkpoint,
    load_search_evidence_checkpoint,
    load_weather_evidence_checkpoint,
    readonly_action_ref,
    readonly_tool_decision_writer,
    route_evidence_checkpoint_writer,
    search_evidence_checkpoint_writer,
    search_scope_ref,
    stage_observation_ref,
    weather_evidence_checkpoint_writer,
    weather_scope_ref,
)
from travelpilot_api.api.v1.advisor_messages import _persist_idempotent_user_message
from travelpilot_core.ports.maps import Place
from travelpilot_infra.db.models.advisor import (
    AdvisorMessageModel,
    AdvisorRunEventModel,
    AdvisorSessionModel,
)
from travelpilot_infra.db.models.poi import PoiModel
from travelpilot_infra.db.models.trip import TripModel
from travelpilot_infra.db.models.user import UserModel
from travelpilot_infra.db.models.wishlist import WishlistItemModel
from travelpilot_infra.db.session import AsyncSessionFactory
from travelpilot_infra.providers.maps.errors import MapsUpstreamError
from travelpilot_infra.providers.maps.fake import FakeMapsProvider
from travelpilot_infra.providers.weather.open_meteo import WeatherProviderError


async def _run_retry_api_phase(
    *,
    phase: str,
    owner_id,
    session_id,
    idempotency_key: str,
    content: str,
    recovered_stage: str | None = None,
) -> None:
    """Run one message API invocation in an isolated verifier subprocess."""
    from travelpilot_api.advisor import turn_service
    from travelpilot_api.api.v1 import advisor_messages
    from travelpilot_api.schemas.advisor import AdvisorMessageCreate

    async with AsyncSessionFactory() as db:
        user = await db.scalar(select(UserModel).where(UserModel.id == owner_id))
        assert user is not None

        service_recovery = phase in {
            "SERVICE_RECOVERY_INTERRUPT",
            "SERVICE_RECOVERY_RETRY",
            "SERVICE_MIXED_INTERRUPT",
            "SERVICE_MIXED_RETRY",
            "SERVICE_MIXED_SEARCH_FAILURE_RETRY",
            "SERVICE_MIXED_ALL_SEARCHES_INTERRUPT",
            "SERVICE_MIXED_ALL_SEARCHES_RETRY",
            "SERVICE_WEATHER_INTERRUPT",
            "SERVICE_WEATHER_RETRY",
            "SERVICE_ROUTE_INTERRUPT",
            "SERVICE_ROUTE_RETRY",
            "SERVICE_SCHEDULE_INTERRUPT",
            "SERVICE_SCHEDULE_RETRY",
            "SERVICE_SEARCH_SCHEDULE_INTERRUPT",
            "SERVICE_SEARCH_SCHEDULE_RETRY",
            "SERVICE_REPLY_COMMIT_INTERRUPT",
            "SERVICE_REPLY_COMMIT_RETRY",
            "SERVICE_PENDING_ANSWER_INTERRUPT",
            "SERVICE_PENDING_ANSWER_RETRY",
            "SERVICE_COMPOUND_INTERRUPT",
            "SERVICE_COMPOUND_RETRY",
            "SERVICE_COMPOUND_ROUTE_FIRST_INTERRUPT",
            "SERVICE_COMPOUND_WEATHER_FIRST_INTERRUPT",
            "SERVICE_COMPOUND_ROUTE_FAILURE_RETRY",
            "SERVICE_COMPOUND_WEATHER_FAILURE_RETRY",
        }

        checkpoint_phase = phase.removesuffix("_INTERRUPT")
        if checkpoint_phase in {
            "SEARCH_CHECKPOINT",
            "WEATHER_CHECKPOINT",
            "ROUTE_CHECKPOINT",
            "DIRECT_ROUTE_CHECKPOINT",
            "DECISION_CHECKPOINT",
        }:
            retrieved_at = datetime.now(UTC).isoformat()
            if checkpoint_phase == "SEARCH_CHECKPOINT":
                expected_source = "AMap Web Service"
                evidence = {
                    "status": "VERIFIED",
                    "retrieved_at": retrieved_at,
                    "places": [
                        {
                            "provider": "amap",
                            "provider_id": "integration-poi-1",
                            "name": "Integration Test Park",
                            "category": "park",
                            "address": "Chengdu",
                            "latitude": 30.67,
                            "longitude": 104.06,
                        }
                    ],
                }
                writer = search_evidence_checkpoint_writer(
                    owner_id=owner_id,
                    session_id=session_id,
                    task_id=f"destination:{idempotency_key}",
                    scope_ref=search_scope_ref("Chengdu", "park", 4, expected_source),
                    expected_source=expected_source,
                )
                await writer(evidence)
            elif checkpoint_phase == "WEATHER_CHECKPOINT":
                expected_source = "Open-Meteo"
                city = "成都"
                writer = weather_evidence_checkpoint_writer(
                    owner_id=owner_id,
                    session_id=session_id,
                    task_id=f"weather:{idempotency_key}",
                    scope_ref=weather_scope_ref(
                        city,
                        "2026-10-20",
                        "2026-10-21",
                        4,
                        expected_source,
                    ),
                    expected_source=expected_source,
                )
                await writer(
                    {
                        "status": "AVAILABLE",
                        "source": expected_source,
                        "fetched_at": retrieved_at,
                        "city": city,
                        "timezone": "Asia/Shanghai",
                        "attribution": "Weather by Open-Meteo",
                        "message": None,
                        "days": [
                            {
                                "date": "2026-10-20",
                                "description": "多云",
                                "weather_code": 2,
                                "temperature_max_c": 23,
                                "temperature_min_c": 16,
                                "precipitation_probability_max": 20,
                            }
                        ],
                    }
                )
            elif checkpoint_phase == "ROUTE_CHECKPOINT":
                writer = route_evidence_checkpoint_writer(
                    owner_id=owner_id,
                    session_id=session_id,
                    task_id=f"schedule:{idempotency_key}",
                )
                await writer(
                    {
                        "candidate_ids": ["candidate-a", "candidate-b"],
                        "context_revision": 6,
                        "durations_min": [[0, 18], [21, 0]],
                        "status": "VERIFIED",
                        "source": "AMap Web Service",
                        "retrieved_at": retrieved_at,
                        "place_names": ["never persist names"],
                        "coordinates": [[30.1, 104.1], [30.2, 104.2]],
                    }
                )
            elif checkpoint_phase == "DIRECT_ROUTE_CHECKPOINT":
                expected_source = "Local simulated maps"
                goal = "从宽窄巷子到熊猫基地查驾车路线"
                candidate_ids = ["place-a", "place-b"]
                scope = direct_route_scope_ref(
                    goal,
                    candidate_ids,
                    7,
                    expected_source,
                )
                writer = direct_route_checkpoint_writer(
                    owner_id=owner_id,
                    session_id=session_id,
                    task_id=f"direct-route:{idempotency_key}",
                    scope_ref=scope,
                    expected_source=expected_source,
                )
                await writer(
                    {
                        "status": "SIMULATED",
                        "origin_place_id": "place-a",
                        "destination_place_id": "place-b",
                        "points": [[30.6, 104.0], [30.7, 104.1]],
                        "distance_m": 12000,
                        "duration_min": 35,
                        "mode": "DRIVING",
                        "source": expected_source,
                        "retrieved_at": retrieved_at,
                        "polyline": [[30.6, 104.0], [30.65, 104.05], [30.7, 104.1]],
                        "segments": [{"mode": "DRIVING", "label": "模拟路线"}],
                    }
                )
            else:
                tool = "search_places"
                action_ref = readonly_action_ref(tool, {"keyword": "park", "city": "Chengdu"})
                observation_ref = stage_observation_ref(
                    {
                        "goal_ref": "synthetic-goal",
                        "evidence": [],
                        "context_revision": 8,
                    }
                )
                approved_actions = {action_ref: tool}
                writer = readonly_tool_decision_writer(
                    owner_id=owner_id,
                    session_id=session_id,
                    task_id=f"decision:{idempotency_key}",
                    context_revision=8,
                    observation_ref=observation_ref,
                    decision_number=1,
                    approved_actions=approved_actions,
                )
                await writer({"tool": tool, "action_ref": action_ref})
            # Simulate a worker dying immediately after the independently
            # committed observation checkpoint, before it can report success.
            os._exit(74)

        async def simulated_turn(
            turn_db,
            row,
            turn_content,
            _maps,
            _weather,
            key,
            turn_user,
            *,
            resume_existing_user_message,
        ):
            assert row.id == session_id
            assert turn_user.id == owner_id
            assert turn_content == content
            assert key == idempotency_key
            assert resume_existing_user_message is True
            if phase == "INTERRUPT":
                # This child is disposable: terminate without running route
                # cleanup/finally so the parent can test a real process boundary.
                os._exit(73)
            if phase in {"CONCURRENT", "CONCURRENT_DISTINCT"}:
                # Hold the owning request open long enough for the second
                # independent process to contend on the same session lock.
                await asyncio.sleep(0.25)
            turn_db.add(
                AdvisorMessageModel(
                    session_id=row.id,
                    owner_id=turn_user.id,
                    role="assistant",
                    content="已从持久化请求恢复",
                    idempotency_key=None,
                    payload={"response_to_idempotency_key": key},
                )
            )
            await turn_db.commit()
            return {"recovered": True}

        async def read_result(_db, _row):
            return {"recovered": True}

        original_execute = turn_service.execute_advisor_turn
        original_route_query = turn_service.run_advisor_route_query
        original_route_evidence_loader = turn_service.load_route_evidence_checkpoint
        original_search_loop = turn_service.run_advisor_search_loop
        original_extract_intent = turn_service._extract_advisor_intent
        original_choose_stage_order = turn_service.choose_stage_order
        original_read_session = advisor_messages.read_session
        if phase not in {
            "SERVICE_REPLY_COMMIT_INTERRUPT", "SERVICE_REPLY_COMMIT_RETRY",
            "SERVICE_PENDING_ANSWER_INTERRUPT", "SERVICE_PENDING_ANSWER_RETRY",
        }:
            advisor_messages.read_session = read_result
        original_search_writer = turn_service.search_evidence_checkpoint_writer
        original_weather_writer = turn_service.weather_evidence_checkpoint_writer
        original_direct_route_writer = turn_service.direct_route_checkpoint_writer
        original_route_evidence_writer = turn_service.route_evidence_checkpoint_writer
        if service_recovery:
            if phase == "SERVICE_SEARCH_SCHEDULE_RETRY":
                async def assert_search_schedule_route_recovery(db, **kwargs):
                    recovered = await original_route_evidence_loader(db, **kwargs)
                    saved_rows = await db.scalars(
                        select(AdvisorRunEventModel).where(
                            AdvisorRunEventModel.owner_id == kwargs["owner_id"],
                            AdvisorRunEventModel.session_id == kwargs["session_id"],
                            AdvisorRunEventModel.operation == "route_evidence_snapshot",
                        ).limit(1)
                    )
                    saved = saved_rows.first()
                    metadata = saved.metadata_json if saved else {}
                    if (
                        recovered is None
                        or metadata.get("context_revision") != kwargs.get("context_revision")
                        or metadata.get("candidate_ids") != kwargs.get("candidate_ids")
                        or metadata.get("source") != kwargs.get("expected_source")
                    ):
                        raise AssertionError(
                            "search-then-schedule retry failed to restore the exact committed "
                            "route matrix scope"
                        )
                    return recovered

                turn_service.load_route_evidence_checkpoint = (
                    assert_search_schedule_route_recovery
                )
            if phase in {
                "SERVICE_SEARCH_SCHEDULE_INTERRUPT", "SERVICE_SEARCH_SCHEDULE_RETRY",
            }:
                from travelpilot_core.agents.extraction import Extraction, PlaceMention

                async def fixed_search_schedule_extraction(*_args, **_kwargs):
                    return Extraction(
                        places=[PlaceMention(
                            name="成都科学博物馆",
                            category="ATTRACTION",
                            confidence=0.95,
                            action="add",
                            evidence="搜索成都科学博物馆并加入旅行",
                        )],
                        intent="schedule",
                    ), False

                turn_service._extract_advisor_intent = fixed_search_schedule_extraction

            if phase == "SERVICE_PENDING_ANSWER_INTERRUPT":

                async def exit_after_answer_is_persisted(*_args, **_kwargs):
                    # send_message commits the answer and TURN_STARTED before
                    # entering the Advisor turn; recovery must rebuild the task
                    # from the preceding persisted assistant response.
                    os._exit(82)

                turn_service.execute_advisor_turn = exit_after_answer_is_persisted

            if phase == "SERVICE_REPLY_COMMIT_INTERRUPT":

                async def execute_and_exit_after_commit(*args, **kwargs):
                    await original_execute(*args, **kwargs)
                    os._exit(79)

                turn_service.execute_advisor_turn = execute_and_exit_after_commit

            if phase in {
                "SERVICE_RECOVERY_INTERRUPT", "SERVICE_MIXED_INTERRUPT",
                "SERVICE_REPLY_COMMIT_INTERRUPT",
            }:

                def interrupt_after_search_checkpoint(**kwargs):
                    writer = original_search_writer(**kwargs)

                    async def write_then_exit(evidence):
                        await writer(evidence)
                        async with AsyncSessionFactory() as check_db:
                            saved = await check_db.scalar(
                                select(AdvisorRunEventModel.id)
                                .where(
                                    AdvisorRunEventModel.owner_id == kwargs["owner_id"],
                                    AdvisorRunEventModel.session_id == kwargs["session_id"],
                                    AdvisorRunEventModel.operation == "map_search_snapshot",
                                    AdvisorRunEventModel.metadata_json["task_ref"].as_string()
                                    == hashlib.sha256(
                                        kwargs["task_id"].encode("utf-8")
                                    ).hexdigest()[:24],
                                )
                                .limit(1)
                            )
                        if saved is None:
                            raise RuntimeError(
                                "search observation writer returned without a durable row"
                            )
                        os._exit(75)

                    return write_then_exit

                turn_service.search_evidence_checkpoint_writer = interrupt_after_search_checkpoint

            if phase == "SERVICE_MIXED_ALL_SEARCHES_INTERRUPT":

                def interrupt_after_all_search_checkpoints(**kwargs):
                    writer = original_search_writer(**kwargs)

                    async def write_then_exit(evidence):
                        await writer(evidence)
                        async with AsyncSessionFactory() as check_db:
                            rows = await check_db.scalars(
                                select(AdvisorRunEventModel.id)
                                .where(
                                    AdvisorRunEventModel.owner_id == kwargs["owner_id"],
                                    AdvisorRunEventModel.session_id == kwargs["session_id"],
                                    AdvisorRunEventModel.operation == "map_search_snapshot",
                                )
                                .limit(2)
                            )
                            if len(rows.all()) >= 2:
                                os._exit(81)

                    return write_then_exit

                turn_service.search_evidence_checkpoint_writer = (
                    interrupt_after_all_search_checkpoints
                )

            if phase == "SERVICE_WEATHER_INTERRUPT":

                def interrupt_after_weather_checkpoint(**kwargs):
                    writer = original_weather_writer(**kwargs)

                    async def write_then_exit(evidence):
                        await writer(evidence)
                        async with AsyncSessionFactory() as check_db:
                            saved = await check_db.scalar(
                                select(AdvisorRunEventModel.id)
                                .where(
                                    AdvisorRunEventModel.owner_id == kwargs["owner_id"],
                                    AdvisorRunEventModel.session_id == kwargs["session_id"],
                                    AdvisorRunEventModel.operation == "weather_evidence_snapshot",
                                    AdvisorRunEventModel.metadata_json["task_ref"].as_string()
                                    == hashlib.sha256(
                                        kwargs["task_id"].encode("utf-8")
                                    ).hexdigest()[:24],
                                )
                                .limit(1)
                            )
                        if saved is None:
                            raise RuntimeError(
                                "weather observation writer returned without a durable row"
                            )
                        os._exit(76)

                    return write_then_exit

                turn_service.weather_evidence_checkpoint_writer = interrupt_after_weather_checkpoint

            if phase == "SERVICE_ROUTE_INTERRUPT":

                async def run_route_then_exit(**kwargs):
                    result = await original_route_query(**kwargs)
                    if result.route is not None:
                        os._exit(77)
                    return result

                turn_service.run_advisor_route_query = run_route_then_exit

            if phase in {
                "SERVICE_COMPOUND_INTERRUPT",
                "SERVICE_COMPOUND_ROUTE_FIRST_INTERRUPT",
                "SERVICE_COMPOUND_WEATHER_FIRST_INTERRUPT",
            }:

                if phase != "SERVICE_COMPOUND_INTERRUPT":
                    forced_stage = (
                        "ROUTE" if phase == "SERVICE_COMPOUND_ROUTE_FIRST_INTERRUPT"
                        else "SEARCH_WEATHER"
                    )

                    async def force_compound_stage_order(**kwargs):
                        stages = list(kwargs["stages"])
                        order = [forced_stage] + [item for item in stages if item != forced_stage]
                        return {
                            "order": order,
                            "decision_source": "RULE_TEST_FIXTURE",
                            "decision_summary": "验证复合阶段两种执行顺序",
                            "fallback_reason": None,
                        }

                    turn_service.choose_stage_order = force_compound_stage_order

                async def exit_after_route_or_weather_stage(**kwargs):
                    result = await original_route_query(**kwargs)
                    if result.route is not None:
                        os._exit(80)
                    return result

                async def exit_after_weather_stage(**kwargs):
                    result = await original_search_loop(**kwargs)
                    if result.weather is not None:
                        os._exit(80)
                    return result

                turn_service.run_advisor_route_query = exit_after_route_or_weather_stage
                turn_service.run_advisor_search_loop = exit_after_weather_stage

            if phase in {"SERVICE_SCHEDULE_INTERRUPT", "SERVICE_SEARCH_SCHEDULE_INTERRUPT"}:

                def interrupt_after_schedule_route_checkpoint(**kwargs):
                    writer = original_route_evidence_writer(**kwargs)

                    async def write_then_exit(evidence):
                        await writer(evidence)
                        async with AsyncSessionFactory() as check_db:
                            saved = await check_db.scalar(
                                select(AdvisorRunEventModel.id).where(
                                    AdvisorRunEventModel.owner_id == kwargs["owner_id"],
                                    AdvisorRunEventModel.session_id == kwargs["session_id"],
                                    AdvisorRunEventModel.operation == "route_evidence_snapshot",
                                    AdvisorRunEventModel.metadata_json["task_ref"].as_string()
                                    == hashlib.sha256(
                                        kwargs["task_id"].encode("utf-8")
                                    ).hexdigest()[:24],
                                ).limit(1)
                            )
                        if saved is None:
                            raise RuntimeError(
                                "schedule route-evidence writer returned without a durable row"
                            )
                        if phase == "SERVICE_SEARCH_SCHEDULE_INTERRUPT":
                            search_snapshot = await check_db.scalar(
                                select(AdvisorRunEventModel).where(
                                    AdvisorRunEventModel.owner_id == kwargs["owner_id"],
                                    AdvisorRunEventModel.session_id == kwargs["session_id"],
                                    AdvisorRunEventModel.operation == "map_search_snapshot",
                                    AdvisorRunEventModel.metadata_json["task_ref"].as_string()
                                    == hashlib.sha256(
                                        kwargs["task_id"].encode("utf-8")
                                    ).hexdigest()[:24],
                                ).limit(1)
                            )
                            if search_snapshot is None:
                                raise RuntimeError(
                                    "search checkpoint was not durable before schedule matrix"
                                )
                        os._exit(78)

                    return write_then_exit

                turn_service.route_evidence_checkpoint_writer = (
                    interrupt_after_schedule_route_checkpoint
                )

            class FakeMapsProviderThatMustNotSearch(FakeMapsProvider):
                async def search_place(self, keyword, city=None):
                    raise AssertionError(
                        f"retry repeated provider search after checkpoint: {keyword} / {city}"
                    )

            class FakeMapsProviderThatMustNotRepeatMealSearch(FakeMapsProvider):
                def __init__(self):
                    self.retried_queries = []

                async def search_place(self, keyword, city=None):
                    if keyword == "餐饮服务":
                        raise AssertionError(
                            "mixed retry repeated the completed meal search after checkpoint"
                        )
                    self.retried_queries.append(keyword)
                    return await super().search_place(keyword, city)

            class FakeMapsProviderThatFailsAttractionSearch(FakeMapsProvider):
                def __init__(self):
                    self.failed_queries = []

                async def search_place(self, keyword, city=None):
                    if keyword == "景点":
                        self.failed_queries.append(keyword)
                        raise MapsUpstreamError("synthetic attraction search failure")
                    raise AssertionError(
                        f"retry repeated the completed meal search: {keyword} / {city}"
                    )

            class FakeMapsSearchScheduleProvider(FakeMapsProvider):
                async def search_place(self, keyword, city=None):
                    if "博物馆" in keyword:
                        return [Place(
                            "fake",
                            "fake-checkpoint-museum",
                            "成都科学博物馆",
                            "成都市评测区",
                            "博物馆;博物馆",
                            30.671,
                            104.073,
                            {"open": "09:00", "close": "17:00"},
                            90,
                        )]
                    return await super().search_place(keyword, city)

            retry_maps = None
            if phase in {
                "SERVICE_RECOVERY_INTERRUPT", "SERVICE_MIXED_INTERRUPT",
                "SERVICE_MIXED_ALL_SEARCHES_INTERRUPT",
                "SERVICE_PENDING_ANSWER_INTERRUPT",
            } or phase == "SERVICE_PENDING_ANSWER_RETRY":
                maps = FakeMapsProvider()
            elif phase == "SERVICE_SEARCH_SCHEDULE_INTERRUPT":
                maps = FakeMapsSearchScheduleProvider()
            elif phase == "SERVICE_MIXED_RETRY":
                retry_maps = FakeMapsProviderThatMustNotRepeatMealSearch()
                maps = retry_maps
            elif phase == "SERVICE_MIXED_SEARCH_FAILURE_RETRY":
                retry_maps = FakeMapsProviderThatFailsAttractionSearch()
                maps = retry_maps
            elif phase == "SERVICE_MIXED_ALL_SEARCHES_RETRY":
                retry_maps = FakeMapsProviderThatMustNotSearch()
                maps = retry_maps
            else:
                maps = FakeMapsProviderThatMustNotSearch()

            class RecoveryWeatherProvider:
                def __init__(self):
                    self.forecast_calls = 0

                async def forecast(self, city, start_date, end_date):
                    if phase == "SERVICE_WEATHER_RETRY" or (
                        phase in {
                            "SERVICE_COMPOUND_RETRY",
                            "SERVICE_COMPOUND_ROUTE_FAILURE_RETRY",
                        }
                        and recovered_stage == "SEARCH_WEATHER"
                    ):
                        raise AssertionError(
                            "retry repeated weather provider call after checkpoint"
                        )
                    self.forecast_calls += 1
                    if phase == "SERVICE_COMPOUND_WEATHER_FAILURE_RETRY":
                        raise WeatherProviderError("synthetic weather provider failure")
                    return {
                        "status": "AVAILABLE",
                        "city": city,
                        "timezone": "Asia/Shanghai",
                        "source": "Open-Meteo",
                        "attribution": "Weather by Open-Meteo",
                        "fetched_at": datetime.now(UTC),
                        "message": None,
                        "days": [
                            {
                                "date": (start_date + timedelta(days=offset)).isoformat(),
                                "description": "小雨",
                                "weather_code": 61,
                                "temperature_max_c": 21,
                                "temperature_min_c": 15,
                                "precipitation_probability_max": 80,
                            }
                            for offset in range((end_date - start_date).days + 1)
                        ],
                    }

            class FakeMapsRouteRecoveryProvider(FakeMapsProvider):
                def __init__(self):
                    self.route_calls = []

                async def get_route(self, origin, destination, mode="DRIVING", **kwargs):
                    if phase == "SERVICE_COMPOUND_ROUTE_FAILURE_RETRY":
                        self.route_calls.append((origin, destination, mode))
                        raise MapsUpstreamError("synthetic route provider failure")
                    if phase == "SERVICE_ROUTE_RETRY" or (
                        phase in {
                            "SERVICE_COMPOUND_RETRY",
                            "SERVICE_COMPOUND_ROUTE_FAILURE_RETRY",
                            "SERVICE_COMPOUND_WEATHER_FAILURE_RETRY",
                        } and recovered_stage == "ROUTE"
                    ):
                        raise AssertionError("retry repeated route provider call after checkpoint")
                    self.route_calls.append((origin, destination, mode))
                    return await super().get_route(origin, destination, mode, **kwargs)

            class FakeMapsScheduleRecoveryProvider(FakeMapsProvider):
                def __init__(self):
                    self.matrix_calls = 0

                async def get_distance_matrix(self, points):
                    if phase == "SERVICE_SCHEDULE_RETRY":
                        raise AssertionError(
                            "retry repeated schedule distance-matrix provider call after checkpoint"
                        )
                    self.matrix_calls += 1
                    return await super().get_distance_matrix(points)

            class FakeMapsSearchScheduleRecoveryProvider(FakeMapsProvider):
                def __init__(self):
                    self.search_calls = 0
                    self.search_queries = []
                    self.matrix_calls = 0

                async def search_place(self, keyword, city=None):
                    self.search_calls += 1
                    self.search_queries.append((keyword, city))
                    raise AssertionError(
                        f"retry repeated completed place search: {keyword} / {city}"
                    )

                async def get_distance_matrix(self, points):
                    self.matrix_calls += 1
                    raise AssertionError(
                        "retry repeated the schedule matrix instead of reusing its checkpoint"
                    )

            weather = RecoveryWeatherProvider()
            maps = FakeMapsRouteRecoveryProvider() if phase in {
                "SERVICE_ROUTE_INTERRUPT", "SERVICE_ROUTE_RETRY",
                "SERVICE_COMPOUND_INTERRUPT", "SERVICE_COMPOUND_RETRY",
                "SERVICE_COMPOUND_ROUTE_FIRST_INTERRUPT",
                "SERVICE_COMPOUND_WEATHER_FIRST_INTERRUPT",
                "SERVICE_COMPOUND_ROUTE_FAILURE_RETRY",
                "SERVICE_COMPOUND_WEATHER_FAILURE_RETRY",
            } else maps
            maps = FakeMapsScheduleRecoveryProvider() if phase in {
                "SERVICE_SCHEDULE_INTERRUPT", "SERVICE_SCHEDULE_RETRY"
            } else maps
            maps = FakeMapsSearchScheduleRecoveryProvider() if phase == (
                "SERVICE_SEARCH_SCHEDULE_RETRY"
            ) else maps
        else:
            turn_service.execute_advisor_turn = simulated_turn
            maps = None
            weather = None
        try:
            try:
                result = await advisor_messages.send_message(
                    session_id=session_id,
                    payload=AdvisorMessageCreate(content=content),
                    db=db,
                    user=user,
                    maps=maps,
                    weather=weather,
                    idempotency_key=idempotency_key,
                )
            except RuntimeError as exc:
                if phase != "INTERRUPT" or str(exc) != "SIMULATED_PROCESS_INTERRUPTION":
                    raise
            except Exception as exc:
                if phase not in {
                    "SERVICE_RECOVERY_RETRY", "SERVICE_WEATHER_RETRY",
                    "SERVICE_ROUTE_RETRY", "SERVICE_SCHEDULE_RETRY",
                    "SERVICE_SEARCH_SCHEDULE_RETRY",
                    "SERVICE_REPLY_COMMIT_RETRY",
                    "SERVICE_PENDING_ANSWER_RETRY",
                    "SERVICE_COMPOUND_RETRY",
                    "SERVICE_COMPOUND_ROUTE_FAILURE_RETRY",
                    "SERVICE_COMPOUND_WEATHER_FAILURE_RETRY",
                    "SERVICE_MIXED_SEARCH_FAILURE_RETRY",
                    "SERVICE_MIXED_ALL_SEARCHES_RETRY",
                }:
                    raise
                raise RuntimeError(
                    f"real Advisor retry failed: {type(exc).__name__}: {exc}"
                ) from exc
            else:
                if phase == "INTERRUPT":
                    raise AssertionError(
                        "the interruption phase should fail after input persistence"
                    )
                if phase == "SERVICE_SEARCH_SCHEDULE_INTERRUPT":
                    assistant = next(
                        (
                            item for item in reversed(getattr(result, "messages", []))
                            if item.role == "assistant"
                        ),
                        None,
                    )
                    raise AssertionError(
                        "search-then-schedule interruption point was not reached; "
                        f"action={assistant.payload.get('agent_action') if assistant else None!r}, "
                        f"status={(assistant.payload.get('agent_trace') or {}).get('status') if assistant else None!r}, "
                        f"trace={(assistant.payload.get('agent_trace') or {}).get('transitions') if assistant else None!r}, "
                        f"schedule_error={assistant.payload.get('schedule_recommendation_error') if assistant else None!r}, "
                        f"stage_statuses={(assistant.payload.get('agent_trace') or {}).get('stage_statuses') if assistant else None!r}, "
                        f"search_results={assistant.payload.get('verified_candidates') if assistant else None!r}, "
                        f"reply={assistant.content if assistant else None!r}"
                    )
                if service_recovery:
                    if phase == "SERVICE_WEATHER_RETRY":
                        messages = getattr(result, "messages", [])
                        assistant = next(
                            (item for item in reversed(messages) if item.role == "assistant"), None
                        )
                        if (
                            assistant is None
                            or "小雨" not in assistant.content
                            or "建议带伞" not in assistant.content
                        ):
                            raise AssertionError("weather retry did not use the recovered forecast")
                    elif phase == "SERVICE_ROUTE_RETRY":
                        messages = getattr(result, "messages", [])
                        assistant = next(
                            (item for item in reversed(messages) if item.role == "assistant"), None
                        )
                        if assistant is None or "路线" not in assistant.content:
                            raise AssertionError("route retry did not report the recovered route")
                        if maps.route_calls:
                            raise AssertionError("route provider was called again during recovery")
                    elif phase == "SERVICE_SCHEDULE_RETRY":
                        if maps.matrix_calls:
                            raise AssertionError(
                                "schedule distance-matrix provider was called again during recovery"
                            )
                        messages = getattr(result, "messages", [])
                        assistant = next(
                            (item for item in reversed(messages) if item.role == "assistant"), None
                        )
                        payload = assistant.payload if assistant else {}
                        if (
                            payload.get("agent_action") != "SCHEDULE_PLAN"
                            or not payload.get("schedule_suggestions")
                        ):
                            raise AssertionError(
                                "schedule retry did not produce a proposal from recovered route evidence; "
                                f"agent_action={payload.get('agent_action')!r}, "
                                f"suggestions={payload.get('schedule_suggestions')!r}, "
                                f"content={assistant.content if assistant else None!r}"
                            )
                    elif phase == "SERVICE_SEARCH_SCHEDULE_RETRY":
                        if maps.search_calls or maps.matrix_calls:
                            raise AssertionError(
                                "search-then-schedule retry repeated a completed maps operation: "
                                f"search_calls={maps.search_calls}, matrix_calls={maps.matrix_calls}, "
                                f"search_queries={maps.search_queries}"
                            )
                        messages = getattr(result, "messages", [])
                        assistant = next(
                            (item for item in reversed(messages) if item.role == "assistant"), None
                        )
                        payload = assistant.payload if assistant else {}
                        if (
                            payload.get("agent_action") != "SCHEDULE_PLAN"
                            or not payload.get("schedule_suggestions")
                        ):
                            raise AssertionError(
                                "search-then-schedule retry did not produce a proposal from recovered evidence"
                            )
                    elif phase == "SERVICE_REPLY_COMMIT_RETRY":
                        messages = getattr(result, "messages", [])
                        linked = [
                            item for item in messages
                            if item.role == "assistant"
                            and isinstance(item.payload, dict)
                            and item.payload.get("response_to_idempotency_key") == idempotency_key
                        ]
                        if len(linked) != 1:
                            raise AssertionError(
                                "same-key response recovery did not return exactly one committed assistant reply; "
                                f"messages={[(item.role, item.payload) for item in messages]!r}"
                            )
                    elif phase == "SERVICE_PENDING_ANSWER_RETRY":
                        messages = getattr(result, "messages", [])
                        linked = [
                            item for item in messages
                            if item.role == "assistant"
                            and isinstance(item.payload, dict)
                            and item.payload.get("response_to_idempotency_key") == idempotency_key
                        ]
                        if len(linked) != 1:
                            raise AssertionError(
                                "pending-task continuation did not persist exactly one linked reply"
                            )
                        payload = linked[0].payload
                        task = payload.get("agent_task") or {}
                        if (
                            payload.get("agent_action") != "SCHEDULE_PLAN"
                            or not payload.get("schedule_suggestions")
                            or task.get("goal") != "把已选收藏地点安排进一天行程"
                            or content not in task.get("answers", [])
                            or task.get("status") not in {"PROPOSED", "COMPLETED"}
                        ):
                            raise AssertionError(
                                "retried answer was not applied to the persisted schedule task; "
                                f"action={payload.get('agent_action')!r}, task={task!r}"
                            )
                    elif phase == "SERVICE_COMPOUND_RETRY":
                        assistant = next(
                            (
                                item for item in reversed(getattr(result, "messages", []))
                                if item.role == "assistant"
                            ),
                            None,
                        )
                        payload = assistant.payload if assistant else {}
                        forecast = payload.get("weather_forecast") or {}
                        if not payload.get("transport_route") or forecast.get("status") != "AVAILABLE":
                            raise AssertionError(
                                "compound retry did not return both route and weather evidence"
                            )
                        if recovered_stage == "ROUTE" and maps.route_calls:
                            raise AssertionError("recovered route was queried again")
                        if recovered_stage != "ROUTE" and len(maps.route_calls) != 1:
                            raise AssertionError("remaining route stage did not run exactly once")
                        if recovered_stage == "SEARCH_WEATHER" and weather.forecast_calls:
                            raise AssertionError("recovered forecast was queried again")
                        if recovered_stage != "SEARCH_WEATHER" and weather.forecast_calls != 1:
                            raise AssertionError("remaining weather stage did not run exactly once")
                    elif phase == "SERVICE_COMPOUND_ROUTE_FAILURE_RETRY":
                        assistant = next(
                            (
                                item for item in reversed(getattr(result, "messages", []))
                                if item.role == "assistant"
                            ),
                            None,
                        )
                        payload = assistant.payload if assistant else {}
                        if (
                            payload.get("transport_route")
                            or (payload.get("weather_forecast") or {}).get("status") != "AVAILABLE"
                            or weather.forecast_calls != 0
                            or len(maps.route_calls) != 1
                        ):
                            raise AssertionError(
                                "partial recovery must reuse weather, attempt route once, and not claim a route"
                            )
                        reply = assistant.content if assistant else ""
                        if "路线" not in reply or "直线距离" not in reply:
                            raise AssertionError(
                                "route-provider failure was not clearly surfaced alongside recovered weather: "
                                f"{reply[:500]!r}"
                            )
                    elif phase == "SERVICE_COMPOUND_WEATHER_FAILURE_RETRY":
                        assistant = next(
                            (
                                item for item in reversed(getattr(result, "messages", []))
                                if item.role == "assistant"
                            ),
                            None,
                        )
                        payload = assistant.payload if assistant else {}
                        if (
                            not payload.get("transport_route")
                            or payload.get("weather_forecast")
                            or maps.route_calls
                            or weather.forecast_calls != 1
                        ):
                            raise AssertionError(
                                "partial recovery must reuse route, attempt weather once, and omit forecast"
                            )
                        reply = assistant.content if assistant else ""
                        if "路线" not in reply or "天气服务暂时不可用" not in reply:
                            raise AssertionError(
                                "recovered route and weather-provider outage were not both explained"
                            )
                    elif phase == "SERVICE_MIXED_RETRY":
                        candidates = getattr(result, "candidates", None)
                        provider_ids = {
                            item.get("provider_id")
                            for item in candidates or []
                            if isinstance(item, dict)
                        }
                        if not {"fake-restaurant", "fake-panda"} <= provider_ids:
                            raise AssertionError(
                                "mixed retry did not combine recovered meal and newly searched attraction evidence"
                            )
                        if retry_maps is None or retry_maps.retried_queries != ["景点"]:
                            raise AssertionError(
                                f"mixed retry should query only the remaining attraction category: "
                                f"{getattr(retry_maps, 'retried_queries', None)}"
                            )
                    elif phase == "SERVICE_MIXED_SEARCH_FAILURE_RETRY":
                        candidates = getattr(result, "candidates", None)
                        provider_ids = {
                            item.get("provider_id")
                            for item in candidates or []
                            if isinstance(item, dict)
                        }
                        if provider_ids != {"fake-restaurant"}:
                            raise AssertionError(
                                "mixed partial failure must preserve only the recovered meal candidate; "
                                f"provider_ids={provider_ids!r}"
                            )
                        if retry_maps is None or retry_maps.failed_queries != ["景点"]:
                            raise AssertionError(
                                "retry must skip the completed meal query and fail the remaining attraction query"
                            )
                        assistant = next(
                            (item for item in reversed(getattr(result, "messages", []))
                             if item.role == "assistant"),
                            None,
                        )
                        transitions = (assistant.payload.get("agent_trace") or {}).get("transitions", []) if assistant else []
                        if (
                            assistant is None
                            or "部分地图查询暂时失败" not in assistant.content
                            or not any(
                                isinstance(item, dict)
                                and item.get("status") == "FAILED"
                                and item.get("error") == "MAPS_PROVIDER_UNAVAILABLE"
                                for item in transitions
                            )
                        ):
                            raise AssertionError(
                                "mixed search outage was not disclosed in the response and trace; "
                                f"reply={assistant.content if assistant else None!r}; "
                                f"agent_trace={(assistant.payload.get('agent_trace') or {}) if assistant else None!r}"
                            )
                    elif phase == "SERVICE_MIXED_ALL_SEARCHES_RETRY":
                        candidates = getattr(result, "candidates", None)
                        provider_ids = {
                            item.get("provider_id")
                            for item in candidates or []
                            if isinstance(item, dict)
                        }
                        if not {"fake-restaurant", "fake-panda"} <= provider_ids:
                            raise AssertionError(
                                "retry after both mixed searches did not return both cached candidate categories"
                            )
                    else:
                        candidates = getattr(result, "candidates", None)
                        if not isinstance(candidates, list) or not any(
                            item.get("provider_id") == "fake-kuanzhai"
                            for item in candidates
                            if isinstance(item, dict)
                        ):
                            raise AssertionError(
                                "real Advisor retry did not return the recovered place candidate"
                            )
                else:
                    assert result == {"recovered": True}
        finally:
            turn_service.execute_advisor_turn = original_execute
            turn_service.run_advisor_route_query = original_route_query
            turn_service.load_route_evidence_checkpoint = original_route_evidence_loader
            turn_service.run_advisor_search_loop = original_search_loop
            turn_service._extract_advisor_intent = original_extract_intent
            turn_service.search_evidence_checkpoint_writer = original_search_writer
            turn_service.weather_evidence_checkpoint_writer = original_weather_writer
            turn_service.direct_route_checkpoint_writer = original_direct_route_writer
            turn_service.route_evidence_checkpoint_writer = original_route_evidence_writer
            turn_service.choose_stage_order = original_choose_stage_order
            advisor_messages.read_session = original_read_session


def _run_fresh_process_phase(
    *, phase: str, owner_id, session_id, key: str, content: str,
    recovered_stage: str | None = None,
) -> None:
    env = dict(os.environ)
    env.update({"LLM_API_KEY": "", "LLM_BASE_URL": "", "MAPS_PROVIDER": "fake"})
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--retry-phase",
        phase,
        "--owner-id",
        str(owner_id),
        "--session-id",
        str(session_id),
        "--idempotency-key",
        key,
        "--content",
        content,
    ]
    if recovered_stage:
        command.extend(("--recovered-stage", recovered_stage))
    result = subprocess.run(
        command,
        check=False,
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    expected_return_code = {
        "INTERRUPT": 73,
        "SEARCH_CHECKPOINT_INTERRUPT": 74,
        "WEATHER_CHECKPOINT_INTERRUPT": 74,
        "ROUTE_CHECKPOINT_INTERRUPT": 74,
        "DIRECT_ROUTE_CHECKPOINT_INTERRUPT": 74,
        "DECISION_CHECKPOINT_INTERRUPT": 74,
        "SERVICE_RECOVERY_INTERRUPT": 75,
        "SERVICE_MIXED_INTERRUPT": 75,
        "SERVICE_MIXED_ALL_SEARCHES_INTERRUPT": 81,
        "SERVICE_WEATHER_INTERRUPT": 76,
        "SERVICE_ROUTE_INTERRUPT": 77,
        "SERVICE_SCHEDULE_INTERRUPT": 78,
        "SERVICE_SEARCH_SCHEDULE_INTERRUPT": 78,
        "SERVICE_REPLY_COMMIT_INTERRUPT": 79,
        "SERVICE_PENDING_ANSWER_INTERRUPT": 82,
        "SERVICE_COMPOUND_INTERRUPT": 80,
        "SERVICE_COMPOUND_ROUTE_FIRST_INTERRUPT": 80,
        "SERVICE_COMPOUND_WEATHER_FIRST_INTERRUPT": 80,
    }.get(phase, 0)
    if result.returncode != expected_return_code:
        raise RuntimeError(
            f"fresh {phase} process failed ({result.returncode})\n"
            f"stdout-start: {result.stdout[:2000]}\nstdout-end: {result.stdout[-1000:]}\n"
            f"stderr: {result.stderr[-4000:]}"
        )
    if result.stdout.strip():
        print(result.stdout.strip())


async def _run_concurrent_processes(
    *,
    owner_id,
    session_id,
    requests: list[tuple[str, str]],
    phase: str = "CONCURRENT",
) -> None:
    env = dict(os.environ)
    env.update({"LLM_API_KEY": "", "LLM_BASE_URL": "", "MAPS_PROVIDER": "fake"})

    async def launch(key: str, content: str):
        command = [
            sys.executable,
            str(Path(__file__).resolve()),
            "--retry-phase",
            phase,
            "--owner-id",
            str(owner_id),
            "--session-id",
            str(session_id),
            "--idempotency-key",
            key,
            "--content",
            content,
        ]
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=Path(__file__).resolve().parents[1],
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=60)
        except TimeoutError:
            process.kill()
            await process.wait()
            raise
        return process.returncode, stdout.decode(errors="replace"), stderr.decode(errors="replace")

    results = await asyncio.gather(*(launch(key, content) for key, content in requests))
    for return_code, stdout, stderr in results:
        if return_code != 0:
            raise RuntimeError(
                f"concurrent API process failed ({return_code})\n"
                f"stdout: {stdout[-2000:]}\nstderr: {stderr[-4000:]}"
            )
        if stdout.strip():
            print(stdout.strip())


async def verify() -> None:
    user_id, session_id = uuid4(), uuid4()
    idempotency_key = f"durable-retry-{uuid4().hex[:20]}"
    evidence_task_id = f"destination:{idempotency_key}"
    expected_source = "AMap Web Service"
    scope_ref = search_scope_ref("Chengdu", "park", 4, expected_source)
    now = datetime.now(UTC)
    temporary_poi_ids = []
    try:
        async with AsyncSessionFactory() as db:
            db.add(
                UserModel(
                    id=user_id,
                    email=f"{idempotency_key}@example.invalid",
                    username=f"r{user_id.hex[:23]}",
                    password_hash="integration-test-only",
                    display_name="",
                    home_city="",
                    bio="",
                    is_active=True,
                    email_verified=True,
                    created_at=now,
                )
            )
            await db.commit()
        async with AsyncSessionFactory() as db:
            db.add(
                AdvisorSessionModel(
                    id=session_id,
                    owner_id=user_id,
                    mode="discover",
                    destination="",
                    pace_level=3,
                    preference_text="",
                    candidates=[],
                    context={},
                    status="ACTIVE",
                    created_at=now,
                    updated_at=now,
                )
            )
            await db.commit()

        # Keep the same lock used by the request endpoint while the independent
        # transaction writes the message, then roll back the simulated turn.
        async with AsyncSessionFactory() as turn_db:
            await turn_db.scalar(
                select(AdvisorSessionModel)
                .where(
                    AdvisorSessionModel.id == session_id,
                    AdvisorSessionModel.owner_id == user_id,
                )
                .with_for_update(key_share=True)
            )
            await asyncio.wait_for(
                _persist_idempotent_user_message(
                    session_id=session_id,
                    owner_id=user_id,
                    idempotency_key=idempotency_key,
                    content="模拟中断后可恢复的顾问请求",
                ),
                timeout=5,
            )
            await turn_db.rollback()

        async with AsyncSessionFactory() as db:
            persisted = await db.scalar(
                select(AdvisorMessageModel).where(
                    AdvisorMessageModel.session_id == session_id,
                    AdvisorMessageModel.owner_id == user_id,
                    AdvisorMessageModel.idempotency_key == idempotency_key,
                )
            )
            assert persisted is not None
            assert persisted.content == "模拟中断后可恢复的顾问请求"
        print(
            "PASS: idempotent user input survived the turn transaction rollback under the session write lock."
        )

        _run_fresh_process_phase(
            phase="SEARCH_CHECKPOINT_INTERRUPT",
            owner_id=user_id,
            session_id=session_id,
            key=idempotency_key,
            content="模拟地图观察持久化后进程中断",
        )
        async with AsyncSessionFactory() as db:
            restored = await load_search_evidence_checkpoint(
                db,
                owner_id=user_id,
                session_id=session_id,
                task_id=evidence_task_id,
                scope_ref=scope_ref,
                expected_source=expected_source,
            )
            assert restored is not None
            assert restored["status"] == "VERIFIED"
            assert restored["places"][0]["provider_id"] == "integration-poi-1"
            assert datetime.fromisoformat(restored["retrieved_at"]) <= datetime.now(UTC)
            wrong_scope = search_scope_ref("Chengdu", "museum", 5, expected_source)
            assert (
                await load_search_evidence_checkpoint(
                    db,
                    owner_id=user_id,
                    session_id=session_id,
                    task_id=evidence_task_id,
                    scope_ref=wrong_scope,
                    expected_source=expected_source,
                )
                is None
            )
            assert (
                await load_search_evidence_checkpoint(
                    db,
                    owner_id=uuid4(),
                    session_id=session_id,
                    task_id=evidence_task_id,
                    scope_ref=scope_ref,
                    expected_source=expected_source,
                )
                is None
            )
            row = await db.scalar(
                select(AdvisorRunEventModel).where(
                    AdvisorRunEventModel.owner_id == user_id,
                    AdvisorRunEventModel.session_id == session_id,
                    AdvisorRunEventModel.phase == "search_recovery",
                    AdvisorRunEventModel.operation == "map_search_snapshot",
                )
            )
            assert row is not None
            assert row.metadata_json["task_ref"] != evidence_task_id
            assert set(row.metadata_json) == {"task_ref", "scope_ref", "search"}
            assert not {"task_id", "city", "keyword", "user_goal"} & row.metadata_json.keys()
        print(
            "PASS: verified map evidence survived abrupt process exit after checkpoint commit and was rejected for mismatched scope or owner."
        )

        weather_scope = weather_scope_ref(
            "成都",
            "2026-10-20",
            "2026-10-21",
            4,
            "Open-Meteo",
        )
        _run_fresh_process_phase(
            phase="WEATHER_CHECKPOINT_INTERRUPT",
            owner_id=user_id,
            session_id=session_id,
            key=idempotency_key,
            content="测试天气检查点中断",
        )
        async with AsyncSessionFactory() as db:
            restored_weather = await load_weather_evidence_checkpoint(
                db,
                owner_id=user_id,
                session_id=session_id,
                task_id=f"weather:{idempotency_key}",
                scope_ref=weather_scope,
                city="成都",
                expected_source="Open-Meteo",
            )
            assert restored_weather is not None
            assert restored_weather["days"][0]["weather_code"] == 2
            changed_weather_scope = weather_scope_ref(
                "成都",
                "2026-10-21",
                "2026-10-22",
                4,
                "Open-Meteo",
            )
            assert (
                await load_weather_evidence_checkpoint(
                    db,
                    owner_id=user_id,
                    session_id=session_id,
                    task_id=f"weather:{idempotency_key}",
                    scope_ref=changed_weather_scope,
                    city="成都",
                    expected_source="Open-Meteo",
                )
                is None
            )
        print(
            "PASS: weather evidence survived abrupt process exit and was rejected for a changed forecast date scope."
        )

        route_candidate_ids = ["candidate-a", "candidate-b"]
        _run_fresh_process_phase(
            phase="ROUTE_CHECKPOINT_INTERRUPT",
            owner_id=user_id,
            session_id=session_id,
            key=idempotency_key,
            content="测试路线矩阵检查点中断",
        )
        async with AsyncSessionFactory() as db:
            restored_route = await load_route_evidence_checkpoint(
                db,
                owner_id=user_id,
                session_id=session_id,
                task_id=f"schedule:{idempotency_key}",
                candidate_ids=route_candidate_ids,
                context_revision=6,
                expected_source="AMap Web Service",
            )
            assert restored_route is not None
            assert restored_route["durations_min"] == [[0, 18], [21, 0]]
            assert (
                await load_route_evidence_checkpoint(
                    db,
                    owner_id=user_id,
                    session_id=session_id,
                    task_id=f"schedule:{idempotency_key}",
                    candidate_ids=route_candidate_ids,
                    context_revision=7,
                    expected_source="AMap Web Service",
                )
                is None
            )
        print(
            "PASS: verified route matrix survived abrupt process exit and was rejected for a changed context revision."
        )

        route_goal = "从宽窄巷子到熊猫基地查驾车路线"
        direct_route_ids = ["place-a", "place-b"]
        direct_route_source = "Local simulated maps"
        direct_route_scope = direct_route_scope_ref(
            route_goal,
            direct_route_ids,
            7,
            direct_route_source,
        )
        _run_fresh_process_phase(
            phase="DIRECT_ROUTE_CHECKPOINT_INTERRUPT",
            owner_id=user_id,
            session_id=session_id,
            key=idempotency_key,
            content=route_goal,
        )
        async with AsyncSessionFactory() as db:
            restored_direct_route = await load_direct_route_checkpoint(
                db,
                owner_id=user_id,
                session_id=session_id,
                task_id=f"direct-route:{idempotency_key}",
                scope_ref=direct_route_scope,
                expected_source=direct_route_source,
            )
            assert restored_direct_route is not None
            assert restored_direct_route["polyline"] == [
                [30.6, 104.0],
                [30.65, 104.05],
                [30.7, 104.1],
            ]
            changed_direct_route_scope = direct_route_scope_ref(
                route_goal,
                ["place-a", "place-c"],
                7,
                direct_route_source,
            )
            assert (
                await load_direct_route_checkpoint(
                    db,
                    owner_id=user_id,
                    session_id=session_id,
                    task_id=f"direct-route:{idempotency_key}",
                    scope_ref=changed_direct_route_scope,
                    expected_source=direct_route_source,
                )
                is None
            )
        print(
            "PASS: direct-route geometry survived abrupt process exit and was rejected for changed endpoint scope."
        )

        decision_tool = "search_places"
        decision_action_ref = readonly_action_ref(
            decision_tool,
            {"keyword": "park", "city": "Chengdu"},
        )
        decision_observation_ref = stage_observation_ref(
            {
                "goal_ref": "synthetic-goal",
                "evidence": [],
                "context_revision": 8,
            }
        )
        decision_approved_actions = {decision_action_ref: decision_tool}
        _run_fresh_process_phase(
            phase="DECISION_CHECKPOINT_INTERRUPT",
            owner_id=user_id,
            session_id=session_id,
            key=idempotency_key,
            content="测试模型决策检查点中断",
        )
        async with AsyncSessionFactory() as db:
            restored_decision = await load_readonly_tool_decision(
                db,
                owner_id=user_id,
                session_id=session_id,
                task_id=f"decision:{idempotency_key}",
                context_revision=8,
                observation_ref=decision_observation_ref,
                decision_number=1,
                approved_actions=decision_approved_actions,
            )
            assert restored_decision == {
                "tool": decision_tool,
                "action_ref": decision_action_ref,
            }
            assert (
                await load_readonly_tool_decision(
                    db,
                    owner_id=user_id,
                    session_id=session_id,
                    task_id=f"decision:{idempotency_key}",
                    context_revision=8,
                    observation_ref="0" * 64,
                    decision_number=1,
                    approved_actions=decision_approved_actions,
                )
                is None
            )
            assert (
                await load_readonly_tool_decision(
                    db,
                    owner_id=user_id,
                    session_id=session_id,
                    task_id=f"decision:{idempotency_key}",
                    context_revision=8,
                    observation_ref=decision_observation_ref,
                    decision_number=1,
                    approved_actions={decision_action_ref: "get_trip_weather"},
                )
                is None
            )
        print(
            "PASS: approved tool decision survived abrupt process exit and was rejected for changed observation or allowlist."
        )

        recovery_session_id = uuid4()
        recovery_key = f"service-recovery-{uuid4().hex[:20]}"
        recovery_content = "我想去宽窄巷子"
        async with AsyncSessionFactory() as db:
            db.add(
                AdvisorSessionModel(
                    id=recovery_session_id,
                    owner_id=user_id,
                    mode="discover",
                    destination="成都",
                    pace_level=3,
                    preference_text="",
                    candidates=[],
                    context={},
                    status="ACTIVE",
                    created_at=now,
                    updated_at=now,
                )
            )
            await db.commit()
        async with AsyncSessionFactory() as db:
            await _persist_idempotent_user_message(
                session_id=recovery_session_id,
                owner_id=user_id,
                idempotency_key=recovery_key,
                content=recovery_content,
            )
        _run_fresh_process_phase(
            phase="SERVICE_RECOVERY_INTERRUPT",
            owner_id=user_id,
            session_id=recovery_session_id,
            key=recovery_key,
            content=recovery_content,
        )
        async with AsyncSessionFactory() as db:
            interrupted_messages = (
                await db.scalars(
                    select(AdvisorMessageModel).where(
                        AdvisorMessageModel.session_id == recovery_session_id,
                        AdvisorMessageModel.owner_id == user_id,
                    )
                )
            ).all()
            interrupted_users = [item for item in interrupted_messages if item.role == "user"]
            assert len(interrupted_users) == 1
            evidence_rows = (
                await db.scalars(
                    select(AdvisorRunEventModel).where(
                        AdvisorRunEventModel.owner_id == user_id,
                        AdvisorRunEventModel.session_id == recovery_session_id,
                        AdvisorRunEventModel.phase == "search_recovery",
                        AdvisorRunEventModel.operation == "map_search_snapshot",
                    )
                )
            ).all()
            assert len(evidence_rows) == 1
            assert (
                evidence_rows[0].metadata_json["search"]["places"][0]["provider_id"]
                == "fake-kuanzhai"
            )
        _run_fresh_process_phase(
            phase="SERVICE_RECOVERY_RETRY",
            owner_id=user_id,
            session_id=recovery_session_id,
            key=recovery_key,
            content=recovery_content,
        )
        async with AsyncSessionFactory() as db:
            recovered_messages = (
                await db.scalars(
                    select(AdvisorMessageModel).where(
                        AdvisorMessageModel.session_id == recovery_session_id,
                        AdvisorMessageModel.owner_id == user_id,
                    )
                )
            ).all()
            assert sum(item.role == "user" for item in recovered_messages) == 1
            assert sum(item.role == "assistant" for item in recovered_messages) == 1
        print(
            "PASS: real Advisor service resumed after a committed place-search checkpoint without repeating the provider call or duplicating messages."
        )

        committed_session_id = uuid4()
        committed_key = f"service-committed-{uuid4().hex[:18]}"
        committed_content = "我想去宽窄巷子"
        async with AsyncSessionFactory() as db:
            db.add(AdvisorSessionModel(
                id=committed_session_id, owner_id=user_id, mode="discover",
                destination="成都", pace_level=3, preference_text="",
                candidates=[], context={}, status="ACTIVE",
                created_at=now, updated_at=now,
            ))
            await db.commit()
        async with AsyncSessionFactory() as db:
            await _persist_idempotent_user_message(
                session_id=committed_session_id, owner_id=user_id,
                idempotency_key=committed_key, content=committed_content,
            )
        _run_fresh_process_phase(
            phase="SERVICE_REPLY_COMMIT_INTERRUPT", owner_id=user_id,
            session_id=committed_session_id, key=committed_key, content=committed_content,
        )
        async with AsyncSessionFactory() as db:
            committed_messages = (
                await db.scalars(select(AdvisorMessageModel).where(
                    AdvisorMessageModel.session_id == committed_session_id,
                    AdvisorMessageModel.owner_id == user_id,
                ))
            ).all()
            assert sum(item.role == "user" for item in committed_messages) == 1
            assert sum(item.role == "assistant" for item in committed_messages) == 1
        _run_fresh_process_phase(
            phase="SERVICE_REPLY_COMMIT_RETRY", owner_id=user_id,
            session_id=committed_session_id, key=committed_key, content=committed_content,
        )
        async with AsyncSessionFactory() as db:
            after_commit_retry = (
                await db.scalars(select(AdvisorMessageModel).where(
                    AdvisorMessageModel.session_id == committed_session_id,
                    AdvisorMessageModel.owner_id == user_id,
                ))
            ).all()
            assert sum(item.role == "user" for item in after_commit_retry) == 1
            assert sum(item.role == "assistant" for item in after_commit_retry) == 1
        print(
            "PASS: retry after an abrupt exit following assistant-response commit returned the committed response without re-running place search or duplicating messages."
        )

        mixed_session_id = uuid4()
        mixed_key = f"service-mixed-{uuid4().hex[:20]}"
        mixed_content = "帮我找几个适合游览的景点和午餐候选，我选好后再决定时间。"
        async with AsyncSessionFactory() as db:
            db.add(
                AdvisorSessionModel(
                    id=mixed_session_id,
                    owner_id=user_id,
                    mode="existing",
                    destination="成都",
                    start_date=date(2026, 11, 18),
                    end_date=date(2026, 11, 18),
                    pace_level=3,
                    preference_text="",
                    candidates=[],
                    context={},
                    status="ACTIVE",
                    created_at=now,
                    updated_at=now,
                )
            )
            await db.commit()
        async with AsyncSessionFactory() as db:
            await _persist_idempotent_user_message(
                session_id=mixed_session_id,
                owner_id=user_id,
                idempotency_key=mixed_key,
                content=mixed_content,
            )
        _run_fresh_process_phase(
            phase="SERVICE_MIXED_INTERRUPT",
            owner_id=user_id,
            session_id=mixed_session_id,
            key=mixed_key,
            content=mixed_content,
        )
        async with AsyncSessionFactory() as db:
            interrupted_mixed_messages = (
                await db.scalars(
                    select(AdvisorMessageModel).where(
                        AdvisorMessageModel.session_id == mixed_session_id,
                        AdvisorMessageModel.owner_id == user_id,
                    )
                )
            ).all()
            assert sum(item.role == "user" for item in interrupted_mixed_messages) == 1
            assert sum(item.role == "assistant" for item in interrupted_mixed_messages) == 0
            mixed_checkpoints = (
                await db.scalars(
                    select(AdvisorRunEventModel).where(
                        AdvisorRunEventModel.owner_id == user_id,
                        AdvisorRunEventModel.session_id == mixed_session_id,
                        AdvisorRunEventModel.phase == "search_recovery",
                        AdvisorRunEventModel.operation == "map_search_snapshot",
                    )
                )
            ).all()
            assert len(mixed_checkpoints) == 1
            assert (
                mixed_checkpoints[0].metadata_json["search"]["places"][0]["provider_id"]
                == "fake-restaurant"
            )
        _run_fresh_process_phase(
            phase="SERVICE_MIXED_RETRY",
            owner_id=user_id,
            session_id=mixed_session_id,
            key=mixed_key,
            content=mixed_content,
        )
        async with AsyncSessionFactory() as db:
            recovered_mixed_messages = (
                await db.scalars(
                    select(AdvisorMessageModel).where(
                        AdvisorMessageModel.session_id == mixed_session_id,
                        AdvisorMessageModel.owner_id == user_id,
                    )
                )
            ).all()
            assert sum(item.role == "user" for item in recovered_mixed_messages) == 1
            mixed_replies = [item for item in recovered_mixed_messages if item.role == "assistant"]
            assert len(mixed_replies) == 1
            assert mixed_replies[0].payload["response_to_idempotency_key"] == mixed_key
            completed_mixed_checkpoints = (
                await db.scalars(
                    select(AdvisorRunEventModel).where(
                        AdvisorRunEventModel.owner_id == user_id,
                        AdvisorRunEventModel.session_id == mixed_session_id,
                        AdvisorRunEventModel.phase == "search_recovery",
                        AdvisorRunEventModel.operation == "map_search_snapshot",
                    )
                )
            ).all()
            assert len(completed_mixed_checkpoints) == 2
        print(
            "PASS: mixed attraction + lunch discovery recovered the committed meal result, searched only the remaining attraction query, and persisted one linked reply."
        )

        mixed_failure_session_id = uuid4()
        mixed_failure_key = f"service-mixed-failure-{uuid4().hex[:16]}"
        mixed_failure_content = "帮我找几个适合游览的景点和午餐候选，我选好后再决定时间。"
        async with AsyncSessionFactory() as db:
            db.add(AdvisorSessionModel(
                id=mixed_failure_session_id, owner_id=user_id, mode="existing",
                destination="成都", start_date=date(2026, 11, 18),
                end_date=date(2026, 11, 18), pace_level=3, preference_text="",
                candidates=[], context={}, status="ACTIVE",
                created_at=now, updated_at=now,
            ))
            await db.commit()
        async with AsyncSessionFactory() as db:
            await _persist_idempotent_user_message(
                session_id=mixed_failure_session_id, owner_id=user_id,
                idempotency_key=mixed_failure_key, content=mixed_failure_content,
            )
        _run_fresh_process_phase(
            phase="SERVICE_MIXED_INTERRUPT", owner_id=user_id,
            session_id=mixed_failure_session_id, key=mixed_failure_key,
            content=mixed_failure_content,
        )
        async with AsyncSessionFactory() as db:
            saved_meal = await db.scalar(select(AdvisorRunEventModel).where(
                AdvisorRunEventModel.owner_id == user_id,
                AdvisorRunEventModel.session_id == mixed_failure_session_id,
                AdvisorRunEventModel.phase == "search_recovery",
                AdvisorRunEventModel.operation == "map_search_snapshot",
            ))
            assert saved_meal is not None
            assert saved_meal.metadata_json["search"]["places"][0]["provider_id"] == "fake-restaurant"
        _run_fresh_process_phase(
            phase="SERVICE_MIXED_SEARCH_FAILURE_RETRY", owner_id=user_id,
            session_id=mixed_failure_session_id, key=mixed_failure_key,
            content=mixed_failure_content,
        )
        async with AsyncSessionFactory() as db:
            mixed_failure_messages = (
                await db.scalars(select(AdvisorMessageModel).where(
                    AdvisorMessageModel.session_id == mixed_failure_session_id,
                    AdvisorMessageModel.owner_id == user_id,
                ))
            ).all()
            assert sum(item.role == "user" for item in mixed_failure_messages) == 1
            failure_replies = [item for item in mixed_failure_messages if item.role == "assistant"]
            assert len(failure_replies) == 1
            assert failure_replies[0].payload["response_to_idempotency_key"] == mixed_failure_key
            assert failure_replies[0].payload.get("agent_task", {}).get("status") == "PARTIAL"
        print(
            "PASS: mixed-search retry reused the committed lunch candidate, surfaced the remaining attraction outage, and saved one linked partial-result reply."
        )

        fully_searched_session_id = uuid4()
        fully_searched_key = f"service-mixed-complete-{uuid4().hex[:16]}"
        fully_searched_content = "帮我找几个适合游览的景点和午餐候选，我选好后再决定时间。"
        async with AsyncSessionFactory() as db:
            db.add(AdvisorSessionModel(
                id=fully_searched_session_id,
                owner_id=user_id,
                mode="existing",
                destination="成都",
                start_date=date(2026, 11, 18),
                end_date=date(2026, 11, 18),
                pace_level=3,
                preference_text="",
                candidates=[],
                context={},
                status="ACTIVE",
                created_at=now,
                updated_at=now,
            ))
            await db.commit()
        async with AsyncSessionFactory() as db:
            await _persist_idempotent_user_message(
                session_id=fully_searched_session_id,
                owner_id=user_id,
                idempotency_key=fully_searched_key,
                content=fully_searched_content,
            )
        _run_fresh_process_phase(
            phase="SERVICE_MIXED_ALL_SEARCHES_INTERRUPT",
            owner_id=user_id,
            session_id=fully_searched_session_id,
            key=fully_searched_key,
            content=fully_searched_content,
        )
        async with AsyncSessionFactory() as db:
            saved_searches = (
                await db.scalars(select(AdvisorRunEventModel).where(
                    AdvisorRunEventModel.owner_id == user_id,
                    AdvisorRunEventModel.session_id == fully_searched_session_id,
                    AdvisorRunEventModel.phase == "search_recovery",
                    AdvisorRunEventModel.operation == "map_search_snapshot",
                ))
            ).all()
            assert len(saved_searches) == 2
            saved_provider_ids = {
                place["provider_id"]
                for event in saved_searches
                for place in event.metadata_json["search"]["places"]
            }
            assert {"fake-restaurant", "fake-panda"} <= saved_provider_ids
        _run_fresh_process_phase(
            phase="SERVICE_MIXED_ALL_SEARCHES_RETRY",
            owner_id=user_id,
            session_id=fully_searched_session_id,
            key=fully_searched_key,
            content=fully_searched_content,
        )
        async with AsyncSessionFactory() as db:
            fully_recovered_messages = (
                await db.scalars(select(AdvisorMessageModel).where(
                    AdvisorMessageModel.session_id == fully_searched_session_id,
                    AdvisorMessageModel.owner_id == user_id,
                ))
            ).all()
            assert sum(item.role == "user" for item in fully_recovered_messages) == 1
            fully_recovered_replies = [
                item for item in fully_recovered_messages if item.role == "assistant"
            ]
            assert len(fully_recovered_replies) == 1
            assert (
                fully_recovered_replies[0].payload["response_to_idempotency_key"]
                == fully_searched_key
            )
        print(
            "PASS: mixed attraction + lunch recovery after both searches reused both validated checkpoints, made no map calls, and persisted one linked reply."
        )

        weather_session_id = uuid4()
        weather_key = f"service-weather-{uuid4().hex[:20]}"
        weather_content = "查一下旅行天气，我需要带伞吗？"
        async with AsyncSessionFactory() as db:
            db.add(
                AdvisorSessionModel(
                    id=weather_session_id,
                    owner_id=user_id,
                    mode="discover",
                    destination="成都",
                    start_date=date(2026, 10, 20),
                    end_date=date(2026, 10, 21),
                    pace_level=3,
                    preference_text="",
                    candidates=[],
                    context={},
                    status="ACTIVE",
                    created_at=now,
                    updated_at=now,
                )
            )
            await db.commit()
        async with AsyncSessionFactory() as db:
            await _persist_idempotent_user_message(
                session_id=weather_session_id,
                owner_id=user_id,
                idempotency_key=weather_key,
                content=weather_content,
            )
        _run_fresh_process_phase(
            phase="SERVICE_WEATHER_INTERRUPT",
            owner_id=user_id,
            session_id=weather_session_id,
            key=weather_key,
            content=weather_content,
        )
        async with AsyncSessionFactory() as db:
            weather_messages = (
                await db.scalars(
                    select(AdvisorMessageModel).where(
                        AdvisorMessageModel.session_id == weather_session_id,
                        AdvisorMessageModel.owner_id == user_id,
                    )
                )
            ).all()
            assert sum(item.role == "user" for item in weather_messages) == 1
            weather_rows = (
                await db.scalars(
                    select(AdvisorRunEventModel).where(
                        AdvisorRunEventModel.owner_id == user_id,
                        AdvisorRunEventModel.session_id == weather_session_id,
                        AdvisorRunEventModel.phase == "search_weather_recovery",
                        AdvisorRunEventModel.operation == "weather_evidence_snapshot",
                    )
                )
            ).all()
            assert len(weather_rows) == 1
            weather_checkpoint = weather_rows[0].metadata_json
            assert set(weather_checkpoint) == {"task_ref", "scope_ref", "weather"}
            assert weather_checkpoint["weather"]["days"][0]["date"] == "2026-10-20"
            assert weather_checkpoint["weather"]["days"][0]["weather_code"] == 61
            assert weather_checkpoint["weather"]["days"][0]["precipitation_probability_max"] == 80
            assert "带伞" not in str(weather_checkpoint)
        _run_fresh_process_phase(
            phase="SERVICE_WEATHER_RETRY",
            owner_id=user_id,
            session_id=weather_session_id,
            key=weather_key,
            content=weather_content,
        )
        async with AsyncSessionFactory() as db:
            recovered_weather_messages = (
                await db.scalars(
                    select(AdvisorMessageModel).where(
                        AdvisorMessageModel.session_id == weather_session_id,
                        AdvisorMessageModel.owner_id == user_id,
                    )
                )
            ).all()
            assert sum(item.role == "user" for item in recovered_weather_messages) == 1
            assert sum(item.role == "assistant" for item in recovered_weather_messages) == 1
            linked_reply = next(
                item for item in recovered_weather_messages if item.role == "assistant"
            )
            assert linked_reply.payload["response_to_idempotency_key"] == weather_key
        print(
            "PASS: real Advisor weather request resumed from its committed forecast checkpoint without repeating the weather provider call or duplicating messages."
        )

        route_trip_id = uuid4()
        route_session_id = uuid4()
        route_key = f"service-route-{uuid4().hex[:20]}"
        route_content = "从宽窄巷子到成都大熊猫繁育研究基地怎么走？"
        kuanzhai_id, panda_id = uuid4(), uuid4()
        temporary_poi_ids.extend([kuanzhai_id, panda_id])
        kuanzhai_candidate_id, panda_candidate_id = uuid4(), uuid4()
        async with AsyncSessionFactory() as db:
            db.add(TripModel(
                id=route_trip_id, owner_id=user_id, destination="成都",
                start_date=date(2026, 10, 20), end_date=date(2026, 10, 21),
                pace_level=3, status="ACTIVE", created_at=now, updated_at=now,
            ))
            await db.commit()
        async with AsyncSessionFactory() as db:
            db.add_all([
                PoiModel(
                    id=kuanzhai_id, name="宽窄巷子", category="ATTRACTION",
                    address="成都市青羊区", latitude=30.6633, longitude=104.0647,
                    suggested_duration_min=120, provider="fake", provider_id="fake-kuanzhai",
                ),
                PoiModel(
                    id=panda_id, name="成都大熊猫繁育研究基地", category="ATTRACTION",
                    address="成都市成华区", latitude=30.7337, longitude=104.1477,
                    suggested_duration_min=180, provider="fake", provider_id="fake-panda",
                ),
            ])
            await db.commit()
        async with AsyncSessionFactory() as db:
            db.add_all([
                WishlistItemModel(trip_id=route_trip_id, poi_id=kuanzhai_id, priority=1),
                WishlistItemModel(trip_id=route_trip_id, poi_id=panda_id, priority=2),
                AdvisorSessionModel(
                    id=route_session_id, owner_id=user_id, trip_id=route_trip_id,
                    mode="existing", destination="成都",
                    start_date=date(2026, 10, 20), end_date=date(2026, 10, 21),
                    pace_level=3, preference_text="",
                    candidates=[
                        {"id": str(kuanzhai_candidate_id), "existing_poi_id": str(kuanzhai_id),
                         "name": "宽窄巷子", "latitude": 30.6633, "longitude": 104.0647},
                        {"id": str(panda_candidate_id), "existing_poi_id": str(panda_id),
                         "name": "成都大熊猫繁育研究基地", "latitude": 30.7337, "longitude": 104.1477},
                    ],
                    context={"revision": 0, "selected_candidate_ids": [], "current_plan": None},
                    status="ACTIVE", created_at=now, updated_at=now,
                ),
            ])
            await db.commit()
        async with AsyncSessionFactory() as db:
            await _persist_idempotent_user_message(
                session_id=route_session_id, owner_id=user_id,
                idempotency_key=route_key, content=route_content,
            )
        _run_fresh_process_phase(
            phase="SERVICE_ROUTE_INTERRUPT", owner_id=user_id,
            session_id=route_session_id, key=route_key, content=route_content,
        )
        async with AsyncSessionFactory() as db:
            route_rows = (
                await db.scalars(select(AdvisorRunEventModel).where(
                    AdvisorRunEventModel.owner_id == user_id,
                    AdvisorRunEventModel.session_id == route_session_id,
                    AdvisorRunEventModel.phase == "route_recovery",
                    AdvisorRunEventModel.operation == "direct_route_snapshot",
                ))
            ).all()
            assert len(route_rows) == 1
            route_snapshot = route_rows[0].metadata_json["route"]
            assert route_snapshot["origin_place_id"] == str(kuanzhai_candidate_id)
            assert route_snapshot["destination_place_id"] == str(panda_candidate_id)
            assert route_snapshot["status"] == "SIMULATED"
        _run_fresh_process_phase(
            phase="SERVICE_ROUTE_RETRY", owner_id=user_id,
            session_id=route_session_id, key=route_key, content=route_content,
        )
        async with AsyncSessionFactory() as db:
            route_messages = (
                await db.scalars(select(AdvisorMessageModel).where(
                    AdvisorMessageModel.session_id == route_session_id,
                    AdvisorMessageModel.owner_id == user_id,
                ))
            ).all()
            assert sum(item.role == "user" for item in route_messages) == 1
            assert sum(item.role == "assistant" for item in route_messages) == 1
            route_reply = next(item for item in route_messages if item.role == "assistant")
            assert route_reply.payload["response_to_idempotency_key"] == route_key
        print(
            "PASS: real Advisor route request resumed from its committed route checkpoint without repeating the maps provider call or duplicating messages."
        )

        schedule_session_id = uuid4()
        schedule_key = f"service-schedule-{uuid4().hex[:18]}"
        schedule_content = "请把我已收藏的宽窄巷子和成都大熊猫繁育研究基地安排成两日行程"
        schedule_candidate_ids = [str(kuanzhai_candidate_id), str(panda_candidate_id)]
        async with AsyncSessionFactory() as db:
            db.add(AdvisorSessionModel(
                id=schedule_session_id, owner_id=user_id, trip_id=route_trip_id,
                mode="existing", destination="成都",
                start_date=date(2026, 10, 20), end_date=date(2026, 10, 21),
                pace_level=3, preference_text="",
                candidates=[
                    {"id": str(kuanzhai_candidate_id), "existing_poi_id": str(kuanzhai_id),
                     "name": "宽窄巷子", "latitude": 30.6633, "longitude": 104.0647},
                    {"id": str(panda_candidate_id), "existing_poi_id": str(panda_id),
                     "name": "成都大熊猫繁育研究基地", "latitude": 30.7337, "longitude": 104.1477},
                ],
                context={
                    "revision": 0, "selected_candidate_ids": schedule_candidate_ids,
                    "current_plan": None,
                },
                status="ACTIVE", created_at=now, updated_at=now,
            ))
            await db.commit()
        async with AsyncSessionFactory() as db:
            await _persist_idempotent_user_message(
                session_id=schedule_session_id, owner_id=user_id,
                idempotency_key=schedule_key, content=schedule_content,
            )
        _run_fresh_process_phase(
            phase="SERVICE_SCHEDULE_INTERRUPT", owner_id=user_id,
            session_id=schedule_session_id, key=schedule_key, content=schedule_content,
        )
        async with AsyncSessionFactory() as db:
            schedule_rows = (
                await db.scalars(select(AdvisorRunEventModel).where(
                    AdvisorRunEventModel.owner_id == user_id,
                    AdvisorRunEventModel.session_id == schedule_session_id,
                    AdvisorRunEventModel.phase == "schedule_recovery",
                    AdvisorRunEventModel.operation == "route_evidence_snapshot",
                ))
            ).all()
            assert len(schedule_rows) == 1
            schedule_snapshot = schedule_rows[0].metadata_json
            assert schedule_snapshot["candidate_ids"] == schedule_candidate_ids
            assert schedule_snapshot["evidence_status"] == "SIMULATED"
            assert len(schedule_snapshot["durations_min"]) == 2
        _run_fresh_process_phase(
            phase="SERVICE_SCHEDULE_RETRY", owner_id=user_id,
            session_id=schedule_session_id, key=schedule_key, content=schedule_content,
        )
        async with AsyncSessionFactory() as db:
            schedule_messages = (
                await db.scalars(select(AdvisorMessageModel).where(
                    AdvisorMessageModel.session_id == schedule_session_id,
                    AdvisorMessageModel.owner_id == user_id,
                ))
            ).all()
            assert sum(item.role == "user" for item in schedule_messages) == 1
            assert sum(item.role == "assistant" for item in schedule_messages) == 1
            schedule_reply = next(item for item in schedule_messages if item.role == "assistant")
            assert schedule_reply.payload["response_to_idempotency_key"] == schedule_key
        print(
            "PASS: real Advisor schedule request resumed from its committed distance-matrix checkpoint without repeating the maps provider call or duplicating messages."
        )

        search_schedule_session_id = uuid4()
        search_schedule_trip_id = uuid4()
        search_schedule_key = f"service-search-schedule-{uuid4().hex[:16]}"
        search_schedule_content = (
            "请搜索成都科学博物馆，加入旅行并安排时间，"
            "和我已收藏的宽窄巷子一起排成一天行程"
        )
        recovered_museum_candidate_id = provider_candidate_id(
            session_id=search_schedule_session_id,
            provider="fake",
            provider_id="fake-checkpoint-museum",
        )
        async with AsyncSessionFactory() as db:
            db.add(TripModel(
                id=search_schedule_trip_id,
                owner_id=user_id,
                destination="成都",
                start_date=date(2026, 10, 20),
                end_date=date(2026, 10, 20),
                pace_level=3,
                status="ACTIVE",
                created_at=now,
                updated_at=now,
            ))
            db.add(WishlistItemModel(
                trip_id=search_schedule_trip_id,
                poi_id=kuanzhai_id,
                priority=1,
            ))
            await db.commit()
        async with AsyncSessionFactory() as db:
            db.add(AdvisorSessionModel(
                id=search_schedule_session_id,
                owner_id=user_id,
                trip_id=search_schedule_trip_id,
                mode="existing",
                destination="成都",
                start_date=date(2026, 10, 20),
                end_date=date(2026, 10, 20),
                pace_level=3,
                preference_text="",
                candidates=[{
                    "id": str(kuanzhai_candidate_id),
                    "existing_poi_id": str(kuanzhai_id),
                    "name": "宽窄巷子",
                    "provider": "fake",
                    "provider_id": "fake-kuanzhai",
                    "address": "成都市青羊区",
                    "category": "ATTRACTION;景点",
                    "planning_category": "ATTRACTION",
                    "latitude": 30.6633,
                    "longitude": 104.0647,
                    "duration_min": 120,
                    "verification_status": "SIMULATED",
                }],
                context={
                    "revision": 0,
                    "selected_candidate_ids": [str(kuanzhai_candidate_id)],
                    "current_plan": None,
                },
                status="ACTIVE",
                created_at=now,
                updated_at=now,
            ))
            await db.commit()
        async with AsyncSessionFactory() as db:
            await _persist_idempotent_user_message(
                session_id=search_schedule_session_id,
                owner_id=user_id,
                idempotency_key=search_schedule_key,
                content=search_schedule_content,
            )
        _run_fresh_process_phase(
            phase="SERVICE_SEARCH_SCHEDULE_INTERRUPT",
            owner_id=user_id,
            session_id=search_schedule_session_id,
            key=search_schedule_key,
            content=search_schedule_content,
        )
        async with AsyncSessionFactory() as db:
            search_schedule_events = (
                await db.scalars(select(AdvisorRunEventModel).where(
                    AdvisorRunEventModel.owner_id == user_id,
                    AdvisorRunEventModel.session_id == search_schedule_session_id,
                    AdvisorRunEventModel.operation.in_([
                        "map_search_snapshot", "route_evidence_snapshot",
                    ]),
                ))
            ).all()
            search_events = [
                event for event in search_schedule_events
                if event.operation == "map_search_snapshot"
            ]
            matrix_events = [
                event for event in search_schedule_events
                if event.operation == "route_evidence_snapshot"
            ]
            assert len(search_events) == len(matrix_events) == 1
            matrix_snapshot = matrix_events[0].metadata_json
            assert set(matrix_snapshot["candidate_ids"]) == {
                str(kuanzhai_candidate_id), str(recovered_museum_candidate_id),
            }, matrix_snapshot["candidate_ids"]
            assert len(matrix_snapshot["durations_min"]) == 2
        _run_fresh_process_phase(
            phase="SERVICE_SEARCH_SCHEDULE_RETRY",
            owner_id=user_id,
            session_id=search_schedule_session_id,
            key=search_schedule_key,
            content=search_schedule_content,
        )
        async with AsyncSessionFactory() as db:
            search_schedule_messages = (
                await db.scalars(select(AdvisorMessageModel).where(
                    AdvisorMessageModel.session_id == search_schedule_session_id,
                    AdvisorMessageModel.owner_id == user_id,
                ))
            ).all()
            assert sum(item.role == "user" for item in search_schedule_messages) == 1
            search_schedule_replies = [
                item for item in search_schedule_messages
                if item.role == "assistant"
                and isinstance(item.payload, dict)
                and item.payload.get("response_to_idempotency_key") == search_schedule_key
            ]
            assert len(search_schedule_replies) == 1
            assert search_schedule_replies[0].payload["agent_action"] == "SCHEDULE_PLAN"
            assert search_schedule_replies[0].payload["schedule_suggestions"]
        print(
            "PASS: search-then-schedule recovery reconstructed stable candidate IDs, reused its committed matrix, and persisted one proposal."
        )

        pending_session_id = uuid4()
        pending_key = f"service-pending-answer-{uuid4().hex[:18]}"
        pending_origin_key = f"service-pending-origin-{uuid4().hex[:18]}"
        pending_answer = "请安排在2026-10-20 09:00开始"
        pending_goal = "把已选收藏地点安排进一天行程"
        pending_task_id = f"pending-task-{uuid4().hex[:12]}"
        pending_candidate = {
            "id": str(kuanzhai_candidate_id),
            "existing_poi_id": str(kuanzhai_id),
            "name": "宽窄巷子",
            "provider": "fake",
            "provider_id": "fake-kuanzhai",
            "address": "成都市青羊区",
            "category": "ATTRACTION",
            "planning_category": "ATTRACTION",
            "latitude": 30.6633,
            "longitude": 104.0647,
            "duration_min": 120,
            "verification_status": "SIMULATED",
        }
        async with AsyncSessionFactory() as db:
            db.add(AdvisorSessionModel(
                id=pending_session_id, owner_id=user_id, trip_id=route_trip_id,
                mode="existing", destination="成都",
                start_date=date(2026, 10, 20), end_date=date(2026, 10, 20),
                pace_level=3, preference_text="",
                candidates=[pending_candidate],
                context={
                    # Existing Advisor conversations have already refreshed
                    # the owner-scoped trip snapshot before asking a question.
                    "revision": 1,
                    "selected_candidate_ids": [str(kuanzhai_candidate_id)],
                    "current_plan": None,
                },
                status="ACTIVE", created_at=now, updated_at=now,
            ))
            await db.commit()
        async with AsyncSessionFactory() as db:
            db.add_all([
                AdvisorMessageModel(
                    session_id=pending_session_id, owner_id=user_id, role="user",
                    content="请帮我把选好的收藏地点安排进一天行程",
                    idempotency_key=pending_origin_key, payload=None,
                    created_at=now - timedelta(seconds=2),
                ),
                AdvisorMessageModel(
                    session_id=pending_session_id, owner_id=user_id, role="assistant",
                    content="你希望哪天几点开始游览？", idempotency_key=None,
                    payload={
                        "agent_trace": {"status": "NEEDS_USER_INPUT"},
                        "schedule_recommendation_requested": True,
                        "pending_task_type": "SCHEDULE",
                        "pending_task": pending_goal,
                        "pending_question": "你希望哪天几点开始游览？",
                        "pending_task_id": pending_task_id,
                        "pending_answers": [],
                        "pending_turn_number": 1,
                        # The original successful turn refreshes the trip
                        # snapshot and advances the revision before asking.
                        "context_revision": 2,
                    },
                    created_at=now - timedelta(seconds=1),
                ),
            ])
            await db.commit()
        async with AsyncSessionFactory() as db:
            await _persist_idempotent_user_message(
                session_id=pending_session_id, owner_id=user_id,
                idempotency_key=pending_key, content=pending_answer,
            )
        _run_fresh_process_phase(
            phase="SERVICE_PENDING_ANSWER_INTERRUPT", owner_id=user_id,
            session_id=pending_session_id, key=pending_key, content=pending_answer,
        )
        async with AsyncSessionFactory() as db:
            interrupted = (
                await db.scalars(select(AdvisorMessageModel).where(
                    AdvisorMessageModel.session_id == pending_session_id,
                    AdvisorMessageModel.owner_id == user_id,
                ).order_by(AdvisorMessageModel.created_at, AdvisorMessageModel.id))
            ).all()
            assert [item.role for item in interrupted] == ["user", "assistant", "user"]
            assert interrupted[-1].idempotency_key == pending_key
            assert interrupted[-1].content == pending_answer
        _run_fresh_process_phase(
            phase="SERVICE_PENDING_ANSWER_RETRY", owner_id=user_id,
            session_id=pending_session_id, key=pending_key, content=pending_answer,
        )
        async with AsyncSessionFactory() as db:
            recovered = (
                await db.scalars(select(AdvisorMessageModel).where(
                    AdvisorMessageModel.session_id == pending_session_id,
                    AdvisorMessageModel.owner_id == user_id,
                ).order_by(AdvisorMessageModel.created_at, AdvisorMessageModel.id))
            ).all()
            assert sum(item.role == "user" for item in recovered) == 2
            replies = [
                item for item in recovered
                if item.role == "assistant"
                and isinstance(item.payload, dict)
                and item.payload.get("response_to_idempotency_key") == pending_key
            ]
            assert len(replies) == 1
            resumed_task = replies[0].payload["agent_task"]
            assert resumed_task["goal"] == pending_goal
            assert resumed_task["answers"] == [pending_answer]
            assert replies[0].payload["agent_action"] == "SCHEDULE_PLAN"
            assert replies[0].payload["schedule_suggestions"]
        print(
            "PASS: a user answer persisted before an abrupt process exit resumed the prior schedule task, produced one proposal, and did not duplicate the answer."
        )

        compound_content = (
            "从宽窄巷子到成都大熊猫繁育研究基地查询路线，同时查一下旅行天气，我需要带伞吗？"
        )
        compound_orders = (
            (
                "ROUTE", "SERVICE_COMPOUND_ROUTE_FIRST_INTERRUPT",
                "direct_route_snapshot", "SERVICE_COMPOUND_RETRY",
            ),
            (
                "SEARCH_WEATHER", "SERVICE_COMPOUND_WEATHER_FIRST_INTERRUPT",
                "weather_evidence_snapshot", "SERVICE_COMPOUND_RETRY",
            ),
            (
                "SEARCH_WEATHER", "SERVICE_COMPOUND_WEATHER_FIRST_INTERRUPT",
                "weather_evidence_snapshot", "SERVICE_COMPOUND_ROUTE_FAILURE_RETRY",
            ),
            (
                "ROUTE", "SERVICE_COMPOUND_ROUTE_FIRST_INTERRUPT",
                "direct_route_snapshot", "SERVICE_COMPOUND_WEATHER_FAILURE_RETRY",
            ),
        )
        for forced_stage, interrupt_phase, expected_operation, retry_phase in compound_orders:
            compound_session_id = uuid4()
            compound_key = f"service-compound-{forced_stage.lower()}-{uuid4().hex[:12]}"
            async with AsyncSessionFactory() as db:
                db.add(AdvisorSessionModel(
                    id=compound_session_id, owner_id=user_id, trip_id=route_trip_id,
                    mode="existing", destination="成都",
                    start_date=date(2026, 10, 20), end_date=date(2026, 10, 21),
                    pace_level=3, preference_text="",
                    candidates=[
                        {"id": str(kuanzhai_candidate_id), "existing_poi_id": str(kuanzhai_id),
                         "name": "宽窄巷子", "latitude": 30.6633, "longitude": 104.0647},
                        {"id": str(panda_candidate_id), "existing_poi_id": str(panda_id),
                         "name": "成都大熊猫繁育研究基地", "latitude": 30.7337, "longitude": 104.1477},
                    ],
                    context={"revision": 0, "selected_candidate_ids": [], "current_plan": None},
                    status="ACTIVE", created_at=now, updated_at=now,
                ))
                await db.commit()
            async with AsyncSessionFactory() as db:
                await _persist_idempotent_user_message(
                    session_id=compound_session_id, owner_id=user_id,
                    idempotency_key=compound_key, content=compound_content,
                )
            _run_fresh_process_phase(
                phase=interrupt_phase, owner_id=user_id,
                session_id=compound_session_id, key=compound_key, content=compound_content,
            )
            async with AsyncSessionFactory() as db:
                compound_messages = (
                    await db.scalars(select(AdvisorMessageModel).where(
                        AdvisorMessageModel.session_id == compound_session_id,
                        AdvisorMessageModel.owner_id == user_id,
                    ))
                ).all()
                assert sum(item.role == "user" for item in compound_messages) == 1
                assert sum(item.role == "assistant" for item in compound_messages) == 0
                compound_events = (
                    await db.scalars(select(AdvisorRunEventModel).where(
                        AdvisorRunEventModel.owner_id == user_id,
                        AdvisorRunEventModel.session_id == compound_session_id,
                        AdvisorRunEventModel.operation.in_([
                            "weather_evidence_snapshot", "direct_route_snapshot",
                        ]),
                    ))
                ).all()
                compound_operations = {event.operation for event in compound_events}
                assert compound_operations == {expected_operation}
                recovered_stage = "SEARCH_WEATHER" if forced_stage == "SEARCH_WEATHER" else "ROUTE"
            _run_fresh_process_phase(
                phase=retry_phase, owner_id=user_id,
                session_id=compound_session_id, key=compound_key, content=compound_content,
                recovered_stage=recovered_stage,
            )
            async with AsyncSessionFactory() as db:
                compound_messages = (
                    await db.scalars(select(AdvisorMessageModel).where(
                        AdvisorMessageModel.session_id == compound_session_id,
                        AdvisorMessageModel.owner_id == user_id,
                    ))
                ).all()
                assert sum(item.role == "user" for item in compound_messages) == 1
                compound_replies = [item for item in compound_messages if item.role == "assistant"]
                assert len(compound_replies) == 1
                reply_payload = compound_replies[0].payload
                assert reply_payload["response_to_idempotency_key"] == compound_key
                if retry_phase == "SERVICE_COMPOUND_ROUTE_FAILURE_RETRY":
                    assert not reply_payload.get("transport_route")
                    assert (reply_payload.get("weather_forecast") or {}).get("status") == "AVAILABLE"
                elif retry_phase == "SERVICE_COMPOUND_WEATHER_FAILURE_RETRY":
                    assert reply_payload.get("transport_route")
                    assert not reply_payload.get("weather_forecast")
                else:
                    assert reply_payload.get("transport_route")
                    assert (reply_payload.get("weather_forecast") or {}).get("status") == "AVAILABLE"
            print(
                f"PASS: route + weather recovery after {forced_stage} completed first resumed the other stage; "
                f"retry phase={retry_phase}."
            )

        # Exercise the actual message endpoint twice with fresh DB sessions.
        # The first execution fails after the endpoint commits the user message;
        # the second invocation must find and reuse that row by idempotency key.
        retry_session_id = uuid4()
        retry_key = f"api-retry-{uuid4().hex[:24]}"
        retry_content = "验证同一请求键在新 API 调用中安全恢复"
        async with AsyncSessionFactory() as db:
            db.add(
                AdvisorSessionModel(
                    id=retry_session_id,
                    owner_id=user_id,
                    mode="discover",
                    destination="",
                    pace_level=3,
                    preference_text="",
                    candidates=[],
                    context={},
                    status="ACTIVE",
                    created_at=now,
                    updated_at=now,
                )
            )
            await db.commit()

        _run_fresh_process_phase(
            phase="INTERRUPT",
            owner_id=user_id,
            session_id=retry_session_id,
            key=retry_key,
            content=retry_content,
        )
        _run_fresh_process_phase(
            phase="RETRY",
            owner_id=user_id,
            session_id=retry_session_id,
            key=retry_key,
            content=retry_content,
        )

        async with AsyncSessionFactory() as db:
            saved = (
                await db.scalars(
                    select(AdvisorMessageModel).where(
                        AdvisorMessageModel.session_id == retry_session_id,
                        AdvisorMessageModel.owner_id == user_id,
                    )
                )
            ).all()
            assert len(saved) == 2
            user_rows = [item for item in saved if item.role == "user"]
            assistant_rows = [item for item in saved if item.role == "assistant"]
            assert len(user_rows) == len(assistant_rows) == 1
            assert user_rows[0].idempotency_key == retry_key
            assert assistant_rows[0].payload["response_to_idempotency_key"] == retry_key
        print(
            "PASS: independent API processes recovered the same request with one user row and one linked reply."
        )

        concurrent_session_id = uuid4()
        concurrent_key = f"api-race-{uuid4().hex[:24]}"
        concurrent_content = "验证两个 API 进程并发提交同一顾问请求"
        async with AsyncSessionFactory() as db:
            db.add(
                AdvisorSessionModel(
                    id=concurrent_session_id,
                    owner_id=user_id,
                    mode="discover",
                    destination="",
                    pace_level=3,
                    preference_text="",
                    candidates=[],
                    context={},
                    status="ACTIVE",
                    created_at=now,
                    updated_at=now,
                )
            )
            await db.commit()
        await _run_concurrent_processes(
            owner_id=user_id,
            session_id=concurrent_session_id,
            requests=[(concurrent_key, concurrent_content)] * 2,
        )
        async with AsyncSessionFactory() as db:
            raced_rows = (
                await db.scalars(
                    select(AdvisorMessageModel).where(
                        AdvisorMessageModel.session_id == concurrent_session_id,
                        AdvisorMessageModel.owner_id == user_id,
                    )
                )
            ).all()
            raced_users = [item for item in raced_rows if item.role == "user"]
            raced_replies = [item for item in raced_rows if item.role == "assistant"]
            assert len(raced_users) == len(raced_replies) == 1
            assert raced_users[0].idempotency_key == concurrent_key
            assert raced_users[0].content == concurrent_content
            assert raced_replies[0].payload["response_to_idempotency_key"] == concurrent_key
        print(
            "PASS: two concurrent API processes produced exactly one user message and one linked reply."
        )

        distinct_session_id = uuid4()
        distinct_requests = [
            (f"api-distinct-{uuid4().hex[:20]}", "同时咨询第一段行程的早餐安排"),
            (f"api-distinct-{uuid4().hex[:20]}", "同时咨询第二段行程的晚餐安排"),
        ]
        async with AsyncSessionFactory() as db:
            db.add(
                AdvisorSessionModel(
                    id=distinct_session_id,
                    owner_id=user_id,
                    mode="discover",
                    destination="",
                    pace_level=3,
                    preference_text="",
                    candidates=[],
                    context={},
                    status="ACTIVE",
                    created_at=now,
                    updated_at=now,
                )
            )
            await db.commit()
        await _run_concurrent_processes(
            owner_id=user_id,
            session_id=distinct_session_id,
            requests=distinct_requests,
            phase="CONCURRENT_DISTINCT",
        )
        async with AsyncSessionFactory() as db:
            distinct_rows = (
                await db.scalars(
                    select(AdvisorMessageModel).where(
                        AdvisorMessageModel.session_id == distinct_session_id,
                        AdvisorMessageModel.owner_id == user_id,
                    )
                )
            ).all()
            distinct_users = {
                item.idempotency_key: item for item in distinct_rows if item.role == "user"
            }
            distinct_replies = {
                item.payload.get("response_to_idempotency_key"): item
                for item in distinct_rows
                if item.role == "assistant"
            }
            assert set(distinct_users) == {key for key, _ in distinct_requests}
            assert set(distinct_replies) == set(distinct_users)
            for key, content in distinct_requests:
                assert distinct_users[key].content == content
                assert key in distinct_replies
        print(
            "PASS: two concurrent distinct requests were serialized into two correctly linked replies."
        )
    finally:
        async with AsyncSessionFactory() as cleanup:
            await cleanup.execute(delete(UserModel).where(UserModel.id == user_id))
            if temporary_poi_ids:
                await cleanup.execute(delete(PoiModel).where(PoiModel.id.in_(temporary_poi_ids)))
            await cleanup.commit()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--retry-phase",
        choices=(
            "INTERRUPT",
            "SEARCH_CHECKPOINT_INTERRUPT",
            "WEATHER_CHECKPOINT_INTERRUPT",
            "ROUTE_CHECKPOINT_INTERRUPT",
            "DIRECT_ROUTE_CHECKPOINT_INTERRUPT",
            "DECISION_CHECKPOINT_INTERRUPT",
            "SERVICE_RECOVERY_INTERRUPT",
            "SERVICE_RECOVERY_RETRY",
            "SERVICE_MIXED_INTERRUPT",
            "SERVICE_MIXED_RETRY",
            "SERVICE_MIXED_SEARCH_FAILURE_RETRY",
            "SERVICE_MIXED_ALL_SEARCHES_INTERRUPT",
            "SERVICE_MIXED_ALL_SEARCHES_RETRY",
            "SERVICE_WEATHER_INTERRUPT",
            "SERVICE_WEATHER_RETRY",
            "SERVICE_ROUTE_INTERRUPT",
            "SERVICE_ROUTE_RETRY",
            "SERVICE_SCHEDULE_INTERRUPT",
            "SERVICE_SCHEDULE_RETRY",
            "SERVICE_SEARCH_SCHEDULE_INTERRUPT",
            "SERVICE_SEARCH_SCHEDULE_RETRY",
            "SERVICE_REPLY_COMMIT_INTERRUPT",
            "SERVICE_REPLY_COMMIT_RETRY",
            "SERVICE_PENDING_ANSWER_INTERRUPT",
            "SERVICE_PENDING_ANSWER_RETRY",
            "SERVICE_COMPOUND_INTERRUPT",
            "SERVICE_COMPOUND_RETRY",
            "SERVICE_COMPOUND_ROUTE_FIRST_INTERRUPT",
            "SERVICE_COMPOUND_WEATHER_FIRST_INTERRUPT",
            "SERVICE_COMPOUND_ROUTE_FAILURE_RETRY",
            "SERVICE_COMPOUND_WEATHER_FAILURE_RETRY",
            "RETRY",
            "CONCURRENT",
            "CONCURRENT_DISTINCT",
        ),
    )
    parser.add_argument("--owner-id")
    parser.add_argument("--session-id")
    parser.add_argument("--idempotency-key")
    parser.add_argument("--content")
    parser.add_argument("--recovered-stage", choices=("SEARCH_WEATHER", "ROUTE"))
    args = parser.parse_args()
    if args.retry_phase:
        if not all((args.owner_id, args.session_id, args.idempotency_key, args.content)):
            parser.error("retry phases require owner, session, idempotency key and content")
        asyncio.run(
            _run_retry_api_phase(
                phase=args.retry_phase,
                owner_id=UUID(args.owner_id),
                session_id=UUID(args.session_id),
                idempotency_key=args.idempotency_key,
                content=args.content,
                recovered_stage=args.recovered_stage,
            )
        )
    else:
        asyncio.run(verify())
