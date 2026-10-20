from travelpilot_api.advisor.session_budget import (
    handle_budget_continuation,
    queue_budget_continuation,
    record_session_usage,
    remaining_session_output_tokens,
    session_output_limit,
    session_usage,
)


def test_session_output_usage_is_accumulated_and_capped():
    context = {}

    assert record_session_usage(context, 90, 40) == {
        "input_tokens": 90, "output_tokens": 40, "total_tokens": 130,
    }
    assert record_session_usage(context, 60, 35) == {
        "input_tokens": 150, "output_tokens": 75, "total_tokens": 225,
    }
    assert remaining_session_output_tokens(context, 100) == 25


def test_continuation_requires_explicit_consent_and_grants_one_turn():
    context = {}
    queue_budget_continuation(context, "请规划两天的梅州行程")

    action, goal, grant = handle_budget_continuation(
        context, "再找梅州附近的餐厅", per_turn_grant=1200,
    )
    assert (action, goal, grant) == ("PROMPT", None, 0)
    assert context["advisor_llm_budget_pending"]["input"] == "再找梅州附近的餐厅"
    assert session_output_limit(context, 12000) == 12000

    action, goal, grant = handle_budget_continuation(
        context, "继续本次对话", per_turn_grant=1200,
    )
    assert (action, goal, grant) == ("CONTINUE", "再找梅州附近的餐厅", 1200)
    assert context["advisor_llm_budget_extension"] == 1200
    assert remaining_session_output_tokens(context, 12000) == 13200
    assert "advisor_llm_budget_pending" not in context


def test_continuation_grant_is_bounded_and_cancel_clears_pending():
    context = {"advisor_llm_budget_extension": 999_999}
    queue_budget_continuation(context, "继续想想路线")

    action, goal, grant = handle_budget_continuation(
        context, "继续", per_turn_grant=50_000,
    )
    assert (action, goal, grant) == ("CONTINUE", "继续想想路线", 8000)
    assert context["advisor_llm_budget_extension"] == 1_000_000

    queue_budget_continuation(context, "准备取消")
    context["pending_agent_question"] = {"task": "old"}
    action, goal, grant = handle_budget_continuation(
        context, "取消继续", per_turn_grant=1200,
    )
    assert (action, goal, grant) == ("CANCEL", None, 0)
    assert "advisor_llm_budget_pending" not in context
    assert "pending_agent_question" not in context


def test_invalid_stored_usage_is_ignored():
    context = {"advisor_llm_usage": {"input_tokens": True, "output_tokens": -9}}
    assert session_usage(context) == {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
