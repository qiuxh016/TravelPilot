import json

import pytest

from tests.evaluation import summarize_product_live_metrics as metrics


def _scenario_report(scenario_id: str) -> dict:
    return {
        "scenario_id": scenario_id,
        "provider_mode": "LIVE_MODEL_REAL_ADVISOR_API_FAKE_MAPS",
        "temporary_account_deleted": True,
        "resources": {"formal_write_prevented": True},
        "product_assessment": {"passed": True, "checks": {"safe": True}},
        "failure": None,
        "turns": [{
            "model_usage": {"input_tokens": 50, "output_tokens": 12},
            "actions": [{"tool": "search_places", "status": "VERIFIED"}],
            "stage_statuses": [{"stage": "SEARCH", "status": "SUCCEEDED"}],
            "stage_decision_history": [{"decision_source": "MODEL"}],
            "pending_question": None,
            "task_status": "COMPLETED",
        }],
    }


def test_build_trace_metrics_derives_safe_hashed_aggregate(tmp_path, monkeypatch):
    results = tmp_path / "results"
    results.mkdir()
    monkeypatch.setattr(metrics, "RESULTS", results)
    scenario_path = results / "run_case.json"
    scenario_path.write_text(json.dumps(_scenario_report("case-a")), encoding="utf-8")
    manifest_path = results / "aggregate.json"
    manifest_path.write_text(json.dumps({
        "evaluation_id": "test-run",
        "model": "test-model",
        "run_at_utc": "2026-10-19T00:00:00Z",
        "maps_weather_mode": "FAKE_MAPS_AND_LOCAL_SYNTHETIC_WEATHER",
        "scenario_count": 1,
        "all_scenarios_passed": True,
        "scenario_reports": [str(scenario_path)],
    }), encoding="utf-8")

    report = metrics.build_trace_metrics(manifest_path)

    assert report["aggregate_metrics"]["input_tokens"] == 50
    assert report["aggregate_metrics"]["output_tokens"] == 12
    assert report["aggregate_metrics"]["tool_action_count"] == 1
    assert report["aggregate_metrics"]["latency_sample_count"] == 0
    assert report["aggregate_metrics"]["mean_request_latency_ms"] is None
    assert len(report["source_manifest_sha256"]) == 64
    assert len(report["scenario_report_sha256"][scenario_path.name]) == 64
    assert "advisor_reply" not in json.dumps(report)


def test_build_trace_metrics_rejects_external_scenario_paths(tmp_path, monkeypatch):
    results = tmp_path / "results"
    results.mkdir()
    monkeypatch.setattr(metrics, "RESULTS", results)
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps(_scenario_report("case-a")), encoding="utf-8")
    manifest_path = results / "aggregate.json"
    manifest_path.write_text(json.dumps({
        "scenario_count": 1,
        "scenario_reports": [str(outside)],
    }), encoding="utf-8")

    with pytest.raises(ValueError, match="directly under evaluation results"):
        metrics.build_trace_metrics(manifest_path)
