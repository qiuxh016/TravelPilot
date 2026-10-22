"""Freeze the next evidence-complete Advisor action pilot before scoring."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CASES = ROOT / "tests/evaluation/cases"
OUTPUT = CASES / "advisor_multiturn_holdout_v5.json"
REFERENCE_IDS = [
    "advisor_multiturn_v1", "advisor_multiturn_v2", "advisor_multiturn_v3",
    "advisor_multiturn_v4", "advisor_multiturn_v5", "advisor_open_consultation_v2",
    "advisor_existing_trip_agent_v3", "advisor_multiturn_holdout_v1",
    "advisor_multiturn_holdout_v2", "advisor_multiturn_holdout_v3",
    "advisor_multiturn_holdout_v4",
]


def _places(*names: str) -> list[dict]:
    return [
        {
            "id": f"fixture-place-{index + 1}",
            "name": name,
            "latitude": 30.60 + index * 0.01,
            "longitude": 104.00 + index * 0.01,
        }
        for index, name in enumerate(names)
    ]


def _turn(user: str, action: str, tools: list[str], status: str = "COMPLETED") -> dict:
    return {
        "user": user,
        "expected_action": action,
        "allowed_tools": tools,
        "expected_status": status,
        "action_owner": "AGENT_DECISION",
    }


def _case(
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
        "turns": [_turn(*first), _turn(*second)],
        "invariants": [*invariants, "no_formal_write"],
    }


def _build_cases() -> list[dict]:
    return [
        _case(
            "v5-schedule-day-clustering-01", "schedule", "SCHEDULE",
            {"destination": "苏州", "trip_dates": ["2027-03-06", "2027-03-07"],
             "saved_places": 3, "saved_place_details": _places("拙政园", "苏州博物馆", "平江路"),
             "fixed_stops": []},
            ("这三个收藏点分两天怎么排能少走回头路？", "GET_DISTANCE_MATRIX",
             ["get_distance_matrix", "build_schedule_draft"], "PROPOSED"),
            ("把博物馆和园林放同一天，平江路留到傍晚，其他照旧。",
             "BUILD_SCHEDULE_DRAFT", ["build_schedule_draft"], "PROPOSED"),
            "preserve_latest_day_assignment", "preserve_all_selected_places"),
        _case(
            "v5-schedule-correction-02", "schedule", "SCHEDULE",
            {"destination": "西安", "trip_dates": ["2027-03-12", "2027-03-13"],
             "saved_places": 2, "saved_place_details": _places("陕西历史博物馆", "大雁塔"),
             "fixed_stops": []},
            ("先给我排这两个地方，节奏别太紧。", "GET_DISTANCE_MATRIX",
             ["get_distance_matrix", "build_schedule_draft"], "PROPOSED"),
            ("改一下，第一天可以紧凑，第二天要轻松；景点都保留。",
             "BUILD_SCHEDULE_DRAFT", ["build_schedule_draft"], "PROPOSED"),
            "apply_latest_correction", "preserve_both_places"),
        _case(
            "v5-schedule-ambiguous-stop-03", "schedule", "SCHEDULE",
            {"destination": "长沙", "trip_dates": ["2027-03-18", "2027-03-18"],
             "saved_places": 3, "saved_place_details": _places("岳麓书院", "橘子洲", "湖南省博物馆"),
             "fixed_stops": []},
            ("把这几个点排成一天，下午别太累。", "GET_DISTANCE_MATRIX",
             ["get_distance_matrix", "build_schedule_draft"], "PROPOSED"),
            ("把最远的那个换成室内的，其他别动。", "ASK_USER", [], "WAITING_USER"),
            "clarify_ambiguous_reference", "preserve_unrelated_stops"),
        _case(
            "v5-meal-allergy-04", "meal", "MEAL",
            {"destination": "潮州", "trip_dates": ["2027-03-20", "2027-03-20"],
             "saved_places": [], "planning_category": "MEAL", "dietary_preference": "清淡",
             "allergy_constraint": "同行者对虾过敏"},
            ("牌坊街附近帮我找午餐，想吃本地菜，口味清淡。", "SEARCH_PLACES",
             ["search_places"], "COMPLETED"),
            ("同行人对虾过敏，优先能确认过敏原的店。", "SEARCH_PLACES",
             ["search_places"], "COMPLETED"),
            "food_only_candidates", "preserve_allergy_constraint"),
        _case(
            "v5-meal-scope-correction-05", "meal", "MEAL",
            {"destination": "贵阳", "trip_dates": ["2027-03-21", "2027-03-21"],
             "saved_places": [], "planning_category": "MEAL", "meal_type": "DINNER"},
            ("青云市集附近找晚餐，想吃酸汤鱼。", "SEARCH_PLACES", ["search_places"], "COMPLETED"),
            ("只要餐馆候选，不用顺带排景点。营业时间请以核验结果为准。",
             "SEARCH_PLACES", ["search_places"], "COMPLETED"),
            "exclude_attractions", "do_not_invent_opening_hours"),
        _case(
            "v5-lodging-budget-06", "lodging", "LODGING",
            {"destination": "青岛", "trip_dates": ["2027-03-25", "2027-03-27"],
             "saved_places": [], "planning_category": "LODGING", "travel_party": 3,
             "budget_per_night": 500},
            ("找靠近五四广场或地铁站的住宿，三个人住两晚，每晚不超过五百。",
             "SEARCH_PLACES", ["search_places"], "COMPLETED"),
            ("优先步行到站十分钟内；没核实的价格不要当成确定信息。",
             "SEARCH_PLACES", ["search_places"], "COMPLETED"),
            "respect_budget_and_party_size", "no_booking_claim"),
        _case(
            "v5-lodging-booking-boundary-07", "lodging", "LODGING",
            {"destination": "泉州", "trip_dates": ["2027-03-28", "2027-03-30"],
             "saved_places": [], "planning_category": "LODGING"},
            ("帮我直接订一家西街附近的民宿。", "FINISH", [], "COMPLETED"),
            ("那先列出可选住宿，我自己决定，不要下单。", "SEARCH_PLACES",
             ["search_places"], "COMPLETED"),
            "no_external_booking", "offer_supported_search"),
        _case(
            "v5-route-explicit-endpoints-08", "transport", "TRANSPORT",
            {"destination": "杭州", "trip_dates": ["2027-04-02", "2027-04-02"],
             "known_places": _places("杭州东站", "灵隐寺"),
             "selected_origin_id": "fixture-place-1", "selected_destination_id": "fixture-place-2",
             "origin": "杭州东站", "endpoints": "clear"},
            ("从杭州东站到灵隐寺查公共交通路线。", "GET_TRIP_ROUTE",
             ["get_trip_route"], "COMPLETED"),
            ("如果带老人出行，有没有少走路的路线？只说有证据的部分。",
             "GET_TRIP_ROUTE", ["get_trip_route"], "COMPLETED"),
            "use_requested_mode", "do_not_invent_accessibility_evidence"),
        _case(
            "v5-route-origin-clarification-09", "transport", "TRANSPORT",
            {"destination": "北京", "trip_dates": ["2027-04-03", "2027-04-03"],
             "known_places": _places("北京站", "首都机场", "故宫"), "endpoints": "ambiguous"},
            ("帮我查去故宫的路线。", "ASK_USER", [], "WAITING_USER"),
            ("从首都机场出发，优先地铁。", "GET_TRIP_ROUTE", ["get_trip_route"], "COMPLETED"),
            "ask_for_missing_origin", "use_user_supplied_endpoint"),
        _case(
            "v5-weather-indoor-followup-10", "weather", "WEATHER",
            {"destination": "福州", "trip_dates": ["2027-04-05", "2027-04-06"],
             "weather_provider": "available"},
            ("查这两天的天气，重点看降雨。", "GET_TRIP_WEATHER", ["get_trip_weather"], "COMPLETED"),
            ("如果第二天下雨，帮我找室内替代地点。", "SEARCH_PLACES",
             ["search_places"], "COMPLETED"),
            "ground_forecast_claims", "indoor_candidates_only"),
        _case(
            "v5-weather-dates-missing-11", "weather", "WEATHER",
            {"destination": "长春", "trip_dates": None, "weather_provider": "available"},
            ("我想下个月去长春，先看看天气和穿衣建议。", "ASK_USER", [], "WAITING_USER"),
            ("4月11到13号。", "GET_TRIP_WEATHER", ["get_trip_weather"], "COMPLETED"),
            "ask_for_exact_dates", "do_not_assume_forecast"),
        _case(
            "v5-preparation-family-12", "preparation", "PREPARATION",
            {"destination": "北海", "trip_dates": ["2027-04-08", "2027-04-09"],
             "weather_provider": "available", "travel_party": "带幼儿"},
            ("带孩子去北海两天，结合天气提醒我带什么。", "GET_TRIP_WEATHER",
             ["get_trip_weather"], "COMPLETED"),
            ("再考虑防晒、备用衣物和孩子午休。", "FINISH", [], "COMPLETED"),
            "age_appropriate_preparation", "separate_forecast_from_general_advice"),
        _case(
            "v5-discovery-origin-13", "discovery", "DISCOVERY",
            {"destination": None, "trip_dates": None, "saved_places": [],
             "origin": "南京", "max_train_hours": 2.5},
            ("周末想看山水，从南京出发高铁两三个小时内有什么目的地？",
             "ASK_USER", [], "WAITING_USER"),
            ("更想看湖和山，不考虑主题乐园。", "SEARCH_PLACES", ["search_places"], "COMPLETED"),
            "preserve_departure_limit", "exclude_theme_parks"),
        _case(
            "v5-discovery-mobility-14", "discovery", "DISCOVERY",
            {"destination": "桂林", "trip_dates": ["2027-04-15", "2027-04-16"],
             "saved_places": [], "mobility_preference": "少台阶、少爬坡"},
            ("桂林两天推荐一些自然景点。", "SEARCH_PLACES", ["search_places"], "COMPLETED"),
            ("同行人膝盖不舒服，尽量选平缓好走的地方。", "SEARCH_PLACES",
             ["search_places"], "COMPLETED"),
            "respect_mobility_need", "avoid_overpacked_recommendations"),
        _case(
            "v5-open-area-advice-15", "general", "GENERAL",
            {"destination": "景德镇", "trip_dates": ["2027-04-17", "2027-04-18"],
             "saved_places": [], "transport_preference": "公交和打车"},
            ("第一次去景德镇住哪一带比较方便？简单解释差别。", "FINISH", [], "COMPLETED"),
            ("我不租车，晚上也想能方便吃饭。", "FINISH", [], "COMPLETED"),
            "answer_open_question", "adapt_to_public_transport"),
        _case(
            "v5-schedule-capacity-16", "schedule", "SCHEDULE",
            {"destination": "武汉", "trip_dates": ["2027-04-20", "2027-04-20"],
             "saved_places": 6,
             "saved_place_details": _places("黄鹤楼", "东湖", "古德寺", "昙华林", "归元寺", "湖北省博物馆"),
             "fixed_stops": []},
            ("想一天走完这六个地方，还要留够午饭时间，先看看是否排得下。",
             "ASK_USER", [], "WAITING_USER"),
            ("可以删一个非必去的，但黄鹤楼和省博物馆一定保留。",
             "GET_DISTANCE_MATRIX", ["get_distance_matrix", "build_schedule_draft"], "PROPOSED"),
            "do_not_drop_required_places", "respect_meal_break"),
        _case(
            "v5-fixed-event-17", "schedule", "SCHEDULE",
            {"destination": "天津", "trip_dates": ["2027-04-24", "2027-04-24"],
             "saved_places": 2, "saved_place_details": _places("天津博物馆", "五大道"),
             "fixed_stops": [{"name": "音乐会", "date": "2027-04-24", "start_time": "19:00"}]},
            ("把两个收藏点安排在晚上七点音乐会之前，票不能迟到。",
             "GET_DISTANCE_MATRIX", ["get_distance_matrix", "build_schedule_draft"], "PROPOSED"),
            ("那天博物馆延长开放到下午五点半，按这个信息重排。",
             "BUILD_SCHEDULE_DRAFT", ["build_schedule_draft"], "PROPOSED"),
            "preserve_fixed_event", "use_latest_opening_information"),
        _case(
            "v5-meals-distinct-days-18", "meal", "MEAL",
            {"destination": "宁波", "trip_dates": ["2027-04-27", "2027-04-28"],
             "saved_places": [], "planning_category": "MEAL"},
            ("天一广场附近分别找一天午餐和一天晚餐。", "SEARCH_PLACES",
             ["search_places"], "COMPLETED"),
            ("两顿别安排同一家，晚饭人均预算一百以内。", "SEARCH_PLACES",
             ["search_places"], "COMPLETED"),
            "keep_meals_distinct", "preserve_dinner_budget"),
        _case(
            "v5-route-provider-retry-19", "transport", "TRANSPORT",
            {"destination": "成都", "trip_dates": ["2027-05-02", "2027-05-02"],
             "known_places": _places("成都东站", "宽窄巷子"),
             "selected_origin_id": "fixture-place-1", "selected_destination_id": "fixture-place-2",
             "origin": "成都东站", "endpoints": "clear", "route_provider": "timeout"},
            ("查一下成都东站到宽窄巷子的驾车路线。", "GET_TRIP_ROUTE",
             ["get_trip_route"], "PARTIAL"),
            ("刚才超时了，可以再试一回，但不要估一个时间冒充结果。",
             "GET_TRIP_ROUTE", ["get_trip_route"], "COMPLETED"),
            "disclose_route_failure", "do_not_fabricate_duration"),
        _case(
            "v5-weather-provider-recovery-20", "weather", "WEATHER",
            {"destination": "三亚", "trip_dates": ["2027-05-06", "2027-05-08"],
             "weather_provider": "timeout"},
            ("查三亚这几天的大风和降雨情况。", "GET_TRIP_WEATHER",
             ["get_trip_weather"], "PARTIAL"),
            ("如果服务还是不通，请说明限制，再给一般的海边防风常识。",
             "FINISH", [], "COMPLETED"),
            "disclose_weather_failure", "label_general_advice_not_forecast"),
    ]


def main() -> None:
    cases = _build_cases()
    reference_hashes = {}
    for dataset_id in REFERENCE_IDS:
        reference = json.loads((CASES / f"{dataset_id}.json").read_text(encoding="utf-8"))
        canonical = json.dumps(reference, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        reference_hashes[dataset_id] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    dataset = {
        "dataset_id": "advisor_multiturn_holdout_v5",
        "version": 5,
        "status": "FROZEN_NOT_LIVE_SCORED",
        "provider_mode": "synthetic_contract",
        "frozen_before_scoring": True,
        "holdout_protocol": {
            "reference_datasets": REFERENCE_IDS,
            "reference_dataset_sha256": reference_hashes,
            "claim_boundary": (
                "Freshly authored diagnostic tasks with exact ID and normalized-prompt separation; "
                "semantic independence is not established."
            ),
            "default_case_ids": [item["id"] for item in cases],
        },
        "scope": "20 freshly authored two-turn tasks; local synthetic read-only observations only; no trip data is read or written.",
        "pre_live_diagnostics": (
            "Scripted policy replay is permitted as a local baseline diagnostic before live scoring; "
            "it is in-sample and is not a model-quality result."
        ),
        "capability_contract": {
            "registered_advisor_tools": [
                "build_schedule_draft", "get_distance_matrix", "get_trip_route",
                "get_trip_weather", "search_places",
            ],
            "confirmation": "No model-callable action confirms or commits a plan.",
            "tool_arguments": "Production tool schemas and grounded synthetic route/schedule evidence are validated.",
        },
        "cases": cases,
    }
    OUTPUT.write_text(json.dumps(dataset, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(OUTPUT.relative_to(ROOT).as_posix())


if __name__ == "__main__":
    main()
