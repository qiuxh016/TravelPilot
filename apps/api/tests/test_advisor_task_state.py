import pytest
from travelpilot_api.advisor.task_state import (
    AdvisorTaskSnapshot,
    resolve_task_status,
    restore_task_snapshot,
)


def test_task_snapshot_captures_pending_multi_turn_state():
    snapshot = AdvisorTaskSnapshot(
        task_id="task-123",
        task_type="GENERAL",
        status="WAITING_USER",
        goal="帮我规划一趟轻松的成都旅行",
        context_revision=4,
        turn_number=2,
        answers=["大概两天，不想太早出门"],
        question="你更偏好自然景点还是城市文化？",
        action_summary="询问偏好以继续推荐",
    )

    assert restore_task_snapshot(snapshot.model_dump()) == snapshot


def test_completed_meal_proposal_confirmation_becomes_clickable_actions():
    snapshot = AdvisorTaskSnapshot(
        task_id="meal-task",
        task_type="MEAL",
        status="WAITING_USER",
        goal="安排第1天午餐",
        context_revision=2,
        turn_number=2,
        answers=["鑫丰特色餐饮"],
        question="需要我把鑫丰特色餐饮作为第1天午餐的待确认安排吗？",
        action_summary="确认餐饮地点",
    )

    assert [(option.label, option.answer) for option in snapshot.confirmation_options] == [
        ("确认，加入安排", "确认，按当前选择加入安排"),
        ("返回调整", "先不确认，我想调整这项安排"),
        ("暂不安排", "暂不安排这项内容"),
    ]


def test_preference_question_does_not_become_yes_no_confirmation():
    snapshot = AdvisorTaskSnapshot(
        task_id="search-task",
        task_type="SEARCH",
        status="WAITING_USER",
        goal="推荐餐馆",
        context_revision=2,
        turn_number=1,
        question="你更喜欢客家菜还是潮汕菜？",
        action_summary="了解口味偏好",
    )
    assert snapshot.confirmation_options == []


def test_confirmation_question_without_a_prior_user_choice_remains_open_ended():
    snapshot = AdvisorTaskSnapshot(
        task_id="meal-task",
        task_type="MEAL",
        status="WAITING_USER",
        goal="安排午餐",
        context_revision=2,
        turn_number=1,
        question="需要我把鑫丰特色餐饮作为第1天午餐的待确认安排吗？",
        action_summary="确认餐饮地点",
    )
    assert snapshot.confirmation_options == []


def test_task_snapshot_rejects_unbounded_or_unknown_state():
    base = {
        "task_id": "task-123",
        "task_type": "SEARCH",
        "status": "WAITING_USER",
        "goal": "找适合亲子游的地点",
        "context_revision": 1,
        "turn_number": 2,
        "answers": ["室内活动"],
    }
    assert restore_task_snapshot({**base, "question": "更喜欢室内还是户外？"}) is not None
    assert restore_task_snapshot({**base, "answers": ["室内活动"] * 7}) is None
    assert restore_task_snapshot({**base, "status": "WRITING_DATABASE"}) is None
    assert restore_task_snapshot({**base, "secret": "unrecognized"}) is None


def test_task_snapshot_enforces_waiting_question_and_turn_consistency():
    base = {
        "task_id": "task-123",
        "task_type": "GENERAL",
        "status": "WAITING_USER",
        "goal": "规划一趟旅行",
        "context_revision": 1,
        "turn_number": 2,
        "answers": ["两天"],
    }
    assert restore_task_snapshot(base) is None
    assert restore_task_snapshot({**base, "question": "你偏好什么节奏？", "turn_number": 1}) is None
    assert restore_task_snapshot({**base, "question": "你偏好什么节奏？"}) is not None


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        ({"stale": True, "cancelled": True}, "STALE"),
        ({"cancelled": True, "waiting_for_user": True}, "CANCELLED"),
        ({"waiting_for_user": True, "tool_failed": True}, "WAITING_USER"),
        ({"proposed": True}, "PROPOSED"),
        ({"tool_failed": True, "has_partial_results": True}, "PARTIAL"),
        ({"proposed": True, "loop_statuses": ["STEP_LIMIT"]}, "PARTIAL"),
        ({"loop_statuses": ["REJECTED"]}, "FAILED"),
        ({"loop_statuses": ["FALLBACK"]}, "FAILED"),
        ({"loop_statuses": ["SUCCEEDED"]}, "COMPLETED"),
        ({}, "COMPLETED"),
    ],
)
def test_task_outcome_maps_loop_terminals_to_consistent_user_status(outcome, expected):
    assert resolve_task_status(**outcome) == expected
