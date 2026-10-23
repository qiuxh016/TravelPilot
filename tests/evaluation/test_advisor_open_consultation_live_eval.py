import json
from types import SimpleNamespace

import pytest
from travelpilot_core.agents.loop import AgentAction


def test_v2_cases_freeze_context_and_per_case_tool_scope():
    dataset = json.loads(evaluation.CASE_FILE.read_text(encoding="utf-8"))
    assert dataset["frozen_before_scoring"] is True
    assert len(dataset["cases"]) == 10
    assert all("known_facts" in case and "available_tools" in case for case in dataset["cases"])
    assert all(set(case["available_tools"]) <= set(evaluation.TOOL_SCHEMAS) for case in dataset["cases"])
    assert not any("commit" in name or "delete" in name for name in evaluation.TOOL_SCHEMAS)

from tests.evaluation import advisor_open_consultation_live_eval as evaluation


@pytest.mark.asyncio
async def test_live_evaluator_is_fixed_at_two_bounded_decisions_per_case(monkeypatch):
    calls = []
    monkeypatch.setattr(evaluation.settings, "llm_api_key", "test-key")
    monkeypatch.setattr(evaluation.settings, "llm_model", "test-model")
    monkeypatch.setattr(evaluation.settings, "llm_base_url", "https://example.test/v1")

    async def fake_decision(observation, _schemas, **kwargs):
        calls.append((observation, kwargs["token_budget"]))
        return AgentAction(kind="ASK_USER", question="请补充一项信息"), SimpleNamespace(
            prompt_tokens=10, completion_tokens=8, total_tokens=18,
        )

    monkeypatch.setattr(evaluation, "decide_next_action", fake_decision)
    result = await evaluation.run_live()

    assert result["requests"] == 20
    assert result["requested_output_token_ceiling"] == 2560
    assert len(calls) == 20
    assert all(budget == 128 for _, budget in calls)
    assert all("verification_contract" not in obs for obs, _ in calls[:10])
    assert all("verification_contract" in obs for obs, _ in calls[10:])
