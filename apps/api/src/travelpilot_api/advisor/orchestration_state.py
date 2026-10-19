"""Owner-scoped persistence for safe, retry-stable orchestration decisions."""

import hashlib
import json
import logging
import math
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from travelpilot_core.agents.verifiers import verify_distance_matrix
from travelpilot_infra.db.models.advisor import AdvisorRunEventModel
from travelpilot_infra.db.session import AsyncSessionFactory

from travelpilot_api.advisor.decision_refs import readonly_action_ref, stage_observation_ref  # noqa: F401
from travelpilot_api.advisor.route_evidence import reusable_route_evidence
from travelpilot_api.advisor.weather_evidence import valid_weather_evidence

logger = logging.getLogger(__name__)
_PHASE = "turn_orchestration"
_OPERATION = "stage_order_selected"
_ROUTE_EVIDENCE_OPERATION = "route_evidence_snapshot"
_ROUTE_EVIDENCE_PHASE = "schedule_recovery"
_WEATHER_EVIDENCE_OPERATION = "weather_evidence_snapshot"
_WEATHER_EVIDENCE_PHASE = "search_weather_recovery"
_SEARCH_EVIDENCE_OPERATION = "map_search_snapshot"
_SEARCH_EVIDENCE_PHASE = "search_recovery"
_DIRECT_ROUTE_OPERATION = "direct_route_snapshot"
_DIRECT_ROUTE_PHASE = "route_recovery"
_TOOL_DECISION_OPERATION = "readonly_tool_decision"
_TOOL_DECISION_PHASE = "search_weather_recovery"
_TURN_STAGE_PHASE = "turn_stage"
_TURN_STAGE_OPERATION = "advisor_agent_loop"
_STAGES = {"SEARCH_WEATHER", "SCHEDULE", "ROUTE"}
_STAGE_ORDER_TTL_SECONDS = 600
_TOOL_DECISION_TTL_SECONDS = 600
_TURN_STAGE_TTL_SECONDS = 600


def _task_ref(task_id: str) -> str:
    return hashlib.sha256(task_id.encode("utf-8")).hexdigest()[:24]


async def load_turn_stage_checkpoint(
    db: AsyncSession,
    *,
    owner_id: UUID,
    session_id: UUID,
    task_id: str,
    context_revision: int,
) -> dict[str, Any] | None:
    """Recover safe stage sequencing from the latest attempt of this task.

    This does not treat a prior stage as completed: domain evidence must still
    be reconstructed by its own validated checkpoint. It only continues the
    lifecycle sequence and identifies a stage that was left in STARTED state.
    """
    task_ref = _task_ref(task_id)
    rows = await db.scalars(
        select(AdvisorRunEventModel)
        .where(
            AdvisorRunEventModel.owner_id == owner_id,
            AdvisorRunEventModel.session_id == session_id,
            AdvisorRunEventModel.phase == _TURN_STAGE_PHASE,
            AdvisorRunEventModel.operation == _TURN_STAGE_OPERATION,
            AdvisorRunEventModel.metadata_json["task_ref"].as_string() == task_ref,
            AdvisorRunEventModel.metadata_json["context_revision"].as_integer()
            == context_revision,
        )
        .order_by(AdvisorRunEventModel.created_at.desc())
        .limit(80)
    )
    now = datetime.now(UTC)
    valid_rows = []
    for row in rows.all():
        created_at = getattr(row, "created_at", None)
        metadata = row.metadata_json if isinstance(row.metadata_json, dict) else {}
        if not isinstance(created_at, datetime) or created_at.tzinfo is None:
            continue
        age = (now - created_at.astimezone(UTC)).total_seconds()
        if (
            not 0 <= age <= _TURN_STAGE_TTL_SECONDS
            or metadata.get("task_ref") != task_ref
            or metadata.get("context_revision") != context_revision
            or metadata.get("tool") not in _STAGES
            or metadata.get("attempt_ref") is None
        ):
            continue
        transition = metadata.get("transition")
        if isinstance(transition, bool) or not isinstance(transition, int) or not 1 <= transition <= 1000:
            continue
        valid_rows.append((row, metadata, transition))
    if not valid_rows:
        return None

    latest_attempt_ref = valid_rows[0][1]["attempt_ref"]
    attempt_rows = [
        entry for entry in valid_rows if entry[1].get("attempt_ref") == latest_attempt_ref
    ]
    latest_states: dict[tuple[int, str], str] = {}
    max_transition = 0
    for _row, metadata, transition in reversed(attempt_rows):
        stage = metadata["tool"]
        state = metadata.get("state")
        key = (transition, stage)
        max_transition = max(max_transition, transition)
        if isinstance(state, str):
            latest_states[key] = state
    # A stage can be retried within one attempt. Report only its most recent
    # lifecycle state; an earlier STARTED/SUCCEEDED event must not mask a later
    # terminal failure or a successful retry.
    latest_by_stage: dict[str, tuple[int, str]] = {}
    for (transition, stage), state in latest_states.items():
        previous = latest_by_stage.get(stage)
        if previous is None or transition > previous[0]:
            latest_by_stage[stage] = (transition, state)
    open_stages = [
        (transition, stage)
        for stage, (transition, state) in latest_by_stage.items()
        if state == "STAGE_STARTED"
    ]
    interrupted = max(open_stages, default=None)
    completed_stages = sorted(
        (stage for stage, (_transition, state) in latest_by_stage.items()
         if state == "SUCCEEDED"),
        key=lambda stage: ("SEARCH_WEATHER", "ROUTE", "SCHEDULE").index(stage),
    )
    recovered_decisions = []
    for _row, metadata, _transition in reversed(attempt_rows):
        if metadata.get("state") != "STAGE_SELECTED":
            continue
        eligible = metadata.get("eligible_stages")
        order = metadata.get("selected_order")
        source = metadata.get("decision_source")
        if (
            not isinstance(eligible, list) or not isinstance(order, list)
            or not eligible or len(eligible) > 3 or len(order) > 3
            or not all(
                isinstance(stage, str) and stage in _STAGES
                for stage in eligible + order
            )
            or len(set(eligible)) != len(eligible)
            or len(set(order)) != len(order)
            or set(eligible) != set(order)
            or not isinstance(source, str)
            or source not in {"MODEL", "RULE", "RUNTIME_GUARD", "RULE_RECOVERY"}
        ):
            continue
        recovered_decisions.append({
            "eligible_stages": eligible,
            "order": order,
            "decision_source": source,
        })
        if len(recovered_decisions) >= 10:
            break
    return {
        "next_transition": max_transition + 1,
        "has_previous_attempt": True,
        "interrupted_stage": interrupted[1] if interrupted else None,
        "completed_stages": completed_stages,
        "selection_history": recovered_decisions,
    }


def _safe_tool_decision(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    tool, action_ref, observation_ref = (
        value.get("tool"), value.get("action_ref"), value.get("observation_ref"),
    )
    decision_number = value.get("decision_number")
    if (
        tool not in {
            "search_places", "get_trip_weather", "get_trip_route",
            "search_destination_places", "build_schedule_draft",
        }
        or not isinstance(action_ref, str) or len(action_ref) != 64
        or not isinstance(observation_ref, str) or len(observation_ref) != 64
        or isinstance(decision_number, bool) or not isinstance(decision_number, int)
        or not 1 <= decision_number <= 10
    ):
        return None
    return {"tool": tool, "action_ref": action_ref,
            "observation_ref": observation_ref, "decision_number": decision_number}


async def load_readonly_tool_decision(
    db: AsyncSession, *, owner_id: UUID, session_id: UUID, task_id: str,
    context_revision: int, observation_ref: str, decision_number: int,
    approved_actions: dict[str, str],
) -> dict[str, str] | None:
    """Recover an action reference only when current observation and scope match."""
    task_ref = _task_ref(task_id)
    rows = await db.scalars(
        select(AdvisorRunEventModel).where(
            AdvisorRunEventModel.owner_id == owner_id,
            AdvisorRunEventModel.session_id == session_id,
            AdvisorRunEventModel.phase == _TOOL_DECISION_PHASE,
            AdvisorRunEventModel.operation == _TOOL_DECISION_OPERATION,
            AdvisorRunEventModel.metadata_json["task_ref"].as_string() == task_ref,
        ).order_by(AdvisorRunEventModel.created_at.desc()).limit(32)
    )
    now = datetime.now(UTC)
    for row in rows.all():
        created_at = getattr(row, "created_at", None)
        metadata = row.metadata_json if isinstance(row.metadata_json, dict) else {}
        decision = _safe_tool_decision(metadata)
        if (
            not isinstance(created_at, datetime) or created_at.tzinfo is None
            or not 0 <= (now - created_at.astimezone(UTC)).total_seconds() <= _TOOL_DECISION_TTL_SECONDS
            or row.owner_id != owner_id or row.session_id != session_id
            or row.status != "RECOVERABLE"
            or metadata.get("task_ref") != task_ref
            or metadata.get("context_revision") != context_revision
            or decision is None
            or decision["observation_ref"] != observation_ref
            or decision["decision_number"] != decision_number
            or approved_actions.get(decision["action_ref"]) != decision["tool"]
        ):
            continue
        return {"tool": decision["tool"], "action_ref": decision["action_ref"]}
    return None


def readonly_tool_decision_writer(
    *, owner_id: UUID, session_id: UUID, task_id: str,
    context_revision: int, observation_ref: str, decision_number: int,
    approved_actions: dict[str, str],
):
    """Persist an opaque ref to an approved read-only or pure draft action."""
    task_ref = _task_ref(task_id)

    async def write(decision: dict[str, Any]) -> None:
        safe = _safe_tool_decision({
            **decision, "observation_ref": observation_ref,
            "decision_number": decision_number,
        })
        if safe is None or approved_actions.get(safe["action_ref"]) != safe["tool"]:
            return
        metadata = {
            "task_ref": task_ref, "context_revision": max(0, context_revision),
            **safe,
        }
        try:
            async with AsyncSessionFactory() as session:
                session.add(AdvisorRunEventModel(
                    owner_id=owner_id, session_id=session_id,
                    phase=_TOOL_DECISION_PHASE, operation=_TOOL_DECISION_OPERATION,
                    status="RECOVERABLE", metadata_json=metadata,
                ))
                await session.commit()
        except Exception:
            logger.exception("Could not persist Advisor read-only action checkpoint")

    return write


def readonly_tool_decision_callbacks(
    db: AsyncSession, *, owner_id: UUID, session_id: UUID, task_id: str,
    context_revision: int,
):
    """Bind decision recovery to one owner/session/task/context scope."""
    async def load(*, observation_ref: str, decision_number: int,
                   approved_actions: dict[str, str]):
        return await load_readonly_tool_decision(
            db, owner_id=owner_id, session_id=session_id, task_id=task_id,
            context_revision=context_revision, observation_ref=observation_ref,
            decision_number=decision_number, approved_actions=approved_actions,
        )

    async def save(*, observation_ref: str, decision_number: int,
                   approved_actions: dict[str, str], tool: str, action_ref: str):
        writer = readonly_tool_decision_writer(
            owner_id=owner_id, session_id=session_id, task_id=task_id,
            context_revision=context_revision, observation_ref=observation_ref,
            decision_number=decision_number, approved_actions=approved_actions,
        )
        await writer({"tool": tool, "action_ref": action_ref})

    return load, save


def weather_scope_ref(city: str, start_date: str, end_date: str, context_revision: int,
                      expected_source: str) -> str:
    """Hash the requested forecast scope; never persist the raw destination."""
    payload = json.dumps(
        [city.strip().casefold(), start_date, end_date, context_revision, expected_source],
        separators=(",", ":"), ensure_ascii=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def search_scope_ref(city: str, keyword: str, context_revision: int,
                     expected_source: str) -> str:
    """Hash a provider search query; unrelated trip edits do not invalidate it."""
    payload = json.dumps(
        [city.strip().casefold(), keyword.strip().casefold(), expected_source],
        separators=(",", ":"), ensure_ascii=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def direct_route_scope_ref(user_goal: str, candidate_ids: list[str], context_revision: int,
                           expected_source: str) -> str:
    payload = json.dumps(
        [user_goal.strip().casefold(), sorted(candidate_ids[:40]), context_revision, expected_source],
        separators=(",", ":"), ensure_ascii=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _safe_direct_route_snapshot(value: Any, *, expected_source: str) -> dict[str, Any] | None:
    if not isinstance(value, dict) or value.get("status") not in {"VERIFIED", "SIMULATED"}:
        return None
    if value.get("source") != expected_source:
        return None
    try:
        retrieved = datetime.fromisoformat(value["retrieved_at"])
    except (KeyError, TypeError, ValueError):
        return None
    if retrieved.tzinfo is None:
        return None
    origin_id, destination_id = value.get("origin_place_id"), value.get("destination_place_id")
    distance, duration, mode = value.get("distance_m"), value.get("duration_min"), value.get("mode")
    if (
        not isinstance(origin_id, str) or not origin_id or not isinstance(destination_id, str)
        or not destination_id or origin_id == destination_id
        or isinstance(distance, bool) or not isinstance(distance, int) or distance <= 0
        or isinstance(duration, bool) or not isinstance(duration, int) or not 0 < duration <= 2880
        or mode not in {"DRIVING", "WALKING", "TRANSIT"}
    ):
        return None

    def valid_path(path: Any, *, exact: int | None = None) -> bool:
        if not isinstance(path, list) or len(path) > 500 or (exact is not None and len(path) != exact):
            return False
        return all(
            isinstance(point, list) and len(point) == 2
            and all(isinstance(coord, (int, float)) and not isinstance(coord, bool)
                    and math.isfinite(coord) for coord in point)
            and -90 <= point[0] <= 90 and -180 <= point[1] <= 180
            for point in path
        )

    if not valid_path(value.get("points"), exact=2) or not valid_path(value.get("polyline")):
        return None
    segments = value.get("segments", [])
    if not isinstance(segments, list) or len(segments) > 30 or any(
        not isinstance(segment, dict)
        or set(segment) != {"mode", "label"}
        or not isinstance(segment.get("mode"), str)
        or not isinstance(segment.get("label"), str)
        for segment in segments
    ):
        return None
    return {
        "status": value["status"], "origin_place_id": origin_id,
        "destination_place_id": destination_id,
        "points": value["points"], "distance_m": distance, "duration_min": duration,
        "mode": mode, "source": expected_source,
        "retrieved_at": retrieved.astimezone(UTC).isoformat(),
        "polyline": value["polyline"],
        "segments": [{"mode": item["mode"][:24], "label": item["label"][:240]}
                     for item in segments],
    }


async def load_direct_route_checkpoint(
    db: AsyncSession, *, owner_id: UUID, session_id: UUID, task_id: str,
    scope_ref: str, expected_source: str,
) -> dict[str, Any] | None:
    task_ref = _task_ref(task_id)
    rows = await db.scalars(
        select(AdvisorRunEventModel).where(
            AdvisorRunEventModel.owner_id == owner_id,
            AdvisorRunEventModel.session_id == session_id,
            AdvisorRunEventModel.phase == _DIRECT_ROUTE_PHASE,
            AdvisorRunEventModel.operation == _DIRECT_ROUTE_OPERATION,
            AdvisorRunEventModel.metadata_json["task_ref"].as_string() == task_ref,
            AdvisorRunEventModel.metadata_json["scope_ref"].as_string() == scope_ref,
        ).order_by(AdvisorRunEventModel.created_at.desc()).limit(5)
    )
    now = datetime.now(UTC)
    for row in rows.all():
        metadata = row.metadata_json if isinstance(row.metadata_json, dict) else {}
        if (
            metadata.get("task_ref") != task_ref or metadata.get("scope_ref") != scope_ref
            or row.owner_id != owner_id or row.session_id != session_id
            or row.status != "RECOVERABLE"
        ):
            continue
        snapshot = _safe_direct_route_snapshot(
            metadata.get("route"), expected_source=expected_source,
        )
        if snapshot is None:
            continue
        age = (now - datetime.fromisoformat(snapshot["retrieved_at"]).astimezone(UTC)).total_seconds()
        if 0 <= age <= 600:
            return snapshot
    return None


def direct_route_checkpoint_writer(*, owner_id: UUID, session_id: UUID, task_id: str,
                                   scope_ref: str, expected_source: str):
    task_ref = _task_ref(task_id)

    async def write(route: dict[str, Any]) -> None:
        snapshot = _safe_direct_route_snapshot(route, expected_source=expected_source)
        if snapshot is None:
            return
        metadata = {"task_ref": task_ref, "scope_ref": scope_ref, "route": snapshot}
        try:
            async with AsyncSessionFactory() as session:
                await session.execute(delete(AdvisorRunEventModel).where(
                    AdvisorRunEventModel.owner_id == owner_id,
                    AdvisorRunEventModel.session_id == session_id,
                    AdvisorRunEventModel.phase == _DIRECT_ROUTE_PHASE,
                    AdvisorRunEventModel.operation == _DIRECT_ROUTE_OPERATION,
                    AdvisorRunEventModel.metadata_json["task_ref"].as_string() == task_ref,
                    AdvisorRunEventModel.metadata_json["scope_ref"].as_string() == scope_ref,
                ))
                session.add(AdvisorRunEventModel(
                    owner_id=owner_id, session_id=session_id,
                    phase=_DIRECT_ROUTE_PHASE, operation=_DIRECT_ROUTE_OPERATION,
                    status="RECOVERABLE", metadata_json=metadata,
                ))
                await session.commit()
        except Exception:
            logger.exception("Could not persist Advisor direct-route checkpoint")

    return write


def _safe_search_snapshot(value: Any, *, expected_source: str) -> dict[str, Any] | None:
    if not isinstance(value, dict) or value.get("status") not in {"VERIFIED", "SIMULATED", "NO_RESULTS"}:
        return None
    if not isinstance(value.get("retrieved_at"), str):
        return None
    try:
        retrieved = datetime.fromisoformat(value["retrieved_at"])
    except ValueError:
        return None
    if retrieved.tzinfo is None:
        return None
    places = value.get("places")
    if not isinstance(places, list) or len(places) > 100:
        return None
    allowed = {"provider", "provider_id", "name", "address", "category", "latitude",
               "longitude", "city_code", "duration_min", "opening_hours", "popularity_score"}
    clean_places = []
    for place in places:
        if not isinstance(place, dict) or set(place) - allowed:
            return None
        if not all(isinstance(place.get(key), str) and place[key]
                   for key in ("provider", "provider_id", "name")):
            return None
        latitude, longitude = place.get("latitude"), place.get("longitude")
        if (
            isinstance(latitude, bool) or not isinstance(latitude, (int, float))
            or not math.isfinite(latitude) or not -90 <= latitude <= 90
            or isinstance(longitude, bool) or not isinstance(longitude, (int, float))
            or not math.isfinite(longitude) or not -180 <= longitude <= 180
        ):
            return None
        clean = {key: place.get(key) for key in allowed if key in place}
        for key in ("address", "category", "city_code"):
            if clean.get(key) is not None and not isinstance(clean[key], str):
                return None
            if isinstance(clean.get(key), str):
                clean[key] = clean[key][:300]
        opening_hours = clean.get("opening_hours")
        if opening_hours is not None:
            if isinstance(opening_hours, str):
                clean["opening_hours"] = opening_hours[:300]
            elif isinstance(opening_hours, dict) and len(opening_hours) <= 16:
                bounded_hours = {}
                for label, hours_value in opening_hours.items():
                    if (
                        not isinstance(label, str) or not label.strip()
                        or len(label) > 40 or not isinstance(hours_value, str)
                    ):
                        return None
                    bounded_hours[label] = hours_value[:160]
                clean["opening_hours"] = bounded_hours
            else:
                return None
        for key in ("duration_min", "popularity_score"):
            item = clean.get(key)
            if item is not None and (
                isinstance(item, bool) or not isinstance(item, (int, float))
                or not math.isfinite(item)
            ):
                return None
        clean_places.append(clean)
    if value["status"] == "NO_RESULTS" and places:
        return None
    if value["status"] in {"VERIFIED", "SIMULATED"} and not places:
        return None
    is_simulated_source = expected_source == "Local simulated maps"
    if any((place["provider"] == "fake") != is_simulated_source for place in clean_places):
        return None
    return {
        "status": value["status"],
        "retrieved_at": retrieved.astimezone(UTC).isoformat(),
        "places": clean_places,
    }


async def load_search_evidence_checkpoint(
    db: AsyncSession, *, owner_id: UUID, session_id: UUID, task_id: str,
    scope_ref: str, expected_source: str,
) -> dict[str, Any] | None:
    task_ref = _task_ref(task_id)
    rows = await db.scalars(
        select(AdvisorRunEventModel).where(
            AdvisorRunEventModel.owner_id == owner_id,
            AdvisorRunEventModel.session_id == session_id,
            AdvisorRunEventModel.phase == _SEARCH_EVIDENCE_PHASE,
            AdvisorRunEventModel.operation == _SEARCH_EVIDENCE_OPERATION,
            AdvisorRunEventModel.metadata_json["task_ref"].as_string() == task_ref,
            AdvisorRunEventModel.metadata_json["scope_ref"].as_string() == scope_ref,
        ).order_by(AdvisorRunEventModel.created_at.desc()).limit(5)
    )
    now = datetime.now(UTC)
    for row in rows.all():
        metadata = row.metadata_json if isinstance(row.metadata_json, dict) else {}
        if (
            metadata.get("task_ref") != task_ref
            or metadata.get("scope_ref") != scope_ref
            or row.owner_id != owner_id or row.session_id != session_id
            or row.status != "RECOVERABLE"
        ):
            continue
        snapshot = _safe_search_snapshot(metadata.get("search"), expected_source=expected_source)
        if snapshot is None:
            continue
        age = (now - datetime.fromisoformat(snapshot["retrieved_at"]).astimezone(UTC)).total_seconds()
        if 0 <= age <= 600:
            return snapshot
    return None


def search_evidence_checkpoint_writer(*, owner_id: UUID, session_id: UUID,
                                      task_id: str, scope_ref: str,
                                      expected_source: str):
    task_ref = _task_ref(task_id)

    async def write(result: dict[str, Any]) -> None:
        snapshot = _safe_search_snapshot(result, expected_source=expected_source)
        if snapshot is None:
            return
        metadata = {"task_ref": task_ref, "scope_ref": scope_ref, "search": snapshot}
        try:
            async with AsyncSessionFactory() as session:
                await session.execute(delete(AdvisorRunEventModel).where(
                    AdvisorRunEventModel.owner_id == owner_id,
                    AdvisorRunEventModel.session_id == session_id,
                    AdvisorRunEventModel.phase == _SEARCH_EVIDENCE_PHASE,
                    AdvisorRunEventModel.operation == _SEARCH_EVIDENCE_OPERATION,
                    AdvisorRunEventModel.metadata_json["task_ref"].as_string() == task_ref,
                    AdvisorRunEventModel.metadata_json["scope_ref"].as_string() == scope_ref,
                ))
                session.add(AdvisorRunEventModel(
                    owner_id=owner_id, session_id=session_id,
                    phase=_SEARCH_EVIDENCE_PHASE, operation=_SEARCH_EVIDENCE_OPERATION,
                    status="RECOVERABLE", metadata_json=metadata,
                ))
                await session.commit()
        except Exception:
            logger.exception("Could not persist Advisor map-search checkpoint")

    return write


def _safe_weather_snapshot(value: Any, *, expected_source: str) -> dict[str, Any] | None:
    if (
        not isinstance(value, dict)
        or not isinstance(value.get("fetched_at"), str)
        or not valid_weather_evidence(value, expected_source=expected_source, require_city=False)
    ):
        return None
    fetched = datetime.fromisoformat(value["fetched_at"])
    days = value.get("days")
    clean_days = []
    allowed = {"date", "description", "weather_code", "temperature_max_c",
               "temperature_min_c", "precipitation_probability_max"}
    for day in days:
        clean = {"date": day["date"], "description": day["description"][:80]}
        for key in allowed - {"date", "description"}:
            item = day.get(key)
            if item is None:
                clean[key] = None
            else:
                clean[key] = item
        clean_days.append(clean)
    timezone = value.get("timezone")
    message = value.get("message")
    attribution = value.get("attribution")
    return {
        "status": value["status"], "source": expected_source,
        "fetched_at": fetched.astimezone(UTC).isoformat(),
        "days": clean_days,
        "timezone": timezone[:80] if isinstance(timezone, str) else None,
        "message": message[:240] if isinstance(message, str) else None,
        "attribution": attribution[:160] if isinstance(attribution, str) else None,
    }


async def load_weather_evidence_checkpoint(
    db: AsyncSession, *, owner_id: UUID, session_id: UUID, task_id: str,
    scope_ref: str, city: str, expected_source: str,
) -> dict[str, Any] | None:
    task_ref = _task_ref(task_id)
    rows = await db.scalars(
        select(AdvisorRunEventModel).where(
            AdvisorRunEventModel.owner_id == owner_id,
            AdvisorRunEventModel.session_id == session_id,
            AdvisorRunEventModel.phase == _WEATHER_EVIDENCE_PHASE,
            AdvisorRunEventModel.operation == _WEATHER_EVIDENCE_OPERATION,
            AdvisorRunEventModel.metadata_json["task_ref"].as_string() == task_ref,
            AdvisorRunEventModel.metadata_json["scope_ref"].as_string() == scope_ref,
        ).order_by(AdvisorRunEventModel.created_at.desc()).limit(5)
    )
    now = datetime.now(UTC)
    for row in rows.all():
        metadata = row.metadata_json if isinstance(row.metadata_json, dict) else {}
        if (
            metadata.get("task_ref") != task_ref
            or metadata.get("scope_ref") != scope_ref
            or row.owner_id != owner_id
            or row.session_id != session_id
            or row.status != "RECOVERABLE"
        ):
            continue
        snapshot = _safe_weather_snapshot(metadata.get("weather"), expected_source=expected_source)
        if snapshot is None:
            continue
        fetched = datetime.fromisoformat(snapshot["fetched_at"])
        age = (now - fetched.astimezone(UTC)).total_seconds()
        if not 0 <= age <= 600:
            continue
        return {**snapshot, "city": city}
    return None


def weather_evidence_checkpoint_writer(*, owner_id: UUID, session_id: UUID,
                                       task_id: str, scope_ref: str,
                                       expected_source: str):
    task_ref = _task_ref(task_id)

    async def write(weather: dict[str, Any]) -> None:
        snapshot = _safe_weather_snapshot(weather, expected_source=expected_source)
        if snapshot is None:
            return
        metadata = {"task_ref": task_ref, "scope_ref": scope_ref, "weather": snapshot}
        try:
            async with AsyncSessionFactory() as session:
                await session.execute(delete(AdvisorRunEventModel).where(
                    AdvisorRunEventModel.owner_id == owner_id,
                    AdvisorRunEventModel.session_id == session_id,
                    AdvisorRunEventModel.phase == _WEATHER_EVIDENCE_PHASE,
                    AdvisorRunEventModel.operation == _WEATHER_EVIDENCE_OPERATION,
                    AdvisorRunEventModel.metadata_json["task_ref"].as_string() == task_ref,
                    AdvisorRunEventModel.metadata_json["scope_ref"].as_string() == scope_ref,
                ))
                session.add(AdvisorRunEventModel(
                    owner_id=owner_id, session_id=session_id,
                    phase=_WEATHER_EVIDENCE_PHASE, operation=_WEATHER_EVIDENCE_OPERATION,
                    status="RECOVERABLE", metadata_json=metadata,
                ))
                await session.commit()
        except Exception:
            logger.exception("Could not persist Advisor weather-evidence checkpoint")

    return write


async def load_route_evidence_checkpoint(
    db: AsyncSession,
    *,
    owner_id: UUID,
    session_id: UUID,
    task_id: str,
    candidate_ids: list[str],
    context_revision: int,
    expected_source: str,
) -> dict[str, Any] | None:
    """Recover only fresh, verified route matrices for the same task and trip state."""
    task_ref = _task_ref(task_id)
    rows = await db.scalars(
        select(AdvisorRunEventModel)
        .where(
            AdvisorRunEventModel.owner_id == owner_id,
            AdvisorRunEventModel.session_id == session_id,
            AdvisorRunEventModel.phase == _ROUTE_EVIDENCE_PHASE,
            AdvisorRunEventModel.operation == _ROUTE_EVIDENCE_OPERATION,
            AdvisorRunEventModel.metadata_json["task_ref"].as_string() == task_ref,
        )
        .order_by(AdvisorRunEventModel.created_at.desc())
        .limit(5)
    )
    for row in rows.all():
        metadata = row.metadata_json if isinstance(row.metadata_json, dict) else {}
        if metadata.get("context_revision") != context_revision:
            continue
        recovered = {
            "candidate_ids": metadata.get("candidate_ids"),
            "context_revision": metadata.get("context_revision"),
            "durations_min": metadata.get("durations_min"),
            "status": metadata.get("evidence_status"),
            "source": metadata.get("source"),
            "retrieved_at": metadata.get("retrieved_at"),
        }
        if reusable_route_evidence(
            recovered, candidate_ids, context_revision,
            expected_source=expected_source,
        ) is not None:
            return recovered
    return None


def route_evidence_checkpoint_writer(
    *, owner_id: UUID, session_id: UUID, task_id: str,
):
    """Persist a small, owner-scoped route matrix for safe idempotent retry only."""
    task_ref = _task_ref(task_id)

    async def write(snapshot: dict[str, Any]) -> None:
        candidate_ids = snapshot.get("candidate_ids")
        matrix = snapshot.get("durations_min")
        status = snapshot.get("status")
        source = snapshot.get("source")
        retrieved_at = snapshot.get("retrieved_at")
        if (
            not isinstance(candidate_ids, list)
            or not 2 <= len(candidate_ids) <= 29
            or any(not isinstance(item, str) or not item for item in candidate_ids)
            or len(set(candidate_ids)) != len(candidate_ids)
            or not verify_distance_matrix({"durations_min": matrix}, len(candidate_ids))
            or status not in {"VERIFIED", "SIMULATED"}
            or not isinstance(source, str)
            or not isinstance(retrieved_at, str)
        ):
            return
        try:
            retrieved = datetime.fromisoformat(retrieved_at)
        except ValueError:
            return
        if retrieved.tzinfo is None:
            return
        metadata = {
            "task_ref": task_ref,
            "candidate_ids": candidate_ids,
            "context_revision": max(0, int(snapshot.get("context_revision", 0))),
            "durations_min": matrix,
            "evidence_status": status,
            "source": source[:80],
            "retrieved_at": retrieved.astimezone(UTC).isoformat(),
        }
        try:
            async with AsyncSessionFactory() as session:
                await session.execute(delete(AdvisorRunEventModel).where(
                    AdvisorRunEventModel.owner_id == owner_id,
                    AdvisorRunEventModel.session_id == session_id,
                    AdvisorRunEventModel.phase == _ROUTE_EVIDENCE_PHASE,
                    AdvisorRunEventModel.operation == _ROUTE_EVIDENCE_OPERATION,
                    AdvisorRunEventModel.metadata_json["task_ref"].as_string() == task_ref,
                ))
                session.add(AdvisorRunEventModel(
                    owner_id=owner_id,
                    session_id=session_id,
                    phase=_ROUTE_EVIDENCE_PHASE,
                    operation=_ROUTE_EVIDENCE_OPERATION,
                    status="RECOVERABLE",
                    metadata_json=metadata,
                ))
                await session.commit()
        except Exception:
            logger.exception("Could not persist Advisor route-evidence checkpoint")

    return write


def _valid_order(value: Any, allowed_stages: list[str]) -> list[str] | None:
    if not isinstance(value, list) or not 1 <= len(value) <= 3:
        return None
    if any(not isinstance(stage, str) or stage not in _STAGES for stage in value):
        return None
    if len(set(value)) != len(value) or set(value) != set(allowed_stages):
        return None
    return value


async def load_stage_order_checkpoint(
    db: AsyncSession,
    *,
    owner_id: UUID,
    session_id: UUID,
    task_id: str,
    context_revision: int,
    allowed_stages: list[str],
    observation_ref: str = "",
) -> dict[str, Any] | None:
    """Load only a recent order decision for the same owner/task/version."""
    expected_task_ref = _task_ref(task_id)
    rows = await db.scalars(
        select(AdvisorRunEventModel)
        .where(
            AdvisorRunEventModel.owner_id == owner_id,
            AdvisorRunEventModel.session_id == session_id,
            AdvisorRunEventModel.phase == _PHASE,
            AdvisorRunEventModel.operation == _OPERATION,
        )
        .order_by(AdvisorRunEventModel.created_at.desc())
        .limit(32)
    )
    now = datetime.now(UTC)
    for row in rows.all():
        created_at = getattr(row, "created_at", None)
        if not isinstance(created_at, datetime) or created_at.tzinfo is None:
            continue
        age_seconds = (now - created_at.astimezone(UTC)).total_seconds()
        if not 0 <= age_seconds <= _STAGE_ORDER_TTL_SECONDS:
            continue
        metadata = row.metadata_json if isinstance(row.metadata_json, dict) else {}
        if (
            metadata.get("task_ref") != expected_task_ref
            or metadata.get("context_revision") != context_revision
            or metadata.get("observation_ref") != observation_ref
        ):
            continue
        order = _valid_order(metadata.get("order"), allowed_stages)
        if order is not None:
            return {
                "order": order,
                "decision_source": "RECOVERED",
                "original_decision_source": str(metadata.get("decision_source", "UNKNOWN"))[:24],
                "decision_summary": "恢复本任务已记录的执行顺序，继续安全重试",
                "fallback_reason": metadata.get("fallback_reason"),
            }
    return None


async def resolve_stage_order(
    db: AsyncSession,
    *,
    owner_id: UUID,
    session_id: UUID,
    task_id: str,
    context_revision: int,
    allowed_stages: list[str],
    observation_ref: str = "",
    retryable: bool,
    choose: Callable[[], Awaitable[dict[str, Any]]],
    persist: Callable[[dict[str, Any]], Awaitable[None]],
) -> dict[str, Any]:
    """Reuse a matching decision on retry, otherwise choose and checkpoint it.

    The caller supplies selection and persistence so this boundary can be
    exercised independently from the larger Advisor turn implementation.
    Non-idempotent turns never read or write recovery checkpoints.
    """
    if retryable:
        recovered = await load_stage_order_checkpoint(
            db,
            owner_id=owner_id,
            session_id=session_id,
            task_id=task_id,
            context_revision=context_revision,
            allowed_stages=allowed_stages,
            observation_ref=observation_ref,
        )
        if recovered is not None:
            return recovered

    decision = await choose()
    if retryable:
        await persist(decision)
    return decision


def stage_order_checkpoint_writer(
    *,
    owner_id: UUID,
    session_id: UUID,
    task_id: str,
    context_revision: int,
    allowed_stages: list[str],
    observation_ref: str = "",
):
    """Persist only approved stage names and decision provenance in its own tx."""
    task_ref = _task_ref(task_id)
    attempt_ref = uuid4().hex[:24]

    async def write(decision: dict[str, Any]) -> None:
        order = _valid_order(decision.get("order"), allowed_stages)
        if order is None:
            return
        metadata = {
            "task_ref": task_ref,
            "attempt_ref": attempt_ref,
            "context_revision": max(0, context_revision),
            "observation_ref": observation_ref,
            "order": order,
            "decision_source": str(decision.get("decision_source", "UNKNOWN"))[:24],
        }
        fallback_reason = decision.get("fallback_reason")
        if isinstance(fallback_reason, str):
            metadata["fallback_reason"] = fallback_reason[:80]
        try:
            async with AsyncSessionFactory() as session:
                session.add(AdvisorRunEventModel(
                    owner_id=owner_id,
                    session_id=session_id,
                    phase=_PHASE,
                    operation=_OPERATION,
                    status="RECORDED",
                    metadata_json=metadata,
                ))
                await session.commit()
        except Exception:
            logger.exception("Could not persist Advisor stage-order checkpoint")

    return write
