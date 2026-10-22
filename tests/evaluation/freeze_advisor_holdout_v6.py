"""Freeze a fresh, action-owned two-turn Advisor diagnostic holdout."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from tests.evaluation.advisor_action_ownership import action_owner

ROOT = Path(__file__).resolve().parents[2]
CASES = ROOT / "tests/evaluation/cases"
OUTPUT = CASES / "advisor_multiturn_holdout_v6.json"
REFERENCE_SOURCE = CASES / "advisor_multiturn_holdout_v5.json"


def _turn(user: str, action: str, tools: list[str]) -> dict[str, Any]:
    row = {"user": user, "expected_action": action, "allowed_tools": tools,
           "expected_status": "WAITING_USER" if action == "ASK_USER" else "COMPLETED"}
    row["action_owner"] = action_owner(row)
    return row


def _case(case_id: str, family: str, task_type: str, state: dict[str, Any],
          first: tuple[str, str, list[str]], second: tuple[str, str, list[str]],
          *invariants: str) -> dict[str, Any]:
    return {
        "id": case_id, "family": family, "task_type": task_type,
        "initial_state": state,
        "turns": [_turn(*first), _turn(*second)],
        "invariants": list(invariants),
    }


def _places(prefix: str, names: tuple[str, ...]) -> list[dict[str, Any]]:
    return [
        {"id": f"{prefix}-{index}", "name": name,
         "latitude": 30.0 + index * 0.01, "longitude": 104.0 + index * 0.01}
        for index, name in enumerate(names, start=1)
    ]


def build_dataset() -> dict[str, Any]:
    schedule_a = _places("v6-schedule-a", ("旧城展馆", "河畔公园", "城市书院"))
    schedule_b = _places("v6-schedule-b", ("海边栈道", "灯塔展馆"))
    fixed_schedule = [
        {**schedule_a[0], "start_time": "10:00", "duration_min": 90, "fixed": True},
        {**schedule_a[1], "start_time": "13:00", "duration_min": 60, "optional": True},
        {**schedule_a[2], "start_time": "15:00", "duration_min": 60},
    ]
    cases = [
        _case("v6-followup-ellipsis-01", "itinerary", "SCHEDULE",
              {"destination": "宁波", "trip_dates": ["2027-05-03", "2027-05-04"], "saved_places": 3, "saved_place_details": schedule_a},
              ("把收藏的三个地方排进两天，第一天别太赶。", "GET_DISTANCE_MATRIX", ["get_distance_matrix", "build_schedule_draft"]),
              ("第二个放到下午，其他照旧。", "GET_DISTANCE_MATRIX", ["get_distance_matrix", "build_schedule_draft"]),
              "resolve_reference_to_second_saved_place", "retain_all_other_stops"),
        _case("v6-user-correction-02", "itinerary", "SCHEDULE",
              {"destination": "南昌", "trip_dates": ["2027-05-06", "2027-05-06"], "saved_places": 3, "saved_place_details": fixed_schedule},
              ("按这几个收藏点排一天。", "GET_DISTANCE_MATRIX", ["get_distance_matrix", "build_schedule_draft"]),
              ("中午那段删掉，上午预约和下午的安排别动。", "GET_DISTANCE_MATRIX", ["get_distance_matrix", "build_schedule_draft"]),
              "preserve_fixed_appointment", "remove_only_requested_optional_stop"),
        _case("v6-schedule-dates-03", "itinerary", "SCHEDULE",
              {"destination": "洛阳", "trip_dates": None, "saved_places": 2, "saved_place_details": _places("v6-schedule-c", ("古墓博物馆", "应天门"))},
              ("帮我把已收藏的地点排一下。", "ASK_USER", []),
              ("五月十号到十二号。", "GET_DISTANCE_MATRIX", ["get_distance_matrix", "build_schedule_draft"]),
              "ask_missing_trip_dates", "carry_dates_into_followup"),
        _case("v6-schedule-capacity-04", "itinerary", "SCHEDULE",
              {"destination": "济南", "trip_dates": ["2027-05-08", "2027-05-08"], "saved_places": 8, "saved_place_details": _places("v6-schedule-d", ("趵突泉", "大明湖", "千佛山", "芙蓉街", "黑虎泉", "解放阁", "泉城广场", "曲水亭街")), "pace": "relaxed"},
              ("一天能把收藏的八个景点都玩完吗？", "ASK_USER", []),
              ("那就只排最重要的四个，其余保留收藏。", "GET_DISTANCE_MATRIX", ["get_distance_matrix", "build_schedule_draft"]),
              "do_not_drop_saved_places_silently", "respect_relaxed_pace"),
        _case("v6-meal-consecutive-days-05", "meal", "MEAL",
              {"destination": "扬州", "trip_dates": ["2027-05-11", "2027-05-12"], "planning_category": "MEAL", "meal_type": "LUNCH"},
              ("帮我找两天午餐，想吃淮扬菜。", "SEARCH_PLACES", ["search_places"]),
              ("第二天换成素食友好的，第一天的条件不变。", "SEARCH_PLACES", ["search_places"]),
              "food_only_candidates", "scope_preference_correction_to_second_day"),
        _case("v6-meal-scope-06", "meal", "MEAL",
              {"destination": "佛山", "trip_dates": ["2027-05-14", "2027-05-14"], "planning_category": "MEAL", "meal_type": "DINNER"},
              ("祖庙附近推荐晚饭，想吃粤菜。", "SEARCH_PLACES", ["search_places"]),
              ("只要餐馆，不用给我加景点或排路线。", "SEARCH_PLACES", ["search_places"]),
              "exclude_attractions_and_routes", "no_formal_write"),
        _case("v6-meal-allergy-07", "meal", "MEAL",
              {"destination": "厦门", "trip_dates": ["2027-05-15", "2027-05-15"], "planning_category": "MEAL", "allergy_constraint": "花生过敏"},
              ("中山路找一家适合晚餐的店。", "SEARCH_PLACES", ["search_places"]),
              ("同行者花生过敏，不能保证就请明确提醒我问店家。", "SEARCH_PLACES", ["search_places"]),
              "retain_allergy_constraint", "do_not_guarantee_allergen_safety"),
        _case("v6-lodging-dates-08", "lodging", "LODGING",
              {"destination": "常州", "trip_dates": None, "planning_category": "LODGING", "travel_party": 2},
              ("找恐龙园附近住的地方，两个人。", "ASK_USER", []),
              ("五月二十日住一晚。", "SEARCH_PLACES", ["search_places"]),
              "ask_dates_before_availability_claim", "never_book"),
        _case("v6-lodging-budget-09", "lodging", "LODGING",
              {"destination": "珠海", "trip_dates": ["2027-05-21", "2027-05-23"], "planning_category": "LODGING", "travel_party": 4, "budget_per_night": 650},
              ("找四个人住两晚、每晚六百五以内的住宿。", "SEARCH_PLACES", ["search_places"]),
              ("房价和房型以酒店确认为准，不要替我预订。", "SEARCH_PLACES", ["search_places"]),
              "retain_party_and_budget", "no_booking_claim"),
        _case("v6-lodging-booking-10", "lodging", "LODGING",
              {"destination": "大理", "trip_dates": ["2027-05-25", "2027-05-27"], "planning_category": "LODGING"},
              ("帮我直接订洱海边的民宿。", "FINISH", []),
              ("那先给住宿候选，我自己核对后下单。", "SEARCH_PLACES", ["search_places"]),
              "disclose_booking_not_supported", "offer_supported_lodging_search"),
        _case("v6-route-explicit-11", "transport", "TRANSPORT",
              {"destination": "苏州", "known_places": _places("v6-route-a", ("苏州站", "拙政园")), "selected_origin_id": "v6-route-a-1", "selected_destination_id": "v6-route-a-2", "origin": "苏州站", "endpoints": "clear"},
              ("从苏州站到拙政园查公交怎么走。", "GET_TRIP_ROUTE", ["get_trip_route"]),
              ("带行李的话少走路优先，没核实的别猜。", "GET_TRIP_ROUTE", ["get_trip_route"]),
              "use_requested_transit_mode", "do_not_invent_accessibility_evidence"),
        _case("v6-route-ambiguous-12", "transport", "TRANSPORT",
              {"destination": "天津", "known_places": _places("v6-route-b", ("天津站", "天津西站", "古文化街")), "origin": None, "endpoints": "origin_ambiguous"},
              ("帮我查去古文化街的路线。", "ASK_USER", []),
              ("从天津西站出发，坐地铁。", "GET_TRIP_ROUTE", ["get_trip_route"]),
              "clarify_ambiguous_origin", "preserve_transit_mode"),
        _case("v6-route-failure-13", "transport", "TRANSPORT",
              {"destination": "桂林", "known_places": _places("v6-route-c", ("桂林站", "象鼻山")), "selected_origin_id": "v6-route-c-1", "selected_destination_id": "v6-route-c-2", "origin": "桂林站", "endpoints": "clear", "route_provider": "timeout"},
              ("查桂林站到象鼻山的驾车路线。", "GET_TRIP_ROUTE", ["get_trip_route"]),
              ("如果地图服务失败，就告诉我无法核验，不要按直线距离估算。", "FINISH", []),
              "disclose_route_provider_failure", "never_substitute_straight_line_distance"),
        _case("v6-weather-missing-date-14", "weather", "WEATHER",
              {"destination": "台州", "trip_dates": None},
              ("帮我看一下出行天气。", "ASK_USER", []),
              ("五月三十和三十一号。", "GET_TRIP_WEATHER", ["get_trip_weather"]),
              "ask_missing_dates", "state_forecast_range"),
        _case("v6-weather-indoor-followup-15", "weather", "DISCOVERY",
              {"destination": "长沙", "trip_dates": ["2027-06-01", "2027-06-01"], "weather": {"status": "AVAILABLE", "precipitation_probability_max": 85}},
              ("那天可能下雨，找室内适合带孩子的地方。", "SEARCH_PLACES", ["search_places", "get_trip_weather"]),
              ("要能坐着休息，别把户外路线混进来。", "SEARCH_PLACES", ["search_places", "get_trip_weather"]),
              "retain_rain_and_child_context", "indoor_only_candidates"),
        _case("v6-preparation-16", "preparation", "PREPARATION",
              {"destination": "吐鲁番", "trip_dates": ["2027-06-04", "2027-06-06"], "weather": None},
              ("出发前帮我看看这几天要准备什么。", "GET_TRIP_WEATHER", ["get_trip_weather"]),
              ("重点提醒高温出行和补水。", "GET_TRIP_WEATHER", ["get_trip_weather"]),
              "ground_advice_in_forecast", "do_not_claim_weather_without_evidence"),
        _case("v6-discovery-preference-17", "discovery", "DISCOVERY",
              {"destination": "景德镇", "trip_dates": ["2027-06-08", "2027-06-09"], "preferences": []},
              ("推荐些适合周末去的地方。", "SEARCH_PLACES", ["search_places"]),
              ("偏陶瓷文化和老街，同行长辈不方便爬坡。", "SEARCH_PLACES", ["search_places"]),
              "retain_culture_and_mobility_preferences", "do_not_invent_accessibility"),
        _case("v6-discovery-open-ended-18", "discovery", "DISCOVERY",
              {"destination": None, "trip_dates": None, "preferences": ["自然", "安静"]},
              ("最近想换个环境待两天，你有什么建议？", "ASK_USER", []),
              ("国内就行，交通别太折腾。", "FINISH", []),
              "ask_one_decisive_missing_question", "do_not_claim_search_without_destination"),
        _case("v6-schedule-opening-19", "itinerary", "SCHEDULE",
              {"destination": "开封", "trip_dates": ["2027-06-12", "2027-06-12"], "saved_places": 2, "saved_place_details": [
                  {"id": "v6-hours-1", "name": "市博物馆", "latitude": 34.8, "longitude": 114.3, "opening_hours": {"open": "09:00", "close": "16:00"}},
                  {"id": "v6-hours-2", "name": "城墙遗址", "latitude": 34.81, "longitude": 114.31, "opening_hours": None},
              ]},
              ("博物馆四点闭馆，帮我安排这两个收藏点。", "GET_DISTANCE_MATRIX", ["get_distance_matrix", "build_schedule_draft"]),
              ("营业时间没有核实的地点请标出来。", "BUILD_SCHEDULE_DRAFT", ["build_schedule_draft"]),
              "respect_known_closing_time", "label_missing_opening_hours"),
        _case("v6-schedule-preserve-20", "itinerary", "SCHEDULE",
              {"destination": "烟台", "trip_dates": ["2027-06-15", "2027-06-16"], "saved_places": 2, "saved_place_details": schedule_b, "fixed_stops": [{"day": 1, "time": "18:00", "name": "已预约演出"}]},
              ("按收藏地点安排两天，第二天轻松一点。", "GET_DISTANCE_MATRIX", ["get_distance_matrix", "build_schedule_draft"]),
              ("把预约演出保留在第一天晚上，其它可以调整。", "GET_DISTANCE_MATRIX", ["get_distance_matrix", "build_schedule_draft"]),
              "preserve_fixed_event", "retain_day_specific_pacing"),
    ]

    reference = json.loads(REFERENCE_SOURCE.read_text(encoding="utf-8"))
    reference_ids = reference["holdout_protocol"]["reference_datasets"]
    protocol = {
        "reference_datasets": reference_ids,
        "reference_dataset_sha256": reference["holdout_protocol"]["reference_dataset_sha256"],
        "claim_boundary": "Freshly authored exploratory tasks with exact ID and normalized-prompt separation; semantic independence is not established.",
        "default_case_ids": [case["id"] for case in cases],
    }
    return {
        "dataset_id": "advisor_multiturn_holdout_v6",
        "version": 6,
        "status": "FROZEN_NOT_SCORED",
        "provider_mode": "synthetic_contract",
        "frozen_before_scoring": True,
        "scope": "20 newly authored two-turn tasks covering context continuation, correction, schedule constraints, meals, lodging, transit, weather, preparation and open-ended discovery.",
        "holdout_protocol": protocol,
        "cases": cases,
    }


def main() -> None:
    dataset = build_dataset()
    OUTPUT.write_text(json.dumps(dataset, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    digest = hashlib.sha256(json.dumps(dataset, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    print(json.dumps({"path": str(OUTPUT), "dataset_id": dataset["dataset_id"], "cases": len(dataset["cases"]), "turns": sum(len(row["turns"]) for row in dataset["cases"]), "canonical_sha256": digest}, ensure_ascii=False))


if __name__ == "__main__":
    main()
