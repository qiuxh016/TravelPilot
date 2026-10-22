"""Create the fresh, capped W1 diagnostic task set before its first score."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CASES = ROOT / "tests/evaluation/cases"
OUTPUT = CASES / "advisor_multiturn_holdout_v4.json"
REFERENCE_IDS = [
    "advisor_multiturn_v1", "advisor_multiturn_v2", "advisor_multiturn_v3",
    "advisor_multiturn_v4", "advisor_multiturn_v5", "advisor_open_consultation_v2",
    "advisor_existing_trip_agent_v3", "advisor_multiturn_holdout_v1",
    "advisor_multiturn_holdout_v2", "advisor_multiturn_holdout_v3",
]


def turn(user: str, action: str, tools: list[str], status: str = "COMPLETED") -> dict:
    return {
        "user": user,
        "expected_action": action,
        "allowed_tools": tools,
        "expected_status": status,
        "action_owner": "AGENT_DECISION",
    }


def case(
    ident: str,
    family: str,
    task_type: str,
    state: dict,
    first: tuple[str, str, list[str], str],
    second: tuple[str, str, list[str], str],
    *invariants: str,
) -> dict:
    return {
        "id": ident,
        "family": family,
        "task_type": task_type,
        "initial_state": state,
        "turns": [turn(*first), turn(*second)],
        "invariants": list(invariants) + ["no_formal_write"],
    }


def main() -> None:
    places = lambda *names: [
        {"id": f"p{index}", "name": name, "latitude": 30.6 + index / 1000,
         "longitude": 104.0 + index / 1000}
        for index, name in enumerate(names)
    ]
    cases = [
        case("v4-cross-day-cluster-01", "schedule", "SCHEDULE",
             {"destination": "南京", "trip_dates": ["2026-12-03", "2026-12-04"],
              "saved_places": 3, "saved_place_details": places("总统府", "六朝博物馆", "鸡鸣寺"),
              "fixed_stops": []},
             ("我收藏的总统府、六朝博物馆和鸡鸣寺分两天逛，尽量少折返。", "GET_DISTANCE_MATRIX", ["get_distance_matrix", "build_schedule_draft"], "PROPOSED"),
             ("第二天上午要开会，下午再出门；别动其他条件。", "BUILD_SCHEDULE_DRAFT", ["build_schedule_draft"], "PROPOSED"),
             "preserve_day_constraint", "preserve_all_selected_places"),
        case("v4-user-correction-02", "schedule", "SCHEDULE",
             {"destination": "青岛", "trip_dates": ["2026-12-11", "2026-12-12"],
              "saved_places": 2, "saved_place_details": places("青岛啤酒博物馆", "小鱼山"), "fixed_stops": []},
             ("先把收藏的两个地方安排一下，第二天轻松些。", "GET_DISTANCE_MATRIX", ["get_distance_matrix", "build_schedule_draft"], "PROPOSED"),
             ("更正一下，轻松日改成第一天，第二天可以排满。", "BUILD_SCHEDULE_DRAFT", ["build_schedule_draft"], "PROPOSED"),
             "apply_latest_correction", "preserve_other_constraints"),
        case("v4-ambiguous-reference-03", "schedule", "SCHEDULE",
             {"destination": "重庆", "trip_dates": ["2026-12-15", "2026-12-15"],
              "saved_places": 3, "saved_place_details": places("湖广会馆", "三峡博物馆", "鹅岭二厂"), "fixed_stops": []},
             ("帮我排一下这三个点，路上别太赶。", "GET_DISTANCE_MATRIX", ["get_distance_matrix", "build_schedule_draft"], "PROPOSED"),
             ("把离得最远的那个换成室内活动，另两个不变。", "ASK_USER", [], "WAITING_USER"),
             "clarify_ambiguous_reference", "preserve_unrelated_stops"),
        case("v4-meal-diet-04", "meal", "MEAL",
             {"destination": "扬州", "trip_dates": ["2026-12-06", "2026-12-06"],
              "saved_places": [], "planning_category": "MEAL", "dietary_preference": "不吃辣"},
             ("东关街附近找扬州炒饭午餐，步行可到，清淡一点。", "SEARCH_PLACES", ["search_places"], "COMPLETED"),
             ("补充：同行有人不吃花生，优先能确认过敏原的店。", "SEARCH_PLACES", ["search_places"], "COMPLETED"),
             "meal_only_results", "respect_allergy_constraint"),
        case("v4-meal-category-correction-05", "meal", "MEAL",
             {"destination": "泉州", "trip_dates": ["2026-12-07", "2026-12-07"],
              "saved_places": [], "planning_category": "MEAL"},
             ("西街附近推荐一顿晚饭，想吃闽南菜。", "SEARCH_PLACES", ["search_places"], "COMPLETED"),
             ("不要景点，我只想找能吃饭的地方，最好营业到九点。", "SEARCH_PLACES", ["search_places"], "COMPLETED"),
             "exclude_non_meal_places", "do_not_assert_unverified_hours"),
        case("v4-lodging-budget-06", "lodging", "LODGING",
             {"destination": "长沙", "trip_dates": ["2026-12-18", "2026-12-20"],
              "saved_places": [], "planning_category": "LODGING", "travel_party": 2},
             ("橘子洲和岳麓山都方便的住处，两个晚上每晚预算不超过四百。", "SEARCH_PLACES", ["search_places"], "COMPLETED"),
             ("最好地铁站步行十分钟内，房价没核实的就标出来。", "SEARCH_PLACES", ["search_places"], "COMPLETED"),
             "preserve_budget", "do_not_claim_booking"),
        case("v4-lodging-booking-boundary-07", "lodging", "LODGING",
             {"destination": "大理", "trip_dates": ["2026-12-21", "2026-12-23"],
              "saved_places": [], "planning_category": "LODGING"},
             ("找一家洱海边的民宿并直接替我订好。", "FINISH", [], "COMPLETED"),
             ("那先给我可选的住宿，暂时不用预订。", "SEARCH_PLACES", ["search_places"], "COMPLETED"),
             "no_external_booking", "offer_supported_alternative"),
        case("v4-route-clear-endpoints-08", "transport", "TRANSPORT",
             {"destination": "广州", "trip_dates": ["2026-12-09", "2026-12-09"],
              "saved_places": ["广州南站", "广东省博物馆"], "endpoints": "clear", "origin": "广州南站"},
             ("广州南站到广东省博物馆怎么走，优先地铁。", "GET_TRIP_ROUTE", ["get_trip_route"], "COMPLETED"),
             ("如果带一个大行李箱，路线会不会需要少走楼梯？", "GET_TRIP_ROUTE", ["get_trip_route"], "COMPLETED"),
             "use_requested_mode", "do_not_invent_accessibility_evidence"),
        case("v4-route-ambiguous-endpoint-09", "transport", "TRANSPORT",
             {"destination": "北京", "trip_dates": ["2026-12-10", "2026-12-10"],
              "saved_places": ["首都机场", "北京南站", "国贸"], "endpoints": "ambiguous"},
             ("帮我查一下去国贸的路线。", "ASK_USER", [], "WAITING_USER"),
             ("从首都机场出发，坐地铁。", "GET_TRIP_ROUTE", ["get_trip_route"], "COMPLETED"),
             "clarify_missing_origin", "use_latest_endpoints"),
        case("v4-weather-follow-up-10", "weather", "WEATHER",
             {"destination": "厦门", "trip_dates": ["2026-12-24", "2026-12-25"],
              "weather_provider": "available"},
             ("跨年去厦门这两天要带伞吗？", "GET_TRIP_WEATHER", ["get_trip_weather"], "COMPLETED"),
             ("如果下雨，第二天附近有什么室内地方可替换？", "SEARCH_PLACES", ["search_places"], "COMPLETED"),
             "ground_weather_claims", "indoor_alternative_only"),
        case("v4-weather-date-clarification-11", "weather", "WEATHER",
             {"destination": "哈尔滨", "trip_dates": None, "weather_provider": "available"},
             ("我下个月去哈尔滨，帮我看看天气和穿什么。", "ASK_USER", [], "WAITING_USER"),
             ("12月14日到16日。", "GET_TRIP_WEATHER", ["get_trip_weather"], "COMPLETED"),
             "ask_exact_dates_for_forecast", "no_assumed_forecast"),
        case("v4-preparation-weather-12", "preparation", "PREPARATION",
             {"destination": "珠海", "trip_dates": ["2026-12-28", "2026-12-29"],
              "weather_provider": "available"},
             ("去珠海海边两天，行李怎么准备？也提醒我天气相关的东西。", "GET_TRIP_WEATHER", ["get_trip_weather"], "COMPLETED"),
             ("我会带小孩，防晒和换洗衣物也考虑进去。", "FINISH", [], "COMPLETED"),
             "child_appropriate_packing", "separate_forecast_from_general_advice"),
        case("v4-discovery-city-13", "discovery", "DISCOVERY",
             {"destination": None, "trip_dates": None, "saved_places": []},
             ("想找一个周末适合看古建筑的地方，先帮我缩小选择。", "ASK_USER", [], "WAITING_USER"),
             ("从杭州出发，最好高铁三小时内。", "SEARCH_PLACES", ["search_places"], "COMPLETED"),
             "ask_city_or_region", "respect_departure_constraint"),
        case("v4-discovery-interest-14", "discovery", "DISCOVERY",
             {"destination": "洛阳", "trip_dates": ["2027-01-09", "2027-01-10"], "saved_places": []},
             ("洛阳两天有哪些值得看的石窟和博物馆？", "SEARCH_PLACES", ["search_places"], "COMPLETED"),
             ("我膝盖不太好，尽量少爬坡，也不要塞太多点。", "SEARCH_PLACES", ["search_places"], "COMPLETED"),
             "respect_mobility_preference", "limit_candidate_density"),
        case("v4-open-consultation-15", "general", "GENERAL",
             {"destination": "景德镇", "trip_dates": ["2027-01-16", "2027-01-17"], "saved_places": []},
             ("第一次去景德镇，住哪片区更方便？简单说下取舍。", "FINISH", [], "COMPLETED"),
             ("我不租车，主要坐公交和打车。", "FINISH", [], "COMPLETED"),
             "answer_open_question", "adapt_to_transport_preference"),
        case("v4-schedule-capacity-16", "schedule", "SCHEDULE",
             {"destination": "武汉", "trip_dates": ["2027-01-20", "2027-01-20"],
              "saved_places": 7, "saved_place_details": places("黄鹤楼", "湖北省博物馆", "东湖", "古德寺", "昙华林", "归元寺", "晴川阁"), "fixed_stops": []},
             ("一天想把这七个收藏地点全走完，午餐至少留一小时。", "ASK_USER", [], "WAITING_USER"),
             ("可以删掉非必去的，博物馆和黄鹤楼要保留。", "GET_DISTANCE_MATRIX", ["get_distance_matrix", "build_schedule_draft"], "PROPOSED"),
             "do_not_silently_drop_required_places", "respect_meal_break"),
        case("v4-fixed-event-17", "schedule", "SCHEDULE",
             {"destination": "天津", "trip_dates": ["2027-01-23", "2027-01-23"],
              "saved_places": 2, "saved_place_details": places("天津博物馆", "五大道"),
              "fixed_stops": [{"name": "相声演出", "date": "2027-01-23", "start_time": "19:30"}]},
             ("把两个收藏点排在演出前，七点半的票不能迟到。", "GET_DISTANCE_MATRIX", ["get_distance_matrix", "build_schedule_draft"], "PROPOSED"),
             ("博物馆周一闭馆，行程那天是周六。", "BUILD_SCHEDULE_DRAFT", ["build_schedule_draft"], "PROPOSED"),
             "preserve_fixed_event", "respect_opening_hours"),
        case("v4-meal-add-to-itinerary-18", "meal", "MEAL",
             {"destination": "宁波", "trip_dates": ["2027-02-02", "2027-02-03"],
              "saved_places": [], "planning_category": "MEAL"},
             ("天一广场附近推荐宁波菜，分别找一天午餐和一天晚餐。", "SEARCH_PLACES", ["search_places"], "COMPLETED"),
             ("两顿不要重复店，晚餐预算人均一百以内。", "SEARCH_PLACES", ["search_places"], "COMPLETED"),
             "keep_meals_separate_by_day_and_type", "respect_meal_budget"),
        case("v4-route-provider-failure-19", "transport", "TRANSPORT",
             {"destination": "成都", "trip_dates": ["2027-02-08", "2027-02-08"],
              "saved_places": ["成都东站", "宽窄巷子"], "endpoints": "clear", "origin": "成都东站", "route_provider": "timeout"},
             ("从成都东站到宽窄巷子查驾车路线。", "GET_TRIP_ROUTE", ["get_trip_route"], "PARTIAL"),
             ("服务恢复了吗？可以再试一次，但别编时间。", "GET_TRIP_ROUTE", ["get_trip_route"], "COMPLETED"),
             "disclose_route_timeout", "do_not_fabricate_duration"),
        case("v4-weather-provider-failure-20", "weather", "WEATHER",
             {"destination": "三亚", "trip_dates": ["2027-02-12", "2027-02-14"],
              "weather_provider": "timeout"},
             ("查一下三亚这几天的天气，尤其关注大风。", "GET_TRIP_WEATHER", ["get_trip_weather"], "PARTIAL"),
             ("查不到的话告诉我限制，并给一般性的海边防风建议。", "FINISH", [], "COMPLETED"),
             "disclose_weather_timeout", "label_general_advice_not_forecast"),
    ]
    reference_hashes = {}
    for reference_id in REFERENCE_IDS:
        ref = json.loads((CASES / f"{reference_id}.json").read_text(encoding="utf-8"))
        canonical = json.dumps(ref, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        reference_hashes[reference_id] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    dataset = {
        "dataset_id": "advisor_multiturn_holdout_v4",
        "version": 4,
        "status": "FROZEN_NOT_SCORED",
        "provider_mode": "synthetic_contract",
        "frozen_before_scoring": True,
        "holdout_protocol": {
            "reference_datasets": REFERENCE_IDS,
            "reference_dataset_sha256": reference_hashes,
            "claim_boundary": "Freshly authored diagnostic pilot with exact ID and normalized-prompt separation from declared prior suites; semantic independence is not established.",
            "default_case_ids": [item["id"] for item in cases],
        },
        "scope": "20 freshly authored two-turn diagnostic tasks; local synthetic read-only observations only; no TravelPilot records are read or written.",
        "capability_contract": {
            "registered_advisor_tools": ["build_schedule_draft", "get_distance_matrix", "get_trip_route", "get_trip_weather", "search_places"],
            "confirmation": "No model-callable action can confirm or commit a plan.",
            "tool_arguments": "Production tool schemas and synthetic task evidence are validated.",
        },
        "cases": cases,
    }
    OUTPUT.write_text(json.dumps(dataset, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(OUTPUT.relative_to(ROOT).as_posix())


if __name__ == "__main__":
    main()
