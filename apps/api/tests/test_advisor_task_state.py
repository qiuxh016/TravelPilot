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


def test_date_reschedule_question_becomes_clickable_choices():
    snapshot = AdvisorTaskSnapshot(
        task_id="route-date-task",
        task_type="SCHEDULE",
        status="WAITING_USER",
        goal="将鼓浪屿文化线安排到合适日期",
        context_revision=3,
        turn_number=3,
        answers=["鼓浪屿文化线", "尽量不要太赶"],
        question="要不要我把这条线放到10/23，再帮你安排10/24和10/25？",
        action_summary="建议调整每日游览顺序",
    )

    assert [(option.label, option.answer) for option in snapshot.confirmation_options] == [
        ("可以，按这个时间继续", "可以，按你刚才建议的日期和时间继续安排"),
        ("换个日期或时间", "我想调整刚才建议的日期或时间，先不要按原时间安排"),
        ("暂不安排", "暂不安排这项内容"),
    ]


def test_action_confirmation_offers_continue_without_granting_commit_consent():
    snapshot = AdvisorTaskSnapshot(
        task_id="schedule-task",
        task_type="SCHEDULE",
        status="WAITING_USER",
        goal="按日期安排每天的游览顺序",
        context_revision=2,
        turn_number=2,
        answers=["10月17日到梅州，住在市区"],
        question="要我现在按日期把每天的游览顺序排出来吗？",
        action_summary="生成待确认的行程建议",
    )

    assert [(option.label, option.answer) for option in snapshot.confirmation_options] == [
        ("继续，生成待确认建议", "好的，继续生成待确认建议"),
        ("补充或调整要求", "我想先补充或调整要求"),
        ("暂不处理", "暂不处理这项任务"),
    ]


def test_binary_preference_question_becomes_clickable_choices_after_context_is_known():
    snapshot = AdvisorTaskSnapshot(
        task_id="search-task",
        task_type="SEARCH",
        status="WAITING_USER",
        goal="推荐餐馆",
        context_revision=2,
        turn_number=2,
        answers=["想找梅州的餐馆"],
        question="你更喜欢客家菜还是潮汕菜？",
        action_summary="了解口味偏好",
    )
    assert [(option.label, option.answer) for option in snapshot.confirmation_options] == [
        ("客家菜", "我选客家菜"),
        ("潮汕菜", "我选潮汕菜"),
    ]


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
