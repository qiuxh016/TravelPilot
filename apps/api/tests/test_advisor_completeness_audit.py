from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

from travelpilot_api.advisor.context import (
    draft_meal_follow_up_options,
    trip_action_items,
    trip_completeness_audit,
)


def test_audit_reports_missing_meals_lodging_origin_and_route_evidence():
    row = SimpleNamespace(
        start_date=date(2026, 11, 2), end_date=date(2026, 11, 3), candidates=[],
        context={
            "planning_inputs": {"origin": ""},
            "current_plan": {
                "stops": [
                    {"name": "城市公园", "category": "公园", "planning_category": "ATTRACTION",
                     "day": "2026-11-02", "start_time": "09:00", "end_time": "11:00"},
                    {"name": "博物馆", "category": "博物馆", "planning_category": "ATTRACTION",
                     "day": "2026-11-02", "start_time": "15:00", "end_time": "17:00"},
                ],
            },
        },
    )

    audit = trip_completeness_audit(row)

    assert "2026-11-02 尚未明确安排午餐" in audit
    assert "2026-11-02 尚未明确安排晚餐" in audit
    assert any("尚未添加住宿备选" in item for item in audit)
    assert any("没有出发地信息" in item for item in audit)
    assert any("交通路线尚未核验" in item for item in audit)
    assert any("2026-11-03 目前没有任何已安排地点" in item for item in audit)


def test_audit_does_not_flag_meal_when_food_is_explicitly_in_the_plan():
    row = SimpleNamespace(
        start_date=date(2026, 11, 2), end_date=date(2026, 11, 2), candidates=[],
        context={"planning_inputs": {"origin": "成都东站"}, "current_plan": {"stops": [
            {"name": "本地餐馆", "category": "餐饮服务", "planning_category": "FOOD",
             "day": "2026-11-02", "start_time": "12:00", "end_time": "13:00"},
        ]}},
    )

    audit = trip_completeness_audit(row)

    assert "2026-11-02 尚未明确安排午餐" not in audit
    assert any("尚未明确安排晚餐" in item for item in audit)


def test_audit_uses_current_draft_meals_while_user_continues_with_lodging():
    row = SimpleNamespace(
        start_date=date(2026, 11, 2), end_date=date(2026, 11, 2), candidates=[],
        context={
            "revision": 8,
            "planning_inputs": {"origin": "成都东站"},
            "current_plan": {"stops": [{
                "candidate_id": "park", "name": "城市公园", "planning_category": "ATTRACTION",
                "day": "2026-11-02", "start_time": "09:00", "end_time": "11:00",
            }]},
        },
        draft={
            "context_revision": 8,
            "stops": [
                {"candidate_id": "park", "name": "城市公园", "planning_category": "ATTRACTION",
                 "day": "2026-11-02", "start_time": "09:00", "end_time": "11:00"},
                {"candidate_id": "lunch", "name": "已选午餐店", "planning_category": "FOOD",
                 "meal_type": "LUNCH", "day": "2026-11-02", "start_time": "12:00", "end_time": "13:00"},
            ],
        },
    )

    audit = trip_completeness_audit(row)

    assert "2026-11-02 尚未明确安排午餐" not in audit
    assert "2026-11-02 尚未明确安排晚餐" in audit


def test_audit_ignores_stale_draft_meals_after_trip_context_changes():
    row = SimpleNamespace(
        start_date=date(2026, 11, 2), end_date=date(2026, 11, 2), candidates=[],
        context={
            "revision": 9,
            "planning_inputs": {"origin": "成都东站"},
            "current_plan": {"stops": [{
                "candidate_id": "park", "name": "城市公园", "planning_category": "ATTRACTION",
                "day": "2026-11-02", "start_time": "09:00", "end_time": "11:00",
            }]},
        },
        draft={
            "context_revision": 8,
            "stops": [{"candidate_id": "lunch", "name": "已选午餐店", "planning_category": "FOOD",
                       "day": "2026-11-02", "start_time": "12:00", "end_time": "13:00"}],
        },
    )

    assert "2026-11-02 尚未明确安排午餐" in trip_completeness_audit(row)


def test_meal_completeness_uses_time_window_overlap_not_only_start_time():
    row = SimpleNamespace(
        start_date=date(2026, 11, 2), end_date=date(2026, 11, 2), candidates=[],
        context={"planning_inputs": {"origin": "成都东站"}, "current_plan": {"stops": [
            {
                "name": "早午餐店", "planning_category": "FOOD",
                "day": "2026-11-02", "start_time": "10:30", "end_time": "12:00",
            },
            {
                "name": "短暂停留咖啡馆", "planning_category": "FOOD",
                "day": "2026-11-02", "start_time": "16:50", "end_time": "17:10",
            },
        ]}},
    )

    audit = trip_completeness_audit(row)

    assert "2026-11-02 尚未明确安排午餐" not in audit
    assert "2026-11-02 尚未明确安排晚餐" in audit


def test_dinner_stop_starting_before_window_counts_when_it_covers_enough_time():
    row = SimpleNamespace(
        start_date=date(2026, 11, 2), end_date=date(2026, 11, 2), candidates=[],
        context={"planning_inputs": {"origin": "成都东站"}, "current_plan": {"stops": [
            {
                "name": "晚餐餐厅", "planning_category": "FOOD",
                "day": "2026-11-02", "start_time": "16:30", "end_time": "18:00",
            },
        ]}},
    )

    audit = trip_completeness_audit(row)

    assert "2026-11-02 尚未明确安排晚餐" not in audit


def test_short_food_stop_that_only_touches_meal_window_does_not_count_as_meal():
    row = SimpleNamespace(
        start_date=date(2026, 11, 2), end_date=date(2026, 11, 2), candidates=[],
        context={"planning_inputs": {"origin": "成都东站"}, "current_plan": {"stops": [
            {
                "name": "咖啡馆", "planning_category": "FOOD",
                "day": "2026-11-02", "start_time": "13:40", "end_time": "13:55",
            },
        ]}},
    )

    audit = trip_completeness_audit(row)

    assert "2026-11-02 尚未明确安排午餐" in audit


def test_route_completeness_requires_fresh_evidence_for_current_stops_and_revision():
    now = datetime.now(UTC)
    stops = [
        {"candidate_id": "a", "name": "公园", "planning_category": "ATTRACTION",
         "day": "2026-11-02", "start_time": "09:00", "end_time": "11:00"},
        {"candidate_id": "b", "name": "博物馆", "planning_category": "ATTRACTION",
         "day": "2026-11-02", "start_time": "15:00", "end_time": "17:00"},
    ]
    evidence = {
        "candidate_ids": ["a", "b"], "context_revision": 7,
        "durations_min": [[0, 20], [25, 0]], "status": "VERIFIED",
        "source": "AMap Web Service", "retrieved_at": now.isoformat(),
    }

    def build_row(route_evidence, revision=7, plan_stops=None):
        return SimpleNamespace(
            start_date=date(2026, 11, 2), end_date=date(2026, 11, 2), candidates=[],
            context={
                "revision": revision,
                "agent_route_evidence": route_evidence,
                "current_plan": {"stops": plan_stops if plan_stops is not None else stops},
            },
        )

    route_gap = "地点之间的实际交通路线尚未核验"
    assert route_gap not in trip_completeness_audit(build_row(evidence))
    assert route_gap in trip_completeness_audit(build_row(evidence, revision=8))
    assert route_gap in trip_completeness_audit(build_row(
        {**evidence, "retrieved_at": (now - timedelta(minutes=11)).isoformat()},
    ))
    assert route_gap in trip_completeness_audit(build_row(
        evidence, plan_stops=[*stops, {**stops[1], "candidate_id": "c"}],
    ))
    simulated_evidence = {
        **evidence, "status": "SIMULATED", "source": "Local simulated maps",
    }
    assert route_gap in trip_completeness_audit(build_row(simulated_evidence))


def test_audit_findings_become_user_invoked_meal_lodging_route_and_origin_actions():
    row = SimpleNamespace(
        start_date=date(2026, 11, 2), end_date=date(2026, 11, 3), candidates=[],
        context={
            "planning_inputs": {"origin": ""},
            "current_plan": {"stops": [
                {"name": "公园", "planning_category": "ATTRACTION", "day": "2026-11-02",
                 "start_time": "09:00", "end_time": "11:00"},
                {"name": "博物馆", "planning_category": "ATTRACTION", "day": "2026-11-02",
                 "start_time": "15:00", "end_time": "17:00"},
            ]},
        },
    )

    actions = trip_action_items(row)
    by_key = {item["key"]: item for item in actions}

    assert by_key["MEAL:午餐"]["action"] == "CHAT"
    assert "2026-11-02" in by_key["MEAL:午餐"]["prompt"]
    assert "2026-11-03" not in by_key["MEAL:午餐"]["prompt"]
    assert by_key["DAY:2026-11-03"]["action_label"] == "探索这一天"
    assert by_key["LODGING"]["action_label"] == "找住宿"
    assert by_key["ORIGIN"]["action"] == "EDIT_ORIGIN"
    assert by_key["ROUTES"]["action_label"] == "核验路线"
    assert by_key["GAP:2026-11-02:11:00–15:00"]["prompt"].find("不要默认修改计划") >= 0


def test_draft_meal_follow_ups_offer_only_uncovered_meals_without_mutating_plan():
    suggestions = [
        {"candidate_id": "park", "day": "2026-11-02", "start_time": "09:00"},
        {"candidate_id": "lunch", "day": "2026-11-02", "start_time": "12:00"},
        {"candidate_id": "museum", "day": "2026-11-03", "start_time": "10:00"},
    ]
    candidates = [
        {"id": "park", "planning_category": "ATTRACTION", "duration_min": 90},
        {"id": "lunch", "planning_category": "FOOD", "duration_min": 60},
        {"id": "museum", "planning_category": "ATTRACTION", "duration_min": 120},
    ]

    options = draft_meal_follow_up_options(
        suggestions, candidates, date(2026, 11, 2), date(2026, 11, 3),
    )

    by_key = {item["key"]: item for item in options}
    assert set(by_key) == {"DRAFT_MEAL:LUNCH", "DRAFT_MEAL:DINNER"}
    assert "2026-11-02" not in by_key["DRAFT_MEAL:LUNCH"]["prompt"]
    assert "2026-11-03" in by_key["DRAFT_MEAL:LUNCH"]["prompt"]
    assert "不写入正式行程" in by_key["DRAFT_MEAL:LUNCH"]["prompt"]


def test_draft_meal_follow_ups_ignore_incomplete_or_invalid_suggestions():
    options = draft_meal_follow_up_options(
        [{"candidate_id": "missing", "day": "2026-11-02", "start_time": "n/a"}],
        [], date(2026, 11, 2), date(2026, 11, 2),
    )

    assert options == []


def test_draft_follow_up_includes_long_gap_with_grounded_neighbor_names():
    suggestions = [
        {"candidate_id": "park", "day": "2026-11-02", "start_time": "09:00"},
        {"candidate_id": "museum", "day": "2026-11-02", "start_time": "15:00"},
    ]
    candidates = [
        {"id": "park", "name": "城市公园", "planning_category": "ATTRACTION", "duration_min": 60},
        {"id": "museum", "name": "城市博物馆", "planning_category": "ATTRACTION", "duration_min": 90},
    ]

    options = draft_meal_follow_up_options(
        suggestions, candidates, date(2026, 11, 2), date(2026, 11, 2),
    )

    gap = next(item for item in options if item["key"].startswith("DRAFT_GAP:"))
    assert "10:00–15:00" in gap["detail"]
    assert "城市公园" in gap["prompt"] and "城市博物馆" in gap["prompt"]
    assert "不要默认地点就在附近" in gap["prompt"]


def test_weather_impacts_appear_as_fresh_user_action_and_stale_draft_is_ignored():
    context = {
        "revision": 7,
        "planning_inputs": {"origin": "成都东站"},
        "current_plan": {"stops": [{
            "candidate_id": "walk", "name": "河边步道", "planning_category": "ATTRACTION",
            "day": "2026-11-02", "start_time": "09:00", "end_time": "11:00",
        }]},
    }
    row = SimpleNamespace(
        start_date=date(2026, 11, 2), end_date=date(2026, 11, 2), candidates=[], context=context,
        draft={"context_revision": 7, "weather": {"plan_impacts": [{
            "candidate_id": "walk", "name": "河边步道", "day": "2026-11-02",
            "reason": "户外且雨敏感度高。",
        }]}},
    )

    audit = trip_completeness_audit(row)
    action = next(item for item in trip_action_items(row) if item["key"].startswith("WEATHER:"))

    assert any("河边步道 可能受降水影响" in item for item in audit)
    assert action["action_label"] == "找室内备选"
    assert "不要自动替换或保存正式行程" in action["prompt"]
    assert "先让我选择候选" in action["prompt"]
    assert "选定后再用路线数据生成替换时间草案" in action["prompt"]
    assert "室内场所" in action["detail"]

    row.draft = {**row.draft, "context_revision": 6}
    assert not any("天气提醒：河边步道" in item for item in trip_completeness_audit(row))
    assert not any(item["key"].startswith("WEATHER:") for item in trip_action_items(row))
