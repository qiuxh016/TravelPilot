import json
from pathlib import Path

import pytest

from tests.evaluation.advisor_multiturn_runtime_eval import evaluate_dataset


@pytest.mark.asyncio
async def test_frozen_agent_owned_multiturn_cases_run_through_production_loop():
    dataset_path = (
        Path(__file__).parent / "cases" / "advisor_multiturn_v5.json"
    )
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))

    result = await evaluate_dataset(dataset)

    assert result["status"] == "PRODUCTION_CONSTRAINED_LOOP_LOCAL_FIXTURE_REPLAY"
    assert result["provider_mode"] == "LOCAL_SYNTHETIC_TOOLS_AND_SCRIPTED_POLICY"
    assert result["case_count"] == 60
    assert result["turn_count"] == 129
    assert result["agent_decision_turns_executed"] == 76
    assert result["tool_calls_executed"] > 0
    assert result["user_questions_returned"] > 0
    assert result["loop_status_counts"]["FAILED"] > 0
    assert result["loop_status_counts"]["NEEDS_USER_INPUT"] > 0
    assert result["unapproved_tool_calls"] == 0
    assert result["failures"] == []
    assert len(result["case_file_sha256"]) == 64
    assert "not action quality" in result["interpretation"]
