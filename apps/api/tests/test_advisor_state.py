from travelpilot_api.advisor.state import (
    cancelled_task_reply,
    cancels_pending_task,
    pending_agent_question,
    pending_agent_question_from_context,
    pending_context_fingerprint,
    pending_question_matches_context,
    resume_pending_task,
)
from travelpilot_api.advisor.turn_lifecycle import resolve_pending_turn


def test_pending_agent_question_is_recovered_only_from_an_open_schedule_turn():
    payload = {
        "schedule_recommendation_requested": True,
        "pending_task": "帮我安排当前行程",
        "pending_question": "每天最多安排几个地点？",
        "agent_trace": {"status": "NEEDS_USER_INPUT"},
    }

    assert pending_agent_question(payload).task == "帮我安排当前行程"
    assert pending_agent_question({**payload, "agent_trace": {"status": "SUCCEEDED"}}) is None
    assert pending_agent_question({**payload, "pending_task": None}) is None


def test_pending_agent_question_recovers_a_search_task_and_context_round_trip():
    payload = {
        "agent_trace": {"status": "NEEDS_USER_INPUT"},
        "pending_task_type": "SEARCH",
        "pending_task": "给我推荐适合亲子游的地点",
        "pending_question": "更偏好室内还是户外？",
        "context_revision": 9,
    }

    from_payload = pending_agent_question(payload)
    assert from_payload is not None
    assert from_payload.task_type == "SEARCH"

    from_context = pending_agent_question_from_context({
        "pending_agent_question": {
            "task": from_payload.task,
            "question": from_payload.question,
            "context_revision": from_payload.context_revision,
            "task_type": from_payload.task_type,
        }
    })
    assert from_context == from_payload


def test_pending_agent_question_accepts_weather_as_a_resumable_read_task():
    pending = pending_agent_question_from_context({
        "pending_agent_question": {
            "task": "这几天需要带伞吗",
            "question": "请先补充旅行日期",
            "context_revision": 3,
            "task_type": "WEATHER",
        }
    })
    assert pending is not None
    assert pending.task_type == "WEATHER"


def test_pending_general_advisor_question_can_resume_open_ended_conversation():
    pending = pending_agent_question_from_context({
        "pending_agent_question": {
            "task": "帮我想想秋天适合去哪旅行",
            "question": "你更想看自然风景还是体验城市文化？",
            "context_revision": 0,
            "task_type": "GENERAL",
            "task_id": "task-open-1",
        }
    })
    assert pending is not None
    resumed, prompt = resume_pending_task(pending, "更想看自然风景，时间大概三天")
    assert resumed.task_type == "GENERAL"
    assert resumed.task_id == "task-open-1"
    assert "帮我想想秋天适合去哪旅行" in prompt
    assert "自然风景" in prompt


def test_pending_agent_question_has_bounded_text_and_explicit_cancel_phrases():
    state = pending_agent_question({
        "schedule_recommendation_requested": True,
        "pending_task": "x" * 1400,
        "pending_question": "y" * 700,
        "agent_trace": {"status": "NEEDS_USER_INPUT"},
    })

    assert len(state.task) == 1200
    assert len(state.question) == 500
    assert cancels_pending_task("  取消吧 ")
    assert cancels_pending_task("never mind")
    assert not cancels_pending_task("别取消，按轻松节奏")


def test_cancelled_task_reply_uses_the_pending_travel_task_type():
    assert "餐饮推荐已停止" in cancelled_task_reply("MEAL")
    assert "住宿搜索已停止" in cancelled_task_reply("LODGING")
    assert "行程调整已停止" in cancelled_task_reply("SCHEDULE")
    assert "这项旅行任务已停止" in cancelled_task_reply("UNKNOWN")


def test_pending_agent_question_is_bound_to_the_trip_context_revision():
    state = pending_agent_question({
        "schedule_recommendation_requested": True,
        "pending_task": "安排当前行程",
        "pending_question": "想要轻松节奏吗？",
        "context_revision": 4,
        "agent_trace": {"status": "NEEDS_USER_INPUT"},
    })

    assert state.context_revision == 4
    assert pending_question_matches_context(state, {"revision": 4})
    assert not pending_question_matches_context(state, {"revision": 5})
    assert not pending_question_matches_context(state, {})


def test_pending_agent_question_with_invalid_revision_fails_closed():
    state = pending_agent_question({
        "schedule_recommendation_requested": True,
        "pending_task": "安排当前行程",
        "pending_question": "想要轻松节奏吗？",
        "context_revision": "4",
        "agent_trace": {"status": "NEEDS_USER_INPUT"},
    })

    assert state is None


def test_pending_question_is_recovered_from_session_state():
    pending = pending_agent_question_from_context({
        "pending_agent_question": {
            "task": "安排当前行程",
            "question": "想要轻松节奏吗？",
            "context_revision": 4,
        },
    })

    assert pending.task == "安排当前行程"
    assert pending.question == "想要轻松节奏吗？"
    assert pending.context_revision == 4
    assert pending_agent_question_from_context({"pending_agent_question": {"context_revision": 4}}) is None


def test_pending_question_preserves_multiturn_answer_history_and_task_identity():
    payload = {
        "agent_trace": {"status": "NEEDS_USER_INPUT"},
        "pending_task_type": "SEARCH",
        "pending_task": "推荐适合亲子游的成都地点",
        "pending_question": "更偏好室内还是户外？",
        "pending_task_id": "task-123",
        "pending_answers": ["希望有室内也有户外"],
        "pending_turn_number": 2,
        "context_revision": 7,
    }

    state = pending_agent_question(payload)
    assert state is not None
    assert state.task_id == "task-123"
    assert state.answers == ("希望有室内也有户外",)
    assert state.turn_number == 2
    restored = pending_agent_question_from_context({
        "pending_agent_question": {
            "task": state.task,
            "question": state.question,
            "task_type": state.task_type,
            "task_id": state.task_id,
            "answers": list(state.answers),
            "turn_number": state.turn_number,
            "context_revision": state.context_revision,
        }
    })
    assert restored == state


def test_pending_question_survives_transient_selection_revision_but_not_trip_change():
    context = {
        "revision": 4,
        "selected_candidate_ids": ["meal-1"],
        "base_trip": {"destination": "梅州", "start_date": "2026-10-17", "end_date": "2026-10-18"},
        "current_plan": {"id": "plan-1", "version": 3},
        "planning_state": {"slots": {"destination": {"value": "梅州"}, "start_date": {"value": "2026-10-17"}}},
    }
    pending = pending_agent_question_from_context({
        "pending_agent_question": {
            "task": "为当前旅行安排午餐", "question": "午餐想几点？",
            "context_revision": 4,
            "context_fingerprint": pending_context_fingerprint(context),
        },
    })
    assert pending is not None

    # Candidate selection changes the broad session revision, not the current trip.
    after_selection = {**context, "revision": 5, "selected_candidate_ids": ["meal-2"]}
    assert pending_question_matches_context(pending, after_selection)

    after_trip_edit = {
        **after_selection,
        "current_plan": {"id": "plan-1", "version": 4},
    }
    assert not pending_question_matches_context(pending, after_trip_edit)
def test_destination_recommendation_kind_survives_message_and_context_round_trip():
    state = pending_agent_question({
        "agent_trace": {"status": "NEEDS_USER_INPUT"},
        "pending_task_type": "DISCOVERY",
        "pending_task_kind": "DESTINATION_RECOMMENDATION",
        "pending_task": "继续推荐适合我的目的地",
        "pending_question": "你更喜欢历史街区还是海滨？",
        "pending_task_id": "destination-1",
        "pending_turn_number": 1,
        "pending_answers": [],
        "context_revision": 0,
    })
    assert state is not None
    assert state.task_kind == "DESTINATION_RECOMMENDATION"
    restored = pending_agent_question_from_context({
        "pending_agent_question": {
            "task": state.task,
            "question": state.question,
            "context_revision": state.context_revision,
            "task_type": state.task_type,
            "task_id": state.task_id,
            "answers": list(state.answers),
            "turn_number": state.turn_number,
            "task_kind": state.task_kind,
        }
    })
    assert restored == state

def test_pending_question_rejects_unbounded_or_malformed_answer_history():
    base = {
        "agent_trace": {"status": "NEEDS_USER_INPUT"},
        "pending_task_type": "SEARCH",
        "pending_task": "推荐地点",
        "pending_question": "偏好什么？",
    }
    assert pending_agent_question({**base, "pending_answers": ["回答"] * 7}) is None
    assert pending_agent_question({**base, "pending_answers": [None]}) is None
    assert pending_agent_question({**base, "pending_turn_number": True}) is None


def test_resume_pending_task_keeps_original_goal_and_bounded_answer_history():
    pending = pending_agent_question_from_context({
        "pending_agent_question": {
            "task": "安排一个轻松的两日行程",
            "question": "你更偏好自然还是历史文化？",
            "context_revision": 2,
            "task_type": "SCHEDULE",
            "answers": ["不想太早出门"],
            "turn_number": 2,
        }
    })

    resumed, prompt = resume_pending_task(pending, "喜欢自然景点，午餐时间要留足")

    assert resumed.turn_number == 3
    assert resumed.answers == ("不想太早出门", "喜欢自然景点，午餐时间要留足")
    assert "安排一个轻松的两日行程" in prompt
    assert "不想太早出门" in prompt
    assert "午餐时间要留足" in prompt


def test_pending_turn_lifecycle_resumes_task_and_clears_context_checkpoint():
    context = {
        "revision": 2,
        "pending_agent_question": {
            "task": "安排成都两日游",
            "question": "希望每天最多安排几个地点？",
            "context_revision": 2,
            "task_type": "SCHEDULE",
            "answers": [],
            "turn_number": 1,
        },
    }

    result = resolve_pending_turn(
        context=context,
        previous_payload=None,
        content="每天三个左右",
    )

    assert result.pending_question is not None
    assert result.pending_question.answers == ("每天三个左右",)
    assert "安排成都两日游" in result.turn_goal
    assert result.continuing_task == result.pending_question.__class__(
        task="安排成都两日游",
        question="希望每天最多安排几个地点？",
        context_revision=2,
        task_type="SCHEDULE",
    )
    assert "pending_agent_question" not in context
    assert not result.invalidated
    assert not result.cancelled


def test_pending_turn_accepts_short_choice_after_transient_selection_change():
    context = {
        "revision": 8,
        "selected_candidate_ids": ["restaurant-a"],
        "base_trip": {"destination": "梅州", "start_date": "2026-10-17", "end_date": "2026-10-18"},
        "current_plan": {"id": "plan-a", "version": 2},
        "planning_state": {"slots": {"destination": {"value": "梅州"}}},
    }
    context["pending_agent_question"] = {
        "task": "为10月17日安排午餐",
        "question": "你希望午餐几点？",
        "context_revision": 8,
        "context_fingerprint": pending_context_fingerprint(context),
        "task_type": "MEAL",
        "answers": [],
        "turn_number": 1,
    }
    context["revision"] = 9
    context["selected_candidate_ids"] = ["restaurant-b"]

    result = resolve_pending_turn(
        context=context,
        previous_payload=None,
        content="方案 A：11:00–12:00",
    )

    assert not result.invalidated
    assert result.pending_question is not None
    assert result.pending_question.answers == ("方案 A：11:00–12:00",)
    assert "为10月17日安排午餐" in result.turn_goal


def test_pending_turn_lifecycle_cancellation_and_context_invalidation_are_distinct():
    pending_context = {
        "revision": 4,
        "pending_agent_question": {
            "task": "找适合亲子的地点",
            "question": "更喜欢室内还是户外？",
            "context_revision": 4,
            "task_type": "SEARCH",
        },
    }
    cancelled = resolve_pending_turn(
        context=pending_context,
        previous_payload=None,
        content="取消吧",
    )
    assert cancelled.cancelled
    assert cancelled.cancelled_task_type == "SEARCH"
    assert cancelled.pending_question is None
    assert cancelled.turn_goal == "取消吧"

    stale_context = {
        "revision": 5,
        "pending_agent_question": {
            "task": "找适合亲子的地点",
            "question": "更喜欢室内还是户外？",
            "context_revision": 4,
            "task_type": "SEARCH",
        },
    }
    invalidated = resolve_pending_turn(
        context=stale_context,
        previous_payload=None,
        content="室内",
    )
    assert invalidated.invalidated
    assert invalidated.pending_question is None
    assert invalidated.turn_goal == "室内"
