from tests.evaluation.reproducible_experiment import run_experiment


def test_experiment_repeats_with_stable_metrics_and_records_limits():
    result = run_experiment()
    assert result["repeatability"]["runs_per_suite"] == 2
    assert result["repeatability"]["stable_outputs_match"] is True
    assert result["environment"]["provider_mode"] == "SYNTHETIC_DETERMINISTIC"
    assert result["environment"]["model"] is None
    assert result["environment"]["random_seed"] == 20261017
    assert result["environment"]["python_hash_seed"] == 20261017
    assert result["environment"]["runs_in_separate_processes"] is True
    assert len(set(result["environment"]["suite_process_ids"])) == 2
    assert result["environment"]["provider_credentials_disabled"] is True
    assert result["scope"].startswith("deterministic synthetic")
    inputs = result["environment"]["input_sha256"]
    assert "apps/api/src/travelpilot_api/api/v1/agent.py" in inputs
    assert "packages/core/src/travelpilot_core/agents/planner.py" in inputs
    assert "pyproject.toml" in inputs
    assert "uv.lock" in inputs
