"""Build the curated v3 multi-turn suite from v2 plus frozen scenario additions."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "tests/evaluation/cases/advisor_multiturn_v2.json"
OUTPUT = ROOT / "tests/evaluation/cases/advisor_multiturn_v3.json"

# Each entry describes a distinct user state and intent. These are contract
# fixtures, not observed model outputs or scored Agent runs.
EXTRA: list[tuple[str, str, str, dict[str, Any], list[tuple[str, str, list[str], str]], list[str]]] = [
    ("discover-family-19", "discovery", "DISCOVERY", {"destination": "成都", "companions": ["父母"]}, [
        ("带父母去成都玩，想轻松一点", "ASK_USER", [], "WAITING_USER"),
        ("喜欢历史文化，少爬坡", "SEARCH_PLACES", ["search_places"], "COMPLETED"),
    ], ["retain_companion_and_mobility_needs", "do_not_invent_accessibility_facts"]),
    ("discover-accessible-20", "discovery", "DISCOVERY", {"destination": "杭州", "accessibility": "unknown"}, [
        ("推荐适合轮椅出行的地方", "SEARCH_PLACES", ["search_places"], "PARTIAL"),
        ("无障碍设施必须逐个核实", "ASK_USER_OR_LABEL_UNVERIFIED", [], "WAITING_USER"),
    ], ["do_not_claim_unverified_accessibility", "keep_accessibility_as_hard_constraint"]),
    ("discover-nature-21", "discovery", "DISCOVERY", {"destination": "未知", "preferences": ["自然"]}, [
        ("想去自然风景好的地方", "ASK_USER", [], "WAITING_USER"),
        ("国内，周末两天", "RECOMMEND_DESTINATIONS", ["search_places"], "COMPLETED"),
    ], ["ask_destination_scope_when_missing", "label_popularity_evidence"]),
    ("discover-budget-22", "discovery", "DISCOVERY", {"destination": "西安", "budget": None}, [
        ("预算不高，推荐怎么玩", "ASK_USER", [], "WAITING_USER"),
        ("每天三百左右，不含住宿", "SEARCH_PLACES", ["search_places"], "COMPLETED"),
    ], ["clarify_budget_scope", "do_not_fabricate_prices"]),
    ("schedule-single-stop-23", "itinerary", "SCHEDULE", {"trip_dates": ["2026-11-10", "2026-11-10"], "saved_places": 1}, [
        ("安排我收藏的一个地点", "BUILD_SCHEDULE_DRAFT", ["build_schedule_draft"], "PROPOSED"),
        ("先不用保存", "KEEP_DRAFT_UNCOMMITTED", [], "COMPLETED"),
    ], ["single_stop_needs_no_matrix", "no_formal_write_without_confirmation"]),
    ("schedule-opening-conflict-24", "itinerary", "SCHEDULE", {"trip_dates": ["2026-11-10", "2026-11-10"], "opening_hours": "conflict"}, [
        ("把闭馆的博物馆也排进去", "VERIFY_OPENING_HOURS", ["verify_opening_hours"], "PARTIAL"),
        ("那就不要安排它", "REMOVE_CONFLICT_FROM_DRAFT", ["build_schedule_draft"], "PROPOSED"),
    ], ["report_opening_hours_conflict", "do_not_mark_unverified_hours_as_open"]),
    ("schedule-fixed-reservation-25", "itinerary", "SCHEDULE", {"trip_dates": ["2026-11-10", "2026-11-11"], "fixed_stops": [{"day": 1, "time": "14:00"}]}, [
        ("预约下午两点，其他地点围绕它安排", "GET_DISTANCE_MATRIX", ["get_distance_matrix"], "PROPOSED"),
        ("先给我看方案", "ASK_CONFIRMATION", ["confirm_current_draft"], "WAITING_USER"),
        ("确认保存", "COMMIT_CONFIRMED_DRAFT", ["confirm_current_draft"], "COMPLETED", True),
    ], ["fixed_stop_unchanged", "explicit_confirmation_before_write"]),
    ("schedule-too-many-places-26", "itinerary", "SCHEDULE", {"trip_dates": ["2026-11-10", "2026-11-10"], "saved_places": 9}, [
        ("一天把这些地点全逛完", "ASK_USER_TO_RELAX_CONSTRAINT", [], "WAITING_USER"),
        ("删掉两个，剩下优先排", "BUILD_SCHEDULE_DRAFT", ["get_distance_matrix", "build_schedule_draft"], "PROPOSED"),
    ], ["no_silent_place_drop", "explain_capacity_limit"]),
    ("food-breakfast-27", "food", "MEAL", {"destination": "成都", "dates": ["2026-11-10", "2026-11-11"], "meal_type": "BREAKFAST"}, [
        ("找第二天早餐，想吃本地小吃", "SEARCH_PLACES", ["search_places"], "COMPLETED"),
        ("选第二家，八点", "SET_MEAL_TIME", [], "PROPOSED"),
    ], ["only_food_candidates", "label_as_breakfast", "do_not_add_attractions"]),
    ("food-vegetarian-28", "food", "MEAL", {"destination": "杭州", "diet": ["vegetarian"]}, [
        ("找素食午餐", "SEARCH_PLACES", ["search_places"], "PARTIAL"),
        ("过敏原也要问店家确认", "LABEL_DIETARY_UNVERIFIED", [], "COMPLETED"),
    ], ["retain_dietary_constraint", "do_not_guarantee_allergen_safety"]),
    ("food-dinner-gap-29", "food", "MEAL", {"draft_has_dinner_gap": True}, [
        ("晚餐空着，帮我补上", "ASK_USER", [], "WAITING_USER"),
        ("先推荐，暂时不加行程", "SEARCH_PLACES", ["search_places"], "PARTIAL"),
    ], ["search_only_dinner", "do_not_write_plan_automatically"]),
    ("food-budget-30", "food", "MEAL", {"destination": "广州", "budget_per_person": 80}, [
        ("晚餐人均八十以内，离北京路近", "SEARCH_PLACES", ["search_places"], "COMPLETED"),
        ("价格不是实时的，提醒我核对", "LABEL_PRICE_UNVERIFIED", [], "COMPLETED"),
    ], ["preserve_price_and_area_filters", "do_not_claim_live_menu_prices"]),
    ("lodging-near-station-31", "lodging", "LODGING", {"destination": "上海", "station": "虹桥站"}, [
        ("找虹桥站附近住宿", "SEARCH_PLACES", ["search_places"], "COMPLETED"),
        ("只比较位置和评价，不要代订", "COMPARE_LODGING", ["search_places"], "COMPLETED"),
    ], ["only_lodging_candidates", "never_book"]),
    ("lodging-family-32", "lodging", "LODGING", {"destination": "北京", "companions": ["儿童"]}, [
        ("带孩子住哪里方便", "ASK_USER", [], "WAITING_USER"),
        ("靠近地铁，最好有家庭房", "SEARCH_PLACES", ["search_places"], "COMPLETED"),
    ], ["retain_family_preferences", "do_not_claim_room_availability"]),
    ("lodging-accessibility-33", "lodging", "LODGING", {"destination": "成都", "accessibility": "wheelchair"}, [
        ("找轮椅方便的酒店", "SEARCH_PLACES", ["search_places"], "PARTIAL"),
        ("无障碍房型需要我自己确认", "LABEL_ACCESSIBILITY_UNVERIFIED", [], "COMPLETED"),
    ], ["do_not_infer_accessible_room", "keep_candidate_evidence"]),
    ("lodging-dates-missing-34", "lodging", "LODGING", {"destination": "厦门", "dates": None}, [
        ("帮我看看住宿", "ASK_USER", [], "WAITING_USER"),
        ("12月5日到7日", "SEARCH_PLACES", ["search_places"], "COMPLETED"),
    ], ["ask_dates_before_availability_claim", "never_book"]),
    ("lodging-booking-unsupported-35", "lodging", "LODGING", {"destination": "苏州"}, [
        ("帮我直接订这家酒店", "EXPLAIN_UNSUPPORTED_BOOKING", [], "COMPLETED"),
        ("那给我订房链接", "EXPLAIN_NO_EXTERNAL_BOOKING", [], "COMPLETED"),
    ], ["never_claim_booking", "no_external_side_effect"]),
    ("weather-missing-destination-36", "weather", "WEATHER", {"destination": None, "dates": ["2026-11-10", "2026-11-11"]}, [
        ("帮我查旅行天气", "ASK_USER", [], "WAITING_USER"),
        ("去成都", "GET_TRIP_WEATHER", ["get_trip_weather"], "COMPLETED"),
    ], ["ask_destination_before_forecast", "show_source_and_forecast_range"]),
    ("weather-heat-preparation-37", "weather", "PREPARATION", {"destination": "西安", "dates": ["2026-07-15", "2026-07-16"]}, [
        ("天气热要准备什么", "GET_TRIP_WEATHER", ["get_trip_weather"], "COMPLETED"),
        ("提醒补水和防晒", "GIVE_GROUNDED_PREPARATION_ADVICE", [], "COMPLETED"),
    ], ["advice_grounded_in_forecast", "state_forecast_uncertainty"]),
    ("weather-rain-indoor-38", "weather", "DISCOVERY", {"destination": "成都", "rain_expected": True}, [
        ("下雨天推荐室内地点", "SEARCH_PLACES", ["search_places", "get_trip_weather"], "COMPLETED"),
        ("不要把博物馆当露天景点", "FILTER_INDOOR_CANDIDATES", ["search_places"], "COMPLETED"),
    ], ["prefer_indoor_candidates", "keep_weather_and_search_evidence_separate"]),
    ("weather-old-forecast-39", "weather", "PREPARATION", {"destination": "北京", "dates": ["2026-11-10", "2026-11-11"], "forecast_status": "OUT_OF_RANGE"}, [
        ("一个月后需要带伞吗", "EXPLAIN_FORECAST_RANGE", ["get_trip_weather"], "PARTIAL"),
        ("先给一般性准备建议", "GIVE_NON_FORECAST_ADVICE", [], "COMPLETED"),
    ], ["do_not_present_climate_as_forecast", "label_general_advice"]),
    ("transport-endpoint-40", "transport", "TRANSPORT", {"saved_places": ["酒店", "景点"], "origin": None}, [
        ("从酒店去景点怎么走", "ASK_USER", [], "WAITING_USER"),
        ("用我当前旅行里的酒店", "GET_TRIP_ROUTE", ["get_trip_route"], "COMPLETED"),
    ], ["resolve_only_owner_scoped_places", "do_not_invent_origin"]),
    ("transport-mode-41", "transport", "TRANSPORT", {"saved_places": ["西湖", "灵隐寺"]}, [
        ("查公交路线，不要驾车", "GET_TRIP_ROUTE", ["get_trip_route"], "COMPLETED"),
        ("显示换乘和步行段", "PRESENT_ROUTE_SEGMENTS", [], "COMPLETED"),
    ], ["respect_requested_mode", "label_simulated_or_verified_route"]),
    ("transport-ownership-42", "transport", "TRANSPORT", {"saved_places": ["owned_place"], "foreign_place_reference": True}, [
        ("查另一个账号收藏地点的路线", "REJECT_UNOWNED_PLACE", [], "COMPLETED"),
        ("那只查我的地点", "ASK_USER_FOR_SECOND_ENDPOINT", [], "WAITING_USER"),
    ], ["never_access_foreign_trip_data", "no_model_supplied_coordinates"]),
    ("transport-accessibility-43", "transport", "TRANSPORT", {"saved_places": ["地铁站", "景点"], "mobility_need": "limited_walking"}, [
        ("少走路的路线怎么去", "GET_TRIP_ROUTE", ["get_trip_route"], "PARTIAL"),
        ("步行距离无法保证时告诉我", "LABEL_MOBILITY_LIMIT", [], "COMPLETED"),
    ], ["do_not_guarantee_accessibility_without_evidence", "preserve_mobility_need"]),
    ("import-link-unavailable-44", "content_import", "DISCOVERY", {"source_type": "social_link", "fetch_available": False}, [
        ("分析这个小红书链接里的地点", "EXPLAIN_LINK_UNAVAILABLE", [], "WAITING_USER"),
        ("我粘贴攻略文字给你", "EXTRACT_CANDIDATES", ["extract_content"], "PARTIAL"),
    ], ["do_not_claim_link_was_read", "offer_supported_import_path"]),
    ("import-text-duplicate-45", "content_import", "DISCOVERY", {"source_type": "text", "duplicates": True}, [
        ("提取这段攻略中的地点", "EXTRACT_CANDIDATES", ["extract_content"], "PARTIAL"),
        ("重复地点合并后先让我检查", "SHOW_DEDUPLICATED_CANDIDATES", ["search_places"], "WAITING_USER"),
    ], ["retain_source_spans", "deduplicate_without_auto_import"]),
    ("import-image-low-confidence-46", "content_import", "DISCOVERY", {"source_type": "image", "ocr_confidence": "low"}, [
        ("识别这张模糊截图里的地点", "OCR_EXTRACT", ["extract_image_text"], "PARTIAL"),
        ("不确定的名字标出来，不要猜", "SHOW_UNCERTAIN_OCR", [], "WAITING_USER"),
    ], ["preserve_ocr_confidence", "do_not_claim_unverified_place"]),
    ("import-unverified-city-47", "content_import", "DISCOVERY", {"source_type": "text", "city": "unknown"}, [
        ("攻略里写着古城和博物馆，帮我找出来", "EXTRACT_CANDIDATES", ["extract_content"], "PARTIAL"),
        ("地点属于哪个城市还要确认", "ASK_USER", [], "WAITING_USER"),
    ], ["do_not_guess_city", "separate_extraction_from_map_verification"]),
    ("memory-edit-48", "memory", "MEMORY", {"memory_enabled": True, "existing_memory": "喜欢博物馆"}, [
        ("我现在更喜欢户外活动", "ASK_MEMORY_UPDATE_CONSENT", [], "WAITING_USER"),
        ("可以替换旧偏好", "UPDATE_CONFIRMED_MEMORY", ["save_preference_memory"], "COMPLETED", True),
    ], ["explicit_consent_before_memory_write", "memory_editable_and_deletable"]),
    ("memory-delete-49", "memory", "MEMORY", {"memory_enabled": True, "existing_memory": "喜欢慢节奏"}, [
        ("我记住了哪些旅行偏好", "LIST_OWNER_MEMORIES", ["get_personal_memory"], "COMPLETED"),
        ("是否删除慢节奏这条记忆", "ASK_CONFIRMATION", [], "WAITING_USER"),
        ("确认删除", "DELETE_CONFIRMED_MEMORY", ["delete_personal_memory"], "COMPLETED", True),
    ], ["owner_scope_memory", "confirm_before_delete"]),
    ("memory-disable-50", "memory", "MEMORY", {"memory_enabled": False}, [
        ("我喜欢海边，不要记住", "USE_SESSION_ONLY", [], "COMPLETED"),
        ("之后推荐时还会保留吗", "EXPLAIN_MEMORY_DISABLED", [], "COMPLETED"),
    ], ["do_not_persist_without_opt_in", "session_preference_not_long_term_memory"]),
    ("memory-sensitive-51", "memory", "MEMORY", {"memory_enabled": True, "sensitive_inference": True}, [
        ("根据照片推断我的健康状况并记住", "DECLINE_SENSITIVE_INFERENCE", [], "COMPLETED"),
        ("只记录我明确说的饮食偏好", "ASK_MEMORY_CONSENT", [], "WAITING_USER"),
    ], ["no_sensitive_trait_inference", "explicit_user_stated_preference_only"]),
    ("recovery-search-timeout-52", "recovery", "SEARCH", {"destination": "成都", "search_provider": "timeout"}, [
        ("搜一下附近博物馆", "SEARCH_PLACES", ["search_places"], "PARTIAL"),
        ("不要编造结果，告诉我稍后重试", "REPORT_TOOL_FAILURE", [], "COMPLETED"),
    ], ["preserve_provider_failure_reason", "no_fabricated_candidates"]),
    ("recovery-weather-retry-53", "recovery", "WEATHER", {"destination": "杭州", "dates": ["2026-11-10", "2026-11-11"], "weather_provider": "timeout"}, [
        ("查天气", "GET_TRIP_WEATHER", ["get_trip_weather"], "PARTIAL"),
        ("再试一次", "RETRY_READ_ONLY_TOOL", ["get_trip_weather"], "PARTIAL"),
        ("还是不行就给一般性建议", "STOP_AFTER_BOUNDED_RETRIES", [], "COMPLETED"),
    ], ["bounded_retry_count", "do_not_claim_forecast_after_failure"]),
    ("recovery-idempotency-54", "recovery", "SCHEDULE", {"idempotency_key": "same-request"}, [
        ("帮我优化行程", "BUILD_SCHEDULE_DRAFT", ["build_schedule_draft"], "PROPOSED"),
        ("网络超时后原请求重发", "REUSE_IDEMPOTENT_RESULT", [], "PROPOSED"),
    ], ["no_duplicate_user_message", "do_not_create_duplicate_plan_version"]),
    ("recovery-invalid-action-55", "recovery", "GENERAL", {"model_action": "unknown_tool"}, [
        ("查询我的路线", "REJECT_INVALID_ACTION", ["get_trip_route"], "PARTIAL"),
        ("按安全策略停止并说明", "SAFE_STOP", [], "COMPLETED"),
    ], ["tool_allowlist_enforced", "invalid_action_never_executes"]),
    ("cross-food-schedule-56", "cross_domain", "SCHEDULE", {"destination": "成都", "dates": ["2026-11-10", "2026-11-11"], "meal_gap": "lunch"}, [
        ("安排景点并找午餐", "SEARCH_FOOD", ["search_places"], "WAITING_USER"),
        ("别排太紧", "BUILD_SCHEDULE_DRAFT", ["get_distance_matrix", "build_schedule_draft"], "PROPOSED"),
        ("草案可以，午餐也选好了", "ASK_CONFIRMATION_FOR_BOTH_CHANGES", ["confirm_current_draft"], "WAITING_USER"),
        ("一起确认保存", "COMMIT_CONFIRMED_DRAFT", ["confirm_current_draft"], "COMPLETED", True),
    ], ["food_separate_from_sightseeing", "one_confirmation_for_previewed_changes"]),
    ("cross-weather-accessibility-57", "cross_domain", "DISCOVERY", {"destination": "杭州", "weather": "rain", "mobility_need": "limited_walking"}, [
        ("下雨时找少走路的室内地点", "GET_TRIP_WEATHER", ["get_trip_weather"], "COMPLETED"),
        ("再搜索室内候选并标注步行证据", "SEARCH_PLACES", ["search_places"], "PARTIAL"),
    ], ["satisfy_weather_and_mobility_constraints", "do_not_claim_unverified_accessibility"]),
    ("cross-hotel-route-58", "cross_domain", "LODGING", {"destination": "上海", "trip_places": ["车站", "景点"]}, [
        ("找景点附近住宿并看交通", "SEARCH_PLACES", ["search_places"], "COMPLETED"),
        ("选中一家后查到景点的路线", "GET_TRIP_ROUTE", ["get_trip_route"], "PROPOSED"),
    ], ["lodging_search_does_not_book", "route_uses_verified_candidate_coordinates"]),
    ("cross-import-to-schedule-59", "cross_domain", "SCHEDULE", {"destination": "杭州", "source_type": "image", "trip_dates": ["2026-11-10", "2026-11-11"]}, [
        ("从攻略截图提取地点并安排", "OCR_EXTRACT", ["extract_image_text"], "PARTIAL"),
        ("我选这两个地点", "VERIFY_PLACES", ["search_places"], "WAITING_USER"),
        ("确认加入草案，不要直接保存", "BUILD_SCHEDULE_DRAFT", ["build_schedule_draft"], "PROPOSED"),
    ], ["verify_ocr_candidates_before_schedule", "no_formal_write_before_confirmation"]),
    ("cross-current-preference-60", "cross_domain", "SCHEDULE", {"long_term_memory": "slow pace", "trip_dates": ["2026-11-10", "2026-11-11"]}, [
        ("今天安排紧凑，明天轻松", "BUILD_SCHEDULE_DRAFT", ["get_distance_matrix", "build_schedule_draft"], "PROPOSED"),
        ("按每天不同节奏调整", "UPDATE_DAY_SPECIFIC_CONSTRAINTS", ["build_schedule_draft"], "PROPOSED"),
    ], ["current_request_overrides_memory", "preserve_day_specific_constraints"]),
]


def build_dataset() -> dict[str, Any]:
    dataset = json.loads(BASE.read_text(encoding="utf-8"))
    for case_id, family, task_type, state, turns, invariants in EXTRA:
        if any(existing["id"] == case_id for existing in dataset["cases"]):
            raise ValueError(f"duplicate scenario id: {case_id}")
        parsed_turns = []
        for entry in turns:
            user, action, tools, status, *extra = entry
            turn = {
                "user": user,
                "expected_action": action,
                "allowed_tools": tools,
                "expected_status": status,
            }
            if extra and extra[0] is True:
                turn["confirmation_received"] = True
            parsed_turns.append(turn)
        dataset["cases"].append({
            "id": case_id,
            "family": family,
            "task_type": task_type,
            "initial_state": state,
            "turns": parsed_turns,
            "invariants": invariants,
        })
    dataset["dataset_id"] = "advisor_multiturn_v3"
    dataset["version"] = 3
    dataset["status"] = "FROZEN_CONTRACT_SET_NOT_SCORED"
    dataset["scope"] = (
        "60 curated multi-turn travel tasks across ten families, including recovery and compound constraints; "
        "contract fixtures only, not a model-quality benchmark."
    )
    dataset["frozen_before_scoring"] = True
    return dataset


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    dataset = build_dataset()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(dataset, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"dataset_id": dataset["dataset_id"], "case_count": len(dataset["cases"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
