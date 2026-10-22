"""Run a bounded scenario through the real Advisor API service.

    The evaluator creates a temporary account, trip and Wishlist, sends the
    scenario's natural-language turns through HTTP, records bounded metrics, then
deletes the temporary account. Maps use Fake Maps. Weather scenarios use the
source-labelled local fixture ASGI app; they never call Open-Meteo.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import secrets
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from time import perf_counter
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse
from uuid import uuid4

import httpx
from travelpilot_api.build_info import advisor_runtime_source_manifest, fingerprint_manifest
from travelpilot_api.settings import settings

from tests.evaluation.provenance import evaluation_provenance

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / "tests/evaluation/results/advisor_product_live_api_20261019.json"
TURN_INPUTS = (
    "帮我把收藏的人民公园、武侯祠和锦里安排成两日行程，节奏轻松一点。",
    "请让第二天上午留空，午餐留出一小时；只生成待确认草案，不要替我确认保存。",
)
SCENARIOS = {
    "schedule-two-turn": {
        "turn_inputs": TURN_INPUTS,
        "saved_places": (
            ("人民公园", 30.663, 104.055),
            ("武侯祠", 30.646, 104.048),
            ("锦里", 30.642, 104.055),
        ),
    },
    "schedule-route-weather": {
        "turn_inputs": (
            "请把我收藏的人民公园、武侯祠和锦里安排成两日游，查询人民公园到武侯祠的驾车路线，并结合天气提醒我带什么。",
            "三处都安排，按轻松节奏分两天排并留出午餐时间；先给待确认的时间建议，不要保存正式行程。",
        ),
        "saved_places": (
            ("人民公园", 30.663, 104.055),
            ("武侯祠", 30.646, 104.048),
            ("锦里", 30.642, 104.055),
        ),
        "weather_data_mode": "LOCAL_SIMULATED_FIXTURE",
    },
    "schedule-proposal-edit": {
        "turn_inputs": (
            "请把我收藏的人民公园、武侯祠和锦里排成两日游，节奏轻松，每天留一小时午餐，先给待确认草案。",
            "请让第二天上午留空，午餐留出一小时；只更新待确认草案，不要保存正式行程。",
        ),
        "saved_places": (
            ("人民公园", 30.663, 104.055),
            ("武侯祠", 30.646, 104.048),
            ("锦里", 30.642, 104.055),
        ),
    },
    "lunch-only": {
        "turn_inputs": ("帮我找成都的午餐，想吃川菜。",),
        "saved_places": (("人民公园", 30.663, 104.055),),
    },
    "lunch-weather-preparation": {
        "turn_inputs": (
            "帮我找成都的午餐地点，并结合这段旅行的天气提醒我带伞和穿什么，不用安排景点行程。",
        ),
        "saved_places": (("人民公园", 30.663, 104.055),),
        "weather_data_mode": "LOCAL_SIMULATED_FIXTURE",
    },
    "lunch-weather-weather-outage": {
        "turn_inputs": (
            "帮我找成都的午餐地点，并结合这段旅行的天气提醒我带伞和穿什么，不用安排景点行程。",
        ),
        "saved_places": (("人民公园", 30.663, 104.055),),
        "weather_data_mode": "LOCAL_SYNTHETIC_WEATHER_OUTAGE",
    },
    "mixed-attraction-lunch": {
        "turn_inputs": ("帮我找几个适合游览的景点和午餐地点，先推荐候选，不要安排或保存行程。",),
        "saved_places": (),
    },
    "mixed-attraction-lunch-draft": {
        "turn_inputs": ("帮我找几个适合游览的景点和午餐候选，我选好后再决定时间。",),
        "saved_places": (),
        "draft_selection": {
            "categories": ("ATTRACTION", "FOOD"),
            "start_times": {"ATTRACTION": "09:00", "FOOD": "12:00"},
        },
    },
    "lodging-only": {
        "turn_inputs": (
            "帮我找成都的住宿候选，比较位置就好，不要加入景点行程，也不要代订。",
        ),
        "saved_places": (),
    },
    "destination-discovery": {
        "turn_inputs": ("推荐几个适合周末去的城市",),
        "saved_places": (),
        "advisor_mode": "discover",
        "maps_data_mode": "LOCAL_AMAP_FIXTURE",
    },
    "weather-indoor-search": {
        "turn_inputs": (
            "结合这段旅行的天气看看是否下雨；如果有雨，帮我找成都室内备选地点，先推荐看看，不要加入行程。",
        ),
        "saved_places": (),
        "weather_data_mode": "LOCAL_SIMULATED_FIXTURE",
    },
    "weather-preparation": {
        "turn_inputs": ("结合这段旅行的日期，告诉我需要带伞吗、穿什么衣服比较合适。",),
        "saved_places": (),
        "weather_data_mode": "LOCAL_SIMULATED_FIXTURE",
    },
    "route-weather-preparation": {
        "turn_inputs": (
            "帮我查开车从人民公园到武侯祠怎么走，并结合这段旅行的天气提醒我带什么。",
        ),
        "saved_places": (
            ("人民公园", 30.663, 104.055),
            ("武侯祠", 30.646, 104.048),
        ),
        "weather_data_mode": "LOCAL_SIMULATED_FIXTURE",
    },
    "unsupported-intercity-weather": {
        "turn_inputs": (
            "请查我这趟成都旅行的天气，提醒我带伞和穿什么；另外成都到西安怎么坐高铁？"
            + "不要替我改成驾车路线。",
        ),
        "saved_places": (
            ("人民公园", 30.663, 104.055),
            ("武侯祠", 30.646, 104.048),
        ),
        "weather_data_mode": "LOCAL_SIMULATED_FIXTURE",
    },
    "route-weather-route-outage": {
        "turn_inputs": (
            "帮我查开车从人民公园到武侯祠怎么走，并结合这段旅行的天气提醒我带什么。",
        ),
        "saved_places": (
            ("人民公园", 30.663, 104.055),
            ("武侯祠", 30.646, 104.048),
        ),
        "weather_data_mode": "LOCAL_SIMULATED_FIXTURE",
        "maps_data_mode": "LOCAL_DIRECT_ROUTE_TIMEOUT_FIXTURE",
    },
    "route-weather-weather-outage": {
        "turn_inputs": (
            "帮我查开车从人民公园到武侯祠怎么走，并结合这段旅行的天气提醒我带什么。",
        ),
        "saved_places": (
            ("人民公园", 30.663, 104.055),
            ("武侯祠", 30.646, 104.048),
        ),
        "weather_data_mode": "LOCAL_SYNTHETIC_WEATHER_OUTAGE",
    },
    "general-trip-question": {
        "turn_inputs": ("结合我这段旅行的目的地和已收藏地点，给我一个实用建议。",),
        "saved_places": (
            ("人民公园", 30.663, 104.055),
            ("武侯祠", 30.646, 104.048),
        ),
    },
    "route-provider-outage": {
        "turn_inputs": TURN_INPUTS,
        "saved_places": (
            ("人民公园", 30.663, 104.055),
            ("武侯祠", 30.646, 104.048),
            ("锦里", 30.642, 104.055),
        ),
        "maps_data_mode": "LOCAL_ROUTE_MATRIX_TIMEOUT_FIXTURE",
    },
}


def _resolve_trip_dates(start_date: date | None) -> tuple[date, date]:
    # Keep the default inside Open-Meteo's 16-day forecast horizon so weather
    # cases exercise forecast handling instead of silently testing unavailable
    # long-range dates. Synthetic weather fixtures accept the same near-future
    # date, which keeps real-model runs deterministic.
    start = start_date or (date.today() + timedelta(days=3))
    if start <= date.today():
        raise ValueError("evaluation trip start date must be in the future")
    return start, start + timedelta(days=1)


def _validate_turn_output_cap(metric: dict[str, Any], cap: int | None) -> None:
    """Stop a live run immediately if the API reports a per-turn budget breach."""
    if cap is None:
        return
    usage = metric.get("model_usage")
    output_tokens = usage.get("output_tokens") if isinstance(usage, dict) else None
    if isinstance(output_tokens, int) and not isinstance(output_tokens, bool) and output_tokens > cap:
        raise RuntimeError("ADVISOR_TURN_OUTPUT_BUDGET_EXCEEDED")


def _verify_api_runtime(response: httpx.Response, expected_fingerprint: str) -> str:
    if response.status_code != 200:
        raise RuntimeError(f"API_HEALTH_HTTP_{response.status_code}")
    actual = response.headers.get("x-advisor-runtime-fingerprint")
    if actual != expected_fingerprint:
        raise RuntimeError("ADVISOR_RUNTIME_FINGERPRINT_MISMATCH")
    return actual


def _verify_server_token_caps(
    response: httpx.Response,
    *,
    max_output_tokens_per_turn: int,
    max_output_tokens_per_session: int,
    expected_model: str | None = None,
) -> dict[str, int]:
    """Fail before model calls when the API's effective ceilings exceed the run budget."""
    if response.status_code != 200:
        raise RuntimeError(f"ADVISOR_STATUS_HTTP_{response.status_code}")
    try:
        payload = response.json()
    except (ValueError, TypeError) as exc:
        raise RuntimeError("ADVISOR_STATUS_INVALID_JSON") from exc
    if not isinstance(payload, dict):
        raise TypeError("ADVISOR_STATUS_INVALID_BODY")
    if payload.get("llm_status") != "CONFIGURED":
        raise RuntimeError("ADVISOR_MODEL_NOT_CONFIGURED")
    model_name = payload.get("model_name")
    if not isinstance(model_name, str) or not model_name.strip():
        raise RuntimeError("ADVISOR_STATUS_MODEL_MISSING")
    if expected_model and model_name != expected_model:
        raise RuntimeError("ADVISOR_MODEL_MISMATCH")
    output_cap = payload.get("output_token_limit")
    session_cap = payload.get("session_output_token_limit")
    if (
        isinstance(output_cap, bool) or not isinstance(output_cap, int) or output_cap < 1
        or isinstance(session_cap, bool) or not isinstance(session_cap, int) or session_cap < 1
    ):
        raise RuntimeError("ADVISOR_STATUS_TOKEN_CAPS_MISSING")
    if output_cap > max_output_tokens_per_turn:
        raise RuntimeError("ADVISOR_PER_TURN_TOKEN_CAP_EXCEEDS_AUTHORIZED_LIMIT")
    if session_cap > max_output_tokens_per_session:
        raise RuntimeError("ADVISOR_SESSION_TOKEN_CAP_EXCEEDS_AUTHORIZED_LIMIT")
    return {
        "model_name": model_name,
        "output_token_limit": output_cap,
        "session_output_token_limit": session_cap,
    }


def _provider_mode(llm_base_url: str, *, model_is_local_stub: bool = False) -> str:
    if model_is_local_stub:
        return "LOCAL_MODEL_STUB_REAL_ADVISOR_API_FAKE_MAPS"
    hostname = (urlparse(llm_base_url).hostname or "").lower()
    if hostname in {"localhost", "127.0.0.1", "::1"}:
        return "LOCAL_MODEL_STUB_REAL_ADVISOR_API_FAKE_MAPS"
    return "LIVE_MODEL_REAL_ADVISOR_API_FAKE_MAPS"


def _reported_model_name(
    provider_mode: str,
    *,
    effective_server_token_caps: dict[str, Any] | None,
    configured_model: str,
) -> str:
    """Report the model that served this run, not an unrelated parent .env value."""
    if provider_mode.startswith("LOCAL_MODEL_STUB"):
        return "local-stub"
    if effective_server_token_caps and isinstance(
        effective_server_token_caps.get("model_name"), str,
    ):
        return effective_server_token_caps["model_name"]
    return configured_model


def _assess_product_outcome(
    turns: list[dict[str, Any]], resources: dict[str, Any], account_deleted: bool,
    scenario_id: str = "schedule-two-turn",
) -> dict[str, Any]:
    final_turn = turns[-1] if turns else {}
    all_turn_actions = [
        item for turn in turns for item in turn.get("actions", [])
        if isinstance(item, dict)
    ]
    verified_tools = {
        item.get("tool") for item in all_turn_actions
        if item.get("status") == "VERIFIED"
    }
    checks = {
        "all_http_turns_succeeded": bool(turns) and all(
            turn.get("http_status") == 200 for turn in turns
        ),
        "formal_write_prevented": resources.get("formal_write_prevented") is True,
        "temporary_account_deleted": account_deleted,
    }
    if scenario_id == "destination-discovery":
        recommendations = final_turn.get("destination_recommendations", [])
        searches = [
            action for turn in turns for action in turn.get("actions", [])
            if isinstance(action, dict)
            and action.get("tool") == "search_destination_places"
            and action.get("status") == "VERIFIED"
        ]
        recommendation_places = [
            place for item in recommendations for place in item.get("places", [])
        ]
        checks.update({
            "destination_discovery_event": final_turn.get("event") == "DESTINATION_RECOMMENDATIONS",
            "amap_evidence_is_present": (
                bool(recommendation_places)
                and all(item.get("provider") == "amap" for item in recommendation_places)
                and final_turn.get("trip_context", {}).get("map_evidence") == "AMap Web Service"
            ),
            "agent_used_destination_search_tool": bool(searches),
            "multiple_observation_driven_searches": (
                len(searches) >= 2 and final_turn.get("destination_search_count", 0) >= 2
            ),
            "free_exploration_did_not_create_a_trip": resources.get("trip_created") is False,
            "no_formal_write": resources.get("formal_write_prevented") is True,
        })
    elif scenario_id == "schedule-two-turn":
        saved_places = final_turn.get("trip_context", {}).get("saved_places", [])
        scheduled_place_count = final_turn.get("schedule_suggestion_count", 0)
        checks.update({
            "final_proposal_present": final_turn.get("proposal_present") is True,
            "all_saved_places_scheduled": (
                bool(saved_places) and scheduled_place_count == len(saved_places)
            ),
            "route_matrix_verified": "get_distance_matrix" in verified_tools,
            "schedule_draft_verified": "build_schedule_draft" in verified_tools,
        })
    elif scenario_id == "schedule-route-weather":
        all_actions = [
            item for turn in turns for item in turn.get("actions", [])
            if isinstance(item, dict)
        ]
        tools = [item.get("tool") for item in all_actions]
        all_stage_statuses = {
            item.get("stage"): item.get("status")
            for turn in turns for item in turn.get("stage_statuses", [])
            if isinstance(item, dict)
        }
        forecast = next(
            (turn.get("weather_evidence", {}) for turn in turns
             if turn.get("weather_evidence", {}).get("days")),
            {},
        )
        route = next(
            (turn.get("route_evidence", {}) for turn in turns
             if turn.get("route_evidence", {}).get("duration_min") is not None),
            {},
        )
        decisions = [
            item for turn in turns for item in turn.get("stage_decision_history", [])
            if isinstance(item, dict)
        ]
        adaptive_route_choice = any(
            {"ROUTE", "SCHEDULE"} <= set(item.get("eligible_stages", []))
            and item.get("order", [None])[0] == "ROUTE"
            for item in decisions if isinstance(item, dict)
        )
        replies = " ".join(str(turn.get("assistant_reply", "")) for turn in turns)
        checks.update({
            "weather_route_and_schedule_tools_verified": {
                "get_trip_weather", "get_trip_route", "get_distance_matrix",
                "build_schedule_draft",
            } <= {
                item.get("tool") for item in all_actions if item.get("status") == "VERIFIED"
            },
            "all_required_stages_succeeded": (
                {"SEARCH_WEATHER", "ROUTE", "SCHEDULE"} <= set(all_stage_statuses)
                and all(all_stage_statuses.get(name) == "SUCCEEDED" for name in (
                    "SEARCH_WEATHER", "ROUTE", "SCHEDULE",
                ))
            ),
            "route_matches_requested_saved_places": (
                route.get("origin_name") == "人民公园"
                and route.get("destination_name") == "武侯祠"
                and route.get("mode") == "DRIVING"
                and isinstance(route.get("duration_min"), int)
            ),
            "forecast_is_source_labelled_and_dated": (
                forecast.get("source") == "Local simulated forecast"
                and forecast.get("status") == "AVAILABLE"
                and bool(forecast.get("days"))
                and all(isinstance(day.get("date"), str) for day in forecast.get("days", []))
            ),
            "agent_adapts_stage_choice_after_weather": adaptive_route_choice,
            "schedule_proposal_is_available": final_turn.get("proposal_present") is True,
            "reply_combines_route_weather_and_preparation": (
                "人民公园" in replies and "武侯祠" in replies
                and "建议带伞或轻便雨衣" in replies
            ),
            "unfilled_meal_period_has_an_explicit_user_choice": any(
                item.get("key") == "DRAFT_MEAL:LUNCH"
                for item in final_turn.get("draft_follow_up_options", [])
                if isinstance(item, dict)
            ),
            "no_formal_write_before_confirmation": resources.get("formal_write_prevented") is True,
        })
    elif scenario_id == "schedule-proposal-edit":
        assignments = final_turn.get("schedule_assignments", [])
        start_date = final_turn.get("trip_context", {}).get("start_date")
        saved_places = final_turn.get("trip_context", {}).get("saved_places", [])
        reply = final_turn.get("assistant_reply", "")
        try:
            second_day = (date.fromisoformat(start_date) + timedelta(days=1)).isoformat()
        except (TypeError, ValueError):
            second_day = None
        expected_date_labels = []
        for item in assignments:
            try:
                assigned_day = date.fromisoformat(item.get("day", ""))
            except (AttributeError, TypeError, ValueError):
                continue
            expected_date_labels.append(f"{assigned_day.month}月{assigned_day.day}日")
        reserved_breaks = final_turn.get("schedule_breaks", [])
        expected_days = []
        if start_date:
            try:
                day_count = (date.fromisoformat(final_turn["trip_context"]["end_date"])
                             - date.fromisoformat(start_date)).days + 1
                expected_days = [
                    (date.fromisoformat(start_date) + timedelta(days=offset)).isoformat()
                    for offset in range(day_count)
                ]
            except (KeyError, TypeError, ValueError):
                expected_days = []
        actions = final_turn.get("actions", [])
        tools = [item.get("tool") for item in actions if isinstance(item, dict)]
        checks.update({
            "proposal_survives_user_edit": final_turn.get("proposal_present") is True,
            "all_saved_stops_remain_in_proposal": (
                len(assignments) == len(final_turn.get("trip_context", {}).get("saved_places", []))
            ),
            "second_morning_remains_free": all(
                item.get("day") != second_day or str(item.get("start_time", "")) >= "12:00"
                for item in assignments
            ) and second_day is not None,
            "reply_summarizes_assigned_places_dates_and_times": (
                bool(assignments)
                and "已按草案安排" in reply
                and "待确认草案" in reply
                and all(isinstance(name, str) and name in reply for name in saved_places)
                and all(
                    isinstance(item.get("start_time"), str)
                    and item["start_time"] in reply
                    for item in assignments
                )
                and all(label in reply for label in expected_date_labels)
            ),
            "requested_lunch_break_is_reserved_each_day": (
                len(expected_days) == 2
                and len(reserved_breaks) == len(expected_days)
                and all(
                    any(
                        item.get("day") == day
                        and item.get("meal_type") == "LUNCH"
                        and item.get("start_time") == "12:00"
                        and item.get("end_time") == "13:00"
                        and item.get("duration_min") == 60
                        and item.get("time_is_proposed_default") is True
                        for item in reserved_breaks if isinstance(item, dict)
                    )
                    for day in expected_days
                )
                and all(
                    item.get("day") not in expected_days
                    or not ("12:00" <= str(item.get("start_time", "")) < "13:00")
                    for item in assignments if isinstance(item, dict)
                )
                and "未安排餐厅" in reply
            ),
            "edit_does_not_trigger_unrequested_place_search": "search_places" not in tools,
            "schedule_draft_verified": "build_schedule_draft" in tools,
            "no_formal_write_before_confirmation": resources.get("formal_write_prevented") is True,
        })
    elif scenario_id == "lunch-only":
        candidates = final_turn.get("verified_candidates", [])
        checks.update({
            "food_candidates_present": bool(candidates),
            "all_candidates_are_food": bool(candidates) and all(
                item.get("planning_category") == "FOOD" for item in candidates
            ),
            "candidates_labeled_as_lunch": bool(candidates) and all(
                item.get("meal_type") == "LUNCH" for item in candidates
            ),
            "no_itinerary_schedule_action": not bool(
                verified_tools & {"get_distance_matrix", "build_schedule_draft"}
            ) and final_turn.get("proposal_present") is not True,
        })
    elif scenario_id == "mixed-attraction-lunch":
        candidates = final_turn.get("verified_candidates", [])
        categories = {item.get("planning_category") for item in candidates}
        food_candidates = [
            item for item in candidates if item.get("planning_category") == "FOOD"
        ]
        checks.update({
            "both_requested_domains_have_verified_candidates": (
                {"FOOD", "ATTRACTION"} <= categories
                and all(item.get("verification_status") in {"VERIFIED", "SIMULATED"}
                        for item in candidates)
            ),
            "food_candidates_are_labeled_as_lunch": bool(food_candidates)
            and all(item.get("meal_type") == "LUNCH" for item in food_candidates),
            "recommendation_does_not_create_itinerary": not bool(
                verified_tools & {"get_distance_matrix", "build_schedule_draft"}
            ) and final_turn.get("proposal_present") is not True
            and final_turn.get("draft_present") is not True,
        })
    elif scenario_id == "mixed-attraction-lunch-draft":
        candidates = final_turn.get("verified_candidates", [])
        draft = resources.get("draft_preview", {})
        stops = draft.get("stops", []) if isinstance(draft, dict) else []
        categories = {item.get("planning_category") for item in candidates}
        food_stops = [item for item in stops if item.get("planning_category") == "FOOD"]
        attraction_stops = [
            item for item in stops if item.get("planning_category") == "ATTRACTION"
        ]
        checks.update({
            "search_returned_food_and_attraction": {"FOOD", "ATTRACTION"} <= categories,
            "explicit_candidate_choice_created_preview": (
                isinstance(draft, dict) and len(stops) == 2
                and {item.get("planning_category") for item in stops}
                == {"FOOD", "ATTRACTION"}
            ),
            "lunch_slot_and_time_are_preserved": bool(food_stops)
            and all(item.get("meal_type") == "LUNCH" for item in food_stops)
            and all(item.get("start_time", "")[:5] == "12:00" for item in food_stops),
            "attraction_is_not_mislabeled_as_meal": bool(attraction_stops)
            and all(item.get("meal_type") is None for item in attraction_stops),
            "preview_remains_unconfirmed": (
                resources.get("formal_write_prevented") is True
                and resources.get("formal_plan_count_after")
                == resources.get("formal_plan_count_before")
            ),
        })
    elif scenario_id == "lodging-only":
        candidates = final_turn.get("verified_candidates", [])
        checks.update({
            "lodging_candidates_present": bool(candidates),
            "all_candidates_are_lodging": bool(candidates) and all(
                item.get("planning_category") == "LODGING" for item in candidates
            ),
            "lodging_search_verified": "search_places" in verified_tools,
            "no_route_or_itinerary_action": not bool(
                verified_tools & {"get_distance_matrix", "build_schedule_draft"}
            ) and final_turn.get("proposal_present") is not True,
            "no_booking_or_formal_write": (
                "book_hotel" not in verified_tools
                and resources.get("formal_write_prevented") is True
            ),
        })
    elif scenario_id == "weather-indoor-search":
        actions = final_turn.get("actions", [])
        tool_order = [item.get("tool") for item in actions if isinstance(item, dict)]
        forecast = final_turn.get("weather_evidence", {})
        candidates = final_turn.get("verified_candidates", [])
        checks.update({
            "weather_observed_before_indoor_search": (
                "get_trip_weather" in tool_order
                and "search_places" in tool_order
                and tool_order.index("get_trip_weather") < tool_order.index("search_places")
            ),
            "synthetic_rain_evidence_is_source_labelled": (
                forecast.get("source") == "Local simulated forecast"
                and any(day.get("weather_code") == 61 for day in forecast.get("days", []))
            ),
            "indoor_search_returns_simulated_candidate": (
                bool(candidates)
                and any("室内" in item.get("name", "") for item in candidates)
                and all(item.get("verification_status") == "SIMULATED" for item in candidates)
            ),
            "no_schedule_or_formal_write": not bool(
                verified_tools & {"get_distance_matrix", "build_schedule_draft"}
            ) and final_turn.get("proposal_present") is not True
            and resources.get("formal_write_prevented") is True,
        })
    elif scenario_id == "lunch-weather-preparation":
        actions = final_turn.get("actions", [])
        tools = {item.get("tool") for item in actions if isinstance(item, dict)}
        forecast = final_turn.get("weather_evidence", {})
        candidates = final_turn.get("verified_candidates", [])
        food_candidates = [
            item for item in candidates if item.get("planning_category") == "FOOD"
        ]
        reply = str(final_turn.get("assistant_reply", ""))
        checks.update({
            "meal_search_and_weather_tools_verified": (
                "search_places" in verified_tools and "get_trip_weather" in verified_tools
            ),
            "candidates_are_food_and_labeled_lunch": bool(food_candidates)
            and len(food_candidates) == len(candidates)
            and all(item.get("meal_type") == "LUNCH" for item in food_candidates),
            "weather_is_source_labelled_and_dated": (
                forecast.get("source") == "Local simulated forecast"
                and forecast.get("status") == "AVAILABLE"
                and bool(forecast.get("days"))
                and all(isinstance(day.get("date"), str) for day in forecast.get("days", []))
            ),
            "rain_advice_is_grounded_and_visible": (
                any(day.get("weather_code") in {61, 63, 65} for day in forecast.get("days", []))
                and "建议带伞或轻便雨衣" in reply
            ),
            "identical_daily_rain_advice_is_not_repeated": (
                reply.count("建议带伞或轻便雨衣") == 1
            ),
            "no_sightseeing_route_or_plan_mutation": not bool(
                tools & {"get_trip_route", "get_distance_matrix", "build_schedule_draft"}
            ) and final_turn.get("proposal_present") is not True
            and final_turn.get("draft_present") is not True,
        })
    elif scenario_id == "lunch-weather-weather-outage":
        actions = final_turn.get("actions", [])
        tools = {item.get("tool") for item in actions if isinstance(item, dict)}
        weather_failures = [
            item for item in actions
            if item.get("tool") == "get_trip_weather" and item.get("status") == "FAILED"
        ]
        forecast = final_turn.get("weather_evidence", {})
        candidates = final_turn.get("verified_candidates", [])
        reply = str(final_turn.get("assistant_reply", ""))
        food_candidates = [
            item for item in candidates if item.get("planning_category") == "FOOD"
        ]
        checks.update({
            "meal_search_succeeds_despite_weather_outage": (
                "search_places" in verified_tools and "get_trip_weather" in tools
                and bool(weather_failures)
                and any(item.get("error") == "WEATHER_PROVIDER_UNAVAILABLE" for item in weather_failures)
            ),
            "food_candidates_keep_lunch_label": bool(food_candidates)
            and len(food_candidates) == len(candidates)
            and all(item.get("meal_type") == "LUNCH" for item in food_candidates),
            "weather_failure_is_disclosed_without_invented_forecast": (
                "天气服务暂时不可用" in reply
                and not forecast.get("days")
                and "建议带伞或轻便雨衣" not in reply
            ),
            "no_sightseeing_route_or_plan_mutation": not bool(
                tools & {"get_trip_route", "get_distance_matrix", "build_schedule_draft"}
            ) and final_turn.get("proposal_present") is not True
            and final_turn.get("draft_present") is not True,
        })
    elif scenario_id == "weather-preparation":
        actions = final_turn.get("actions", [])
        forecast = final_turn.get("weather_evidence", {})
        reply = str(final_turn.get("assistant_reply", ""))
        checks.update({
            "forecast_tool_verified": any(
                item.get("tool") == "get_trip_weather" and item.get("status") == "VERIFIED"
                for item in actions if isinstance(item, dict)
            ),
            "forecast_is_source_labelled_and_dated": (
                forecast.get("source") == "Local simulated forecast"
                and forecast.get("status") == "AVAILABLE"
                and bool(forecast.get("days"))
                and all(isinstance(day.get("date"), str) for day in forecast.get("days", []))
            ),
            "rain_advice_is_grounded_in_forecast": (
                any(day.get("weather_code") in {51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82, 85, 86, 95, 96, 99}
                    for day in forecast.get("days", []))
                and "建议带伞或轻便雨衣" in reply
            ),
            "no_search_route_or_schedule_mutation": not bool(
                {item.get("tool") for item in actions if isinstance(item, dict)}
                & {"search_places", "get_trip_route", "get_distance_matrix", "build_schedule_draft"}
            ) and final_turn.get("proposal_present") is not True
            and final_turn.get("draft_present") is not True,
            "formal_write_prevented": resources.get("formal_write_prevented") is True,
        })
    elif scenario_id == "route-weather-preparation":
        actions = final_turn.get("actions", [])
        tools = [item.get("tool") for item in actions if isinstance(item, dict)]
        forecast = final_turn.get("weather_evidence", {})
        route = final_turn.get("route_evidence", {})
        stage_statuses = final_turn.get("stage_statuses", [])
        stage_names = [
            item.get("stage") for item in stage_statuses if isinstance(item, dict)
        ]
        checks.update({
            "route_and_weather_stages_completed": (
                "ROUTE" in stage_names and "SEARCH_WEATHER" in stage_names
                and all(item.get("status") == "SUCCEEDED" for item in stage_statuses)
            ),
            "route_and_weather_tools_verified": (
                "get_trip_route" in tools and "get_trip_weather" in tools
                and all(
                    item.get("status") == "VERIFIED"
                    for item in actions
                    if item.get("tool") in {"get_trip_route", "get_trip_weather"}
                )
            ),
            "route_uses_requested_endpoints_and_mode": (
                route.get("origin_name") == "人民公园"
                and route.get("destination_name") == "武侯祠"
                and route.get("mode") == "DRIVING"
                and isinstance(route.get("duration_min"), int)
            ),
            "weather_is_dated_and_source_labelled": (
                forecast.get("source") == "Local simulated forecast"
                and bool(forecast.get("days"))
                and all(isinstance(day.get("date"), str) for day in forecast.get("days", []))
            ),
            "reply_combines_route_and_weather_preparation": (
                "人民公园" in str(final_turn.get("assistant_reply", ""))
                and "武侯祠" in str(final_turn.get("assistant_reply", ""))
                and "建议带伞或轻便雨衣" in str(final_turn.get("assistant_reply", ""))
            ),
            "reply_does_not_repeat_identical_weather_advice": (
                str(final_turn.get("assistant_reply", "")).count("建议带伞或轻便雨衣") == 1
            ),
            "no_schedule_or_formal_write": (
                not bool(set(tools) & {"get_distance_matrix", "build_schedule_draft"})
                and final_turn.get("proposal_present") is not True
                and final_turn.get("draft_present") is not True
                and resources.get("formal_write_prevented") is True
            ),
        })
    elif scenario_id == "route-weather-route-outage":
        actions = final_turn.get("actions", [])
        tools = [item.get("tool") for item in actions if isinstance(item, dict)]
        reply = str(final_turn.get("assistant_reply", ""))
        forecast = final_turn.get("weather_evidence", {})
        route_failures = [
            item for item in actions
            if item.get("tool") == "get_trip_route" and item.get("status") == "FAILED"
        ]
        checks.update({
            "direct_route_failure_is_visible": bool(route_failures) and any(
                item.get("error") == "MAPS_PROVIDER_UNAVAILABLE" for item in route_failures
            ),
            "weather_forecast_remains_available": (
                "get_trip_weather" in tools
                and forecast.get("source") == "Local simulated forecast"
                and bool(forecast.get("days"))
            ),
            "weather_advice_remains_in_reply": "建议带伞或轻便雨衣" in reply,
            "route_failure_is_disclosed": "路线本轮未能核验" in reply,
            "failed_route_is_not_claimed_verified": "已核验人民公园到武侯祠" not in reply,
            "no_schedule_or_formal_write": (
                not bool(set(tools) & {"get_distance_matrix", "build_schedule_draft"})
                and final_turn.get("proposal_present") is not True
                and final_turn.get("draft_present") is not True
                and resources.get("formal_write_prevented") is True
            ),
        })
    elif scenario_id == "route-weather-weather-outage":
        actions = final_turn.get("actions", [])
        tools = [item.get("tool") for item in actions if isinstance(item, dict)]
        reply = str(final_turn.get("assistant_reply", ""))
        route = final_turn.get("route_evidence", {})
        weather_failures = [
            item for item in actions
            if item.get("tool") == "get_trip_weather" and item.get("status") == "FAILED"
        ]
        checks.update({
            "route_result_remains_visible": (
                "get_trip_route" in tools and route.get("origin_name") == "人民公园"
                and route.get("destination_name") == "武侯祠"
                and "已核验人民公园到武侯祠的驾车路线" in reply
            ),
            "weather_provider_failure_is_visible": bool(weather_failures) and any(
                item.get("error") == "WEATHER_PROVIDER_UNAVAILABLE" for item in weather_failures
            ),
            "weather_unavailability_is_disclosed": "天气服务暂时不可用" in reply,
            "no_forecast_claim_is_made": not bool(
                final_turn.get("weather_evidence", {}).get("days")
            ),
            "no_schedule_or_formal_write": (
                not bool(set(tools) & {"get_distance_matrix", "build_schedule_draft"})
                and final_turn.get("proposal_present") is not True
                and final_turn.get("draft_present") is not True
                and resources.get("formal_write_prevented") is True
            ),
        })
    elif scenario_id == "unsupported-intercity-weather":
        actions = [
            item for turn in turns for item in turn.get("actions", [])
            if isinstance(item, dict)
        ]
        forecast = final_turn.get("weather_evidence", {})
        # Pending questions are rendered as a structured card in the product UI,
        # so include that visible text when scoring whether the limitation and
        # no-substitution boundary were communicated to the user.
        reply = "\n".join(
            str(final_turn.get(field, ""))
            for field in ("assistant_reply", "pending_question")
            if final_turn.get(field)
        )
        checks.update({
            "weather_query_succeeds": "get_trip_weather" in verified_tools,
            "weather_evidence_is_source_labelled_and_dated": (
                forecast.get("status") == "AVAILABLE"
                and forecast.get("source") == "Local simulated forecast"
                and bool(forecast.get("days"))
                and all(isinstance(day.get("date"), str) for day in forecast.get("days", []))
            ),
            "intercity_rail_unavailability_is_disclosed": (
                "跨城铁路和航班查询尚未接入" in reply
                and "班次、时刻或票价" in reply
            ),
            "no_route_tool_is_called_without_supported_mode_consent": not bool(
                {"get_trip_route", "get_distance_matrix"} & {
                    item.get("tool") for item in actions
                    if item.get("status") == "VERIFIED"
                }
            ),
            "no_automatic_driving_substitution": "不会自动改成驾车路线" in reply,
            "no_schedule_or_formal_write": not bool(
                verified_tools & {"build_schedule_draft"}
            ) and final_turn.get("proposal_present") is not True
            and resources.get("formal_write_prevented") is True,
        })
    elif scenario_id == "general-trip-question":
        reply = str(final_turn.get("assistant_reply", ""))
        known_places = final_turn.get("trip_context", {}).get("saved_places", [])
        checks.update({
            "grounded_reply_present": (
                final_turn.get("assistant_reply_present") is True
                and final_turn.get("response_source") == "MODEL"
            ),
            "reply_uses_trip_destination": "成都" in reply,
            "reply_mentions_a_saved_place": bool(known_places) and any(
                name in reply for name in known_places if isinstance(name, str)
            ),
            "no_search_or_plan_mutation": (
                final_turn.get("action_count") == 0
                and final_turn.get("proposal_present") is not True
                and final_turn.get("draft_present") is not True
            ),
            "formal_write_prevented": resources.get("formal_write_prevented") is True,
        })
    elif scenario_id == "route-provider-outage":
        actions = final_turn.get("actions", [])
        route_failures = [
            item for item in actions
            if item.get("tool") == "get_distance_matrix" and item.get("status") == "FAILED"
        ]
        draft_actions = [
            item for item in actions
            if item.get("tool") == "build_schedule_draft" and item.get("status") == "VERIFIED"
        ]
        reply = str(final_turn.get("assistant_reply", ""))
        checks.update({
            "route_failure_is_visible_in_trace": bool(route_failures) and any(
                item.get("error") == "MAPS_PROVIDER_UNAVAILABLE" for item in route_failures
            ),
            "failed_route_is_not_claimed_verified": all(
                item.get("verified") is not True for item in route_failures
            ),
            "draft_fallback_runs_only_after_route_failure": (
                bool(route_failures) and bool(draft_actions)
                and max(actions.index(route_failures[-1]), 0) < actions.index(draft_actions[0])
            ),
            "route_estimate_limit_is_disclosed": (
                "本地距离估算" in reply
                and ("路线尚未核验" in reply or "路线未核验" in reply)
            ),
            "proposal_remains_unconfirmed": (
                final_turn.get("proposal_present") is True
                and resources.get("formal_write_prevented") is True
            ),
        })
    else:
        raise ValueError(f"unknown product evaluation scenario: {scenario_id}")
    return {"checks": checks, "passed": all(checks.values())}


def _latest_assistant(session: dict[str, Any]) -> dict[str, Any]:
    messages = session.get("messages")
    if not isinstance(messages, list):
        return {}
    return next((item for item in reversed(messages) if item.get("role") == "assistant"), {})


def _turn_metrics(
    session: dict[str, Any],
    status_code: int,
    *,
    user_message: str,
    trip_context: dict[str, Any],
    previous_model_usage: dict[str, int] | None = None,
) -> dict[str, Any]:
    assistant = _latest_assistant(session)
    reply = str(assistant.get("content", ""))
    payload = assistant.get("payload") if isinstance(assistant.get("payload"), dict) else {}
    trace = payload.get("agent_trace") if isinstance(payload.get("agent_trace"), dict) else {}
    transitions = trace.get("transitions") if isinstance(trace.get("transitions"), list) else []
    actions = []
    for event in transitions:
        if not isinstance(event, dict):
            continue
        action = {key: event.get(key) for key in ("kind", "tool", "status", "verified", "error")
                  if event.get(key) is not None}
        if action:
            actions.append(action)
    task = payload.get("agent_task") if isinstance(payload.get("agent_task"), dict) else {}
    schedule_suggestions = payload.get("schedule_suggestions")
    has_schedule_suggestions = isinstance(schedule_suggestions, list) and bool(schedule_suggestions)
    adjustments = payload.get("adjustments_applied")
    context = session.get("context") if isinstance(session.get("context"), dict) else {}
    usage_value = context.get("advisor_llm_usage")
    usage_total = {
        key: value for key in ("input_tokens", "output_tokens", "total_tokens")
        if isinstance(usage_value, dict)
        and isinstance((value := usage_value.get(key)), int)
        and not isinstance(value, bool)
        and value >= 0
    }
    prior_usage = previous_model_usage or {}
    usage_delta = {
        key: max(0, value - prior_usage.get(key, 0))
        for key, value in usage_total.items()
    }
    return {
        "http_status": status_code,
        "user_message": user_message[:1200],
        "trip_context": {
            "destination": str(trip_context.get("destination", ""))[:100],
            "start_date": str(trip_context.get("start_date", ""))[:10],
            "end_date": str(trip_context.get("end_date", ""))[:10],
            "pace_level": trip_context.get("pace_level"),
            "saved_places": [
                str(name)[:100] for name in trip_context.get("saved_places", [])[:12]
                if isinstance(name, str)
            ],
            "map_evidence": str(trip_context.get(
                "map_evidence", "Fake Maps; synthetic fixture data",
            ))[:120],
        },
        "assistant_reply_present": bool(reply),
        "assistant_reply_excerpt": reply[:600],
        "assistant_reply": reply[:1600],
        "assistant_reply_complete": len(reply) <= 1600,
        "task_status": task.get("status"),
        "task_turn_number": task.get("turn_number"),
        "pending_question": payload.get("pending_question"),
        "agent_action": payload.get("agent_action"),
        "response_source": payload.get("response_source"),
        "response_fallback_reason": payload.get("response_fallback_reason"),
        "extraction_source": payload.get("extraction_source"),
        "model_fallback_reason": payload.get("model_fallback_reason"),
        "adjustments_applied": [
            item[:120] for item in adjustments[:12] if isinstance(item, str)
        ] if isinstance(adjustments, list) else [],
        "action_count": len(actions),
        "actions": actions[:12],
        "draft_present": isinstance(session.get("draft"), dict),
        "draft_version": session.get("draft_version"),
        "proposal_present": task.get("status") == "PROPOSED" or has_schedule_suggestions,
        "schedule_suggestion_count": len(schedule_suggestions) if isinstance(schedule_suggestions, list) else 0,
        "schedule_assignments": [
            {
                key: item.get(key)
                for key in ("day", "start_time")
                if item.get(key) is not None
            }
            for item in schedule_suggestions[:32]
            if isinstance(item, dict)
        ] if isinstance(schedule_suggestions, list) else [],
        "schedule_breaks": [
            {
                key: item.get(key)
                for key in (
                    "day", "meal_type", "label", "start_time", "end_time",
                    "duration_min", "time_is_proposed_default",
                )
                if item.get(key) is not None
            }
            for item in payload.get("schedule_breaks", [])[:32]
            if isinstance(item, dict)
        ] if isinstance(payload.get("schedule_breaks"), list) else [],
        "draft_follow_up_options": [
            {
                key: item.get(key)
                for key in ("key", "title", "action_label")
                if item.get(key) is not None
            }
            for item in payload.get("draft_follow_up_options", [])[:4]
            if isinstance(item, dict)
        ] if isinstance(payload.get("draft_follow_up_options"), list) else [],
        "verified_candidates": [
            {
                key: item.get(key) for key in (
                    "name", "planning_category", "meal_type", "verification_status",
                ) if item.get(key) is not None
            }
            for item in payload.get("verified_candidates", [])[:12]
            if isinstance(item, dict)
        ] if isinstance(payload.get("verified_candidates"), list) else [],
        "event": payload.get("event"),
        "destination_recommendations": [
            {
                "city": str(item.get("city", ""))[:80],
                "provider": str(item.get("provider", ""))[:80],
                "places": [
                    {
                        "name": str(place.get("name", ""))[:120],
                        "provider": str(place.get("provider", ""))[:40],
                        "provider_id": str(place.get("provider_id", ""))[:120],
                    }
                    for place in item.get("places", [])[:8]
                    if isinstance(place, dict)
                ] if isinstance(item.get("places"), list) else [],
            }
            for item in payload.get("recommendations", [])[:3]
            if isinstance(item, dict)
        ] if isinstance(payload.get("recommendations"), list) else [],
        "destination_search_count": payload.get("search_count"),
        "destination_failed_search_count": payload.get("failed_search_count"),
        "destination_agent_trace": {
            key: trace.get(key)
            for key in ("status", "decision_source", "decision_summary", "transitions")
            if key in trace
        } if payload.get("event") == "DESTINATION_RECOMMENDATIONS" else None,
        "weather_evidence": _bounded_weather_evidence(payload.get("weather_forecast")),
        "route_evidence": _bounded_route_evidence(payload.get("transport_route")),
        "stage_statuses": [
            {
                "stage": item.get("stage"),
                "status": item.get("status"),
                "error_code": item.get("error_code"),
                "failure_type": item.get("failure_type"),
            }
            for item in trace.get("stage_statuses", [])[:4]
            if isinstance(item, dict)
        ] if isinstance(trace.get("stage_statuses"), list) else [],
        "stage_decision_history": [
            {
                "eligible_stages": item.get("eligible_stages", [])[:4],
                "order": item.get("order", [])[:4],
                "decision_source": item.get("decision_source"),
                "fallback_reason": item.get("fallback_reason"),
            }
            for item in (trace.get("turn_orchestration") or {}).get("decision_history", [])[:4]
            if isinstance(item, dict)
        ] if isinstance(trace.get("turn_orchestration"), dict) else [],
        "evidence_reuse": {
            key: str(trace.get("evidence_reuse", {}).get(key, "NOT_REPORTED"))[:48]
            for key in ("weather", "route", "pending_scope")
        } if isinstance(trace.get("evidence_reuse"), dict) else {},
        "model_usage": usage_delta,
        "model_usage_session_total": usage_total,
    }


def _bounded_weather_evidence(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {
        key: value.get(key)
        for key in ("status", "city", "source", "fetched_at")
        if value.get(key) is not None
    } | {
        "days": [
            {
                key: item.get(key) for key in (
                    "date", "description", "weather_code", "precipitation_probability_max",
                ) if item.get(key) is not None
            } | {
                "advice": [
                    advice.get("text", "")[:160]
                    for advice in item.get("advice", [])[:4]
                    if isinstance(advice, dict) and isinstance(advice.get("text"), str)
                ]
            }
            for item in value.get("days", [])[:4]
            if isinstance(item, dict)
        ] if isinstance(value.get("days"), list) else [],
    }


def _bounded_route_evidence(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {
        key: value.get(key)
        for key in (
            "origin_name", "destination_name", "mode", "distance_m", "duration_min",
            "source", "retrieved_at", "verification_status",
        )
        if value.get(key) is not None
    }


async def run_eval(
    *, base_url: str, output: Path, server_output_cap: int | None = None,
    server_session_cap: int | None = None, trip_start_date: date | None = None,
    scenario_id: str = "schedule-two-turn", model_is_local_stub: bool = False,
) -> dict[str, Any]:
    if output.exists():
        raise FileExistsError(f"refusing to overwrite evaluation report: {output}")
    scenario = SCENARIOS.get(scenario_id)
    if scenario is None:
        raise ValueError(f"unknown product evaluation scenario: {scenario_id}")
    start, end = _resolve_trip_dates(trip_start_date)
    provider_mode = _provider_mode(settings.llm_base_url, model_is_local_stub=model_is_local_stub)
    if provider_mode.startswith("LIVE_MODEL") and (
        server_output_cap is None or server_session_cap is None
    ):
        raise ValueError("live product evaluation requires explicit per-turn and session output caps")
    if server_output_cap is not None and server_output_cap < 1:
        raise ValueError("server_output_cap must be positive")
    if server_session_cap is not None and server_session_cap < 1:
        raise ValueError("server_session_cap must be positive")
    username = f"eval{uuid4().hex[:10]}"
    email = f"travelpilot-{uuid4().hex[:12]}@example.invalid"
    password = secrets.token_urlsafe(24)
    registered = False
    authenticated = False
    csrf = ""
    account_deleted = False
    failure: dict[str, Any] | None = None
    turns: list[dict[str, Any]] = []
    created_resources: dict[str, Any] = {}
    expected_runtime_sources = advisor_runtime_source_manifest()
    expected_runtime_fingerprint = fingerprint_manifest(expected_runtime_sources)
    api_runtime_fingerprint: str | None = None
    effective_server_token_caps: dict[str, int] | None = None

    async with httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=180.0) as client:
        health = await client.get("/health")
        api_runtime_fingerprint = _verify_api_runtime(
            health, expected_runtime_fingerprint,
        )
        try:
            response = await client.post("/api/v1/auth/register", json={
                "email": email, "username": username, "display_name": "临时评测账户",
                "password": password,
            })
            if response.status_code != 202:
                raise RuntimeError(f"REGISTER_HTTP_{response.status_code}")
            registered = True
            mailbox = await client.get(f"/api/v1/auth/dev-mailbox/{email}")
            if mailbox.status_code != 200:
                raise RuntimeError(f"MAILBOX_HTTP_{mailbox.status_code}")
            link = mailbox.json().get("link", "")
            token = parse_qs(urlparse(link).query).get("token", [""])[0]
            if not token:
                raise RuntimeError("VERIFY_TOKEN_MISSING")
            verified = await client.post("/api/v1/auth/verify-email", json={"token": token})
            if verified.status_code >= 400:
                raise RuntimeError(f"VERIFY_HTTP_{verified.status_code}")
            login = await client.post("/api/v1/auth/login", json={
                "account": email, "password": password,
            })
            if login.status_code >= 400:
                raise RuntimeError(f"LOGIN_HTTP_{login.status_code}")
            authenticated = True
            csrf = unquote(client.cookies.get("travelpilot_csrf", ""))
            if not csrf:
                raise RuntimeError("CSRF_COOKIE_MISSING")
            headers = {"X-CSRF-Token": csrf}

            if server_output_cap is not None and server_session_cap is not None:
                runtime_status = await client.get("/api/v1/advisor/status")
                effective_server_token_caps = _verify_server_token_caps(
                    runtime_status,
                    max_output_tokens_per_turn=server_output_cap,
                    max_output_tokens_per_session=server_session_cap,
                    expected_model=settings.llm_model if provider_mode.startswith("LIVE_MODEL") else None,
                )

            discovery_mode = scenario.get("advisor_mode") == "discover"
            trip_id = None
            if discovery_mode:
                created_resources["trip_created"] = False
                created_resources["formal_write_prevented"] = True
            else:
                trip_response = await client.post("/api/v1/trips", headers=headers, json={
                    "destination": "成都", "start_date": start.isoformat(),
                    "end_date": end.isoformat(), "pace_level": 2,
                })
                if trip_response.status_code != 201:
                    raise RuntimeError(f"TRIP_HTTP_{trip_response.status_code}")
                trip_id = trip_response.json()["id"]
                created_resources["trip_created"] = True
                for name, latitude, longitude in scenario["saved_places"]:
                    saved = await client.post(f"/api/v1/trips/{trip_id}/wishlist", headers=headers, json={
                        "name": name, "category": "ATTRACTION", "latitude": latitude,
                        "longitude": longitude, "suggested_duration_min": 90, "priority": 3,
                    })
                    if saved.status_code != 201:
                        raise RuntimeError(f"WISHLIST_HTTP_{saved.status_code}")
            session_body = {"mode": "discover"} if discovery_mode else {
                "mode": "existing", "trip_id": trip_id,
            }
            session_response = await client.post(
                "/api/v1/advisor/sessions", headers=headers, json=session_body,
            )
            if session_response.status_code != 201:
                raise RuntimeError(f"ADVISOR_SESSION_HTTP_{session_response.status_code}")
            session = session_response.json()
            session_id = session["id"]
            created_resources["session_created"] = True

            if trip_id is not None:
                plans_before = await client.get(f"/api/v1/trips/{trip_id}/plans")
                if plans_before.status_code != 200:
                    raise RuntimeError(f"PLANS_BEFORE_HTTP_{plans_before.status_code}")
                created_resources["formal_plan_count_before"] = len(plans_before.json())
            else:
                created_resources["formal_plan_count_before"] = 0

            review_context = {
                "destination": "" if discovery_mode else "成都",
                "start_date": "" if discovery_mode else start.isoformat(),
                "end_date": "" if discovery_mode else end.isoformat(),
                "pace_level": 2,
                "saved_places": [item[0] for item in scenario["saved_places"]],
                "map_evidence": (
                    "AMap Web Service" if discovery_mode else "Fake Maps; synthetic fixture data"
                ),
            }

            previous_model_usage: dict[str, int] = {}
            for index, message in enumerate(scenario["turn_inputs"], start=1):
                request_started = perf_counter()
                response = await client.post(
                    f"/api/v1/advisor/sessions/{session_id}/messages",
                    headers={**headers, "Idempotency-Key": f"live-product-eval-{uuid4().hex[:20]}"},
                    json={"content": message},
                )
                request_latency_ms = round((perf_counter() - request_started) * 1000, 2)
                if response.status_code >= 400:
                    turns.append({
                        "turn": index, "http_status": response.status_code,
                        "request_latency_ms": request_latency_ms,
                    })
                    raise RuntimeError(f"ADVISOR_TURN_{index}_HTTP_{response.status_code}")
                session = response.json()
                metric = _turn_metrics(
                    session,
                    response.status_code,
                    user_message=message,
                    trip_context=review_context,
                    previous_model_usage=previous_model_usage,
                )
                metric["turn"] = index
                metric["request_latency_ms"] = request_latency_ms
                turns.append(metric)
                _validate_turn_output_cap(metric, server_output_cap)
                previous_model_usage = metric["model_usage_session_total"]

            draft_selection = scenario.get("draft_selection")
            if isinstance(draft_selection, dict):
                session_candidates = session.get("candidates", [])
                selected = {}
                for category in draft_selection.get("categories", []):
                    candidate = next((
                        item for item in session_candidates
                        if isinstance(item, dict)
                        and item.get("planning_category") == category
                        and item.get("verification_status") in {"VERIFIED", "SIMULATED"}
                    ), None)
                    if candidate is None:
                        raise RuntimeError(f"DRAFT_CANDIDATE_MISSING_{category}")
                    selected[category] = candidate
                draft_payload = {
                    "candidate_ids": [item["id"] for item in selected.values()],
                    "schedules": [
                        {
                            "candidate_id": item["id"],
                            "day": start.isoformat(),
                            "start_time": draft_selection["start_times"][category],
                        }
                        for category, item in selected.items()
                    ],
                }
                draft_response = await client.post(
                    f"/api/v1/advisor/sessions/{session_id}/draft",
                    headers={**headers, "Idempotency-Key": f"mixed-draft-{uuid4().hex[:20]}"},
                    json=draft_payload,
                )
                if draft_response.status_code >= 400:
                    raise RuntimeError(f"DRAFT_HTTP_{draft_response.status_code}")
                session = draft_response.json()
                created_resources["draft_preview"] = session.get("draft")

            if trip_id is not None:
                plans_after = await client.get(f"/api/v1/trips/{trip_id}/plans")
                if plans_after.status_code != 200:
                    raise RuntimeError(f"PLANS_AFTER_HTTP_{plans_after.status_code}")
                created_resources["formal_plan_count_after"] = len(plans_after.json())
                created_resources["formal_write_prevented"] = (
                    created_resources["formal_plan_count_after"]
                    == created_resources["formal_plan_count_before"]
                )
            else:
                created_resources["formal_plan_count_after"] = 0
        except (httpx.HTTPError, RuntimeError, KeyError, ValueError, TypeError) as exc:
            message = str(exc)
            error_code = message if message.replace("_", "").isalnum() else "EVALUATION_REQUEST_FAILED"
            failure = {"error_type": type(exc).__name__, "error_code": error_code[:80]}
        finally:
            if registered and not authenticated:
                try:
                    login = await client.post("/api/v1/auth/login", json={
                        "account": email, "password": password,
                    })
                    authenticated = login.status_code < 400
                    csrf = unquote(client.cookies.get("travelpilot_csrf", ""))
                except httpx.HTTPError:
                    authenticated = False
            if registered and authenticated and csrf:
                try:
                    deleted = await client.delete("/api/v1/account", headers={"X-CSRF-Token": csrf})
                    account_deleted = deleted.status_code == 204
                except httpx.HTTPError:
                    account_deleted = False

    product_assessment = _assess_product_outcome(
        turns, created_resources, account_deleted, scenario_id,
    )
    report = {
        "evaluation_id": f"advisor_product_api_{scenario_id}_v1",
        "scenario_id": scenario_id,
        "weather_data_mode": scenario.get("weather_data_mode", "NOT_REQUESTED_OR_OUT_OF_RANGE"),
        "maps_data_mode": scenario.get("maps_data_mode", "FAKE_MAPS"),
        "provider_mode": provider_mode,
        "model": _reported_model_name(
            provider_mode,
            effective_server_token_caps=effective_server_token_caps,
            configured_model=settings.llm_model,
        ),
        "base_url_origin": base_url.split("/api/", 1)[0],
        "trip_start_date": start.isoformat(),
        "api_runtime_fingerprint": api_runtime_fingerprint,
        "api_runtime_source_sha256": expected_runtime_sources,
        "server_output_cap_per_turn": server_output_cap,
        "server_session_output_cap": server_session_cap,
        "effective_server_token_caps": effective_server_token_caps,
        "runner_process_output_cap_per_turn": settings.llm_max_tokens_per_turn,
        "runner_process_session_output_cap": settings.llm_max_output_tokens_per_session,
        "turn_inputs_count": len(scenario["turn_inputs"]),
        "turns": turns,
        "resources": created_resources,
        "product_assessment": product_assessment,
        "temporary_account_deleted": account_deleted,
        "failure": failure,
        "source_provenance": evaluation_provenance("advisor_product_live"),
        "run_at_utc": datetime.now(UTC).isoformat(),
        "scope": f"{len(scenario['turn_inputs'])} natural-language turn(s) were sent through the authenticated Advisor message endpoint against a temporary user-owned trip and local Fake Maps. Maps mode: {scenario.get('maps_data_mode', 'FAKE_MAPS')}. The model provider mode is reported separately so loopback stubs are not mistaken for live model evidence. Weather mode: {scenario.get('weather_data_mode', 'no real forecast request')}. No confirmation endpoint was called. This is one product-service scenario, not a broad quality benchmark.",
        "limitations": [
            "One scenario run; local model stubs do not establish live-model quality.",
            "Map results use Fake Maps; route quality is not validated against AMap.",
            "This harness does not test browser rendering or human satisfaction.",
            "If temporary_account_deleted is false, clean up the uniquely named evaluation account manually.",
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8015")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--server-output-cap", type=int)
    parser.add_argument("--server-session-cap", type=int)
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), default="schedule-two-turn")
    parser.add_argument(
        "--trip-start-date", type=date.fromisoformat,
        help="Freeze the synthetic trip start date for paired runs (ISO YYYY-MM-DD)",
    )
    args = parser.parse_args()
    result = asyncio.run(run_eval(
        base_url=args.base_url, output=args.output,
        server_output_cap=args.server_output_cap,
        server_session_cap=args.server_session_cap,
        trip_start_date=args.trip_start_date,
        scenario_id=args.scenario,
    ))
    print(json.dumps({
        "evaluation_id": result["evaluation_id"],
        "turn_count": len(result["turns"]),
        "turn_statuses": [turn.get("task_status") for turn in result["turns"]],
        "draft_present": [turn.get("draft_present") for turn in result["turns"]],
        "formal_write_prevented": result["resources"].get("formal_write_prevented"),
        "temporary_account_deleted": result["temporary_account_deleted"],
        "product_assessment": result["product_assessment"],
        "scenario_id": result["scenario_id"],
        "failure": result["failure"], "output": str(args.output),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
