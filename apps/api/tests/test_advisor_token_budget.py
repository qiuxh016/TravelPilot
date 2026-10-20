from types import SimpleNamespace

import pytest
from travelpilot_api.advisor.budgets import (
    aggregate_turn_token_usage,
    remaining_turn_output_tokens,
)
from travelpilot_api.advisor.turn_service import (
    _persist_failed_turn_usage,
    _turn_usage_snapshot,
)


@pytest.mark.parametrize(
    ("budget", "extraction", "runtime_usage", "expected"),
    [
        (1000, 100, [200, 150, 50], 500),
        (100, 20, [80], 0),
        (100, 0, [-10, 20], 80),
        (100, True, [True, 25], 75),
        (-10, 0, [], 0),
    ],
)
def test_turn_budget_subtracts_all_completed_model_output(budget, extraction, runtime_usage, expected):
    runtimes = [SimpleNamespace(output_tokens_used=usage) for usage in runtime_usage]

    assert remaining_turn_output_tokens(budget, extraction, *runtimes) == expected


def test_turn_token_report_includes_route_search_schedule_and_composer_usage():
    usage = aggregate_turn_token_usage(
        120,
        20,
        90,
        30,
        SimpleNamespace(execution_metrics={"input_tokens": 40, "output_tokens": 10}),
        SimpleNamespace(execution_metrics={"input_tokens": 60, "output_tokens": 15}),
        SimpleNamespace(execution_metrics={"input_tokens": 50, "output_tokens": 12}),
    )

    assert usage == {"input_tokens": 310, "output_tokens": 87, "total_tokens": 397}


def test_partial_turn_usage_can_be_aggregated_before_a_later_tool_failure():
    usage = _turn_usage_snapshot(
        SimpleNamespace(token_usage=210, output_tokens=35),
        0,
        0,
        SimpleNamespace(execution_metrics={"input_tokens": 50, "output_tokens": 12}),
        None,
        None,
    )

    assert usage == {"input_tokens": 225, "output_tokens": 47, "total_tokens": 272}


@pytest.mark.asyncio
async def test_failed_turn_persists_known_token_usage_after_parent_rollback():
    class FakeDB:
        committed = False

        def __init__(self, current_row):
            self.current_row = current_row

        async def scalar(self, _query):
            return self.current_row

        async def commit(self):
            self.committed = True

    row = SimpleNamespace(id="session-1", owner_id="owner-1", context={"existing": "preserved"})
    db = FakeDB(row)

    await _persist_failed_turn_usage(
        db,
        session_id=row.id,
        owner_id=row.owner_id,
        extraction=SimpleNamespace(token_usage=210, output_tokens=35),
        reply_tokens=0,
        reply_output_tokens=0,
        search_runtime=SimpleNamespace(execution_metrics={"input_tokens": 50, "output_tokens": 12}),
        schedule_runtime=None,
        route_runtime=None,
    )

    assert db.committed is True
    assert row.context["existing"] == "preserved"
    assert row.context["advisor_llm_usage"] == {
        "input_tokens": 225, "output_tokens": 47, "total_tokens": 272,
    }
