import json

import pytest

from tests.evaluation import prepare_corrected_product_review as packet


def test_packet_uses_only_complete_successful_anonymized_turns(tmp_path, monkeypatch):
    monkeypatch.setattr(packet, "RESULTS", tmp_path)
    monkeypatch.setattr(packet, "SCENARIOS", ("case-a",))
    report = {
        "provider_mode": "LIVE_MODEL_REAL_ADVISOR_API_FAKE_MAPS_BOUNDED_PROXY",
        "scenario_id": "case-a",
        "run_at_utc": "2026-10-20T00:08:30+00:00",
        "model": "must-not-leak",
        "maps_data_mode": "FAKE_MAPS",
        "weather_data_mode": "SYNTHETIC",
        "failure": None,
        "product_assessment": {"passed": True},
        "temporary_account_deleted": True,
        "resources": {"formal_write_prevented": True},
        "turns": [{
            "http_status": 200,
            "assistant_reply_complete": True,
            "user_message": "帮我安排旅行",
            "trip_context": {"destination": "成都"},
            "assistant_reply": "这是待确认草案。",
            "verified_candidates": [],
            "weather_evidence": {},
            "route_evidence": {},
            "schedule_assignments": [],
            "schedule_breaks": [],
            "agent_action": "must-not-leak",
        }],
    }
    (tmp_path / "run_case-a.json").write_text(
        json.dumps(report, ensure_ascii=False), encoding="utf-8",
    )

    result = packet.build_review_packet("run")

    assert result["sample"]["conversation_count"] == 1
    assert result["sample"]["turn_count"] == 1
    assert result["sample"]["source"] == "bounded live Advisor product run (UTC 2026-10-20)"
    assert "deepseek" not in str(result).lower()
    assert "2 or 4 only" in result["rubric"]["scale"]
    assert set(result["rubric"]["anchors"]) == set(packet.SCORE_FIELDS)
    assert all(set(anchors) == {"1", "3", "5"} for anchors in result["rubric"]["anchors"].values())
    row = result["responses"][0]
    assert row["scores"]["factual_grounding"] is None
    assert "model" not in str(row).lower()
    assert "agent_action" not in str(row)
    assert "must-not-leak" not in str(row)
    assert row["conversation"][0]["available_context_and_evidence"]["evidence_mode"]["maps"] == "FAKE_MAPS"


def test_packet_rejects_failed_or_unclean_scenario(tmp_path, monkeypatch):
    monkeypatch.setattr(packet, "RESULTS", tmp_path)
    monkeypatch.setattr(packet, "SCENARIOS", ("case-a",))
    (tmp_path / "run_case-a.json").write_text(json.dumps({"failure": "timeout"}), encoding="utf-8")

    with pytest.raises(ValueError, match="completed, clean"):
        packet.build_review_packet("run")


def test_manifest_builds_packet_from_mixed_run_prefixes(tmp_path, monkeypatch):
    monkeypatch.setattr(packet, "RESULTS", tmp_path)
    monkeypatch.setattr(packet, "SCENARIOS", ("case-a", "case-b"))
    base_report = {
        "provider_mode": "LIVE_MODEL_REAL_ADVISOR_API_FAKE_MAPS",
        "run_at_utc": "2026/10/20 0:08:30",
        "maps_data_mode": "FAKE_MAPS",
        "weather_data_mode": "SYNTHETIC",
        "failure": None,
        "product_assessment": {"passed": True},
        "temporary_account_deleted": True,
        "resources": {"formal_write_prevented": True},
        "turns": [{
            "http_status": 200,
            "assistant_reply_complete": True,
            "user_message": "帮我安排旅行",
            "trip_context": {"destination": "成都"},
            "assistant_reply": "这是待确认草案。",
        }],
    }
    report_a = {**base_report, "scenario_id": "case-a"}
    report_b = {**base_report, "scenario_id": "case-b"}
    path_a = tmp_path / "run-one_case-a.json"
    path_b = tmp_path / "run-two_case-b.json"
    path_a.write_text(json.dumps(report_a), encoding="utf-8")
    path_b.write_text(json.dumps(report_b), encoding="utf-8")
    manifest_path = tmp_path / "aggregate.json"
    manifest_path.write_text(json.dumps({
        "scenario_reports": [str(path_a), str(path_b)],
    }), encoding="utf-8")

    scenario_paths = packet._scenario_paths_from_manifest(manifest_path)
    result = packet.build_review_packet(scenario_paths=scenario_paths)

    assert result["sample"]["conversation_count"] == 2
    assert result["sample"]["turn_count"] == 2
    assert all(row["scores"]["factual_grounding"] is None for row in result["responses"])


def test_manifest_rejects_duplicate_or_external_scenario_reports(tmp_path, monkeypatch):
    monkeypatch.setattr(packet, "RESULTS", tmp_path / "results")
    packet.RESULTS.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    manifest_path = packet.RESULTS / "aggregate.json"
    manifest_path.write_text(json.dumps({"scenario_reports": [str(outside)]}), encoding="utf-8")

    monkeypatch.setattr(packet, "SCENARIOS", ("case-a",))
    with pytest.raises(ValueError, match="directly under evaluation results"):
        packet._scenario_paths_from_manifest(manifest_path)


def test_review_packet_rejects_scenarios_from_different_utc_dates(tmp_path):
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    first.write_text(json.dumps({"run_at_utc": "2026-10-19T23:59:00+00:00"}), encoding="utf-8")
    second.write_text(json.dumps({"run_at_utc": "2026-10-20T00:01:00+00:00"}), encoding="utf-8")

    with pytest.raises(ValueError, match="one evaluation run date"):
        packet._source_run_date({"case-a": first, "case-b": second})

