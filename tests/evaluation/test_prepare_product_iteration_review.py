import json

import pytest
from travelpilot_api.build_info import fingerprint_manifest

from tests.evaluation import prepare_product_iteration_review as evaluation


def _report(*, second_turn_reply="回复乙", app_revision="product-revision-a"):
    runtime_sources = {
        "apps/api/src/travelpilot_api/advisor/turn_service.py": app_revision,
    }
    return {
        "evaluation_id": "advisor_product_live_api_two_turn_v1",
        "provider_mode": "LIVE_MODEL_REAL_ADVISOR_API_FAKE_MAPS",
        "model": "deepseek-flash",
        "base_url_origin": "https://api.deepseek.com",
        "server_output_cap_per_turn": 512,
        "server_session_output_cap": 2048,
        "runner_process_output_cap_per_turn": 512,
        "runner_process_session_output_cap": 2048,
        "temporary_account_deleted": True,
        "failure": None,
        "resources": {"formal_write_prevented": True},
        "api_runtime_source_sha256": runtime_sources,
        "api_runtime_fingerprint": fingerprint_manifest(runtime_sources),
        "source_provenance": {"source_sha256": {
            "tests/evaluation/advisor_product_live_eval.py": "frozen-evaluator-hash",
            "apps/api/src/travelpilot_api/advisor/turn_service.py": app_revision,
        }},
        "turns": [
            {"turn": 1, "http_status": 200, "assistant_reply_present": True,
             "assistant_reply_excerpt": "回复甲", "assistant_reply": "回复甲",
             "assistant_reply_complete": True, "user_message": evaluation.TURN_INPUTS[0],
             "trip_context": {"destination": "成都", "dates": "2027-01-27/28"}},
            {"turn": 2, "http_status": 200, "assistant_reply_present": True,
             "assistant_reply_excerpt": second_turn_reply, "assistant_reply": second_turn_reply,
             "assistant_reply_complete": True, "user_message": evaluation.TURN_INPUTS[1],
             "trip_context": {"destination": "成都", "dates": "2027-01-27/28"}},
        ],
    }


def test_prepare_review_pairs_two_complete_iterations_without_exposing_arm_labels(tmp_path):
    first_path, second_path = tmp_path / "first.json", tmp_path / "second.json"
    first_path.write_text(json.dumps(_report()), encoding="utf-8")
    second_path.write_text(json.dumps(_report(
        second_turn_reply="回复丙", app_revision="product-revision-b",
    )), encoding="utf-8")

    sheet, key = evaluation.prepare_review(first_path, second_path)

    assert len(sheet["responses"]) == 2
    assert len(key["mapping"]) == 2
    assert all("arm" not in row for row in sheet["responses"])
    assert {row["arm"] for row in key["mapping"]} == set(evaluation.ARMS)
    assert key["changed_application_sources"] == [
        "apps/api/src/travelpilot_api/advisor/turn_service.py",
    ]
    assert set(key["application_source_sha256_by_arm"]) == set(evaluation.ARMS)
    rows = sheet["responses"]
    assert rows[0]["user_goal"] == rows[1]["user_goal"]
    assert rows[0]["trip_context"] == rows[1]["trip_context"]
    contexts = json.loads(rows[0]["trip_context"])
    assert [item["turn"] for item in contexts] == [1, 2]
    assert "第1轮用户" in rows[0]["user_goal"]
    assert "第2轮顾问" in rows[0]["response"]


def test_prepare_scenario_review_blinds_matched_live_turns(tmp_path):
    scenario_id = "schedule-route-weather"
    prompts = evaluation.SCENARIOS[scenario_id]["turn_inputs"]

    def report(revision, api_origin):
        runtime_sources = {"apps/api/src/travelpilot_api/advisor/turn_service.py": revision}
        return {
            "evaluation_id": f"advisor_product_api_{scenario_id}_v1",
            "scenario_id": scenario_id,
            "provider_mode": "LIVE_MODEL_REAL_ADVISOR_API_FAKE_MAPS",
            "model": "deepseek-flash",
            "base_url_origin": api_origin,
            "server_output_cap_per_turn": 512,
            "server_session_output_cap": 1024,
            "runner_process_output_cap_per_turn": 512,
            "runner_process_session_output_cap": 1024,
            "temporary_account_deleted": True,
            "failure": None,
            "resources": {"formal_write_prevented": True},
            "api_runtime_source_sha256": runtime_sources,
            "api_runtime_fingerprint": fingerprint_manifest(runtime_sources),
            "turns": [{
                "turn": index,
                "http_status": 200,
                "assistant_reply_present": True,
                "assistant_reply_complete": True,
                "assistant_reply": f"回复 {revision} 第{index}轮",
                "user_message": prompt,
                "trip_context": {"destination": "成都", "dates": "2027-01-27/28"},
            } for index, prompt in enumerate(prompts, 1)],
        }

    first_path, second_path = tmp_path / "a.json", tmp_path / "b.json"
    first_path.write_text(json.dumps(report("before", "http://127.0.0.1:10001")), encoding="utf-8")
    second_path.write_text(json.dumps(report("after", "http://127.0.0.1:10002")), encoding="utf-8")

    sheet, key = evaluation.prepare_scenario_review(first_path, second_path, scenario_id)

    assert len(sheet["responses"]) == 4
    assert len(key["mapping"]) == 4
    assert all("arm" not in row for row in sheet["responses"])
    assert {row["arm"] for row in key["mapping"]} == set(evaluation.ARMS)
    assert {row["turn"] for row in key["mapping"]} == {1, 2}
    assert key["scenario_id"] == scenario_id
    assert len(set(key["api_runtime_fingerprint_by_arm"].values())) == 2
    row_by_turn_and_reply = {
        (row["turn"], row["response"]): row for row in sheet["responses"]
    }
    first_turn = row_by_turn_and_reply[(1, "第1轮顾问：回复 before 第1轮")]
    second_turn = row_by_turn_and_reply[(2, "第1轮顾问：回复 before 第1轮\n\n第2轮顾问：回复 before 第2轮")]
    assert "第2轮用户" not in first_turn["user_goal"]
    assert "第2轮用户" in second_turn["user_goal"]


def test_prepare_review_rejects_missing_or_truncated_responses(tmp_path):
    first_path, second_path = tmp_path / "first.json", tmp_path / "second.json"
    first_path.write_text(json.dumps(_report()), encoding="utf-8")
    truncated = _report(second_turn_reply="x" * 1600)
    truncated["turns"][1]["assistant_reply_complete"] = False
    second_path.write_text(json.dumps(truncated), encoding="utf-8")

    with pytest.raises(ValueError, match="possibly truncated"):
        evaluation.prepare_review(first_path, second_path)


@pytest.mark.parametrize("change, message", [
    ("source", "input definitions changed"),
    ("runtime", "different model/runtime settings"),
    ("same_app", "no application-code difference"),
    ("prompt", "different user messages"),
    ("context", "different trip contexts"),
    ("old", "failed, missing, or possibly truncated"),
])
def test_prepare_review_rejects_unmatched_or_legacy_runs(tmp_path, change, message):
    first = _report()
    second = _report(app_revision="product-revision-b")
    if change == "source":
        second["source_provenance"]["source_sha256"][
            "tests/evaluation/advisor_product_live_eval.py"
        ] = "different-hash"
    elif change == "runtime":
        second["server_session_output_cap"] += 1
    elif change == "same_app":
        second["api_runtime_source_sha256"] = first["api_runtime_source_sha256"].copy()
        second["api_runtime_fingerprint"] = fingerprint_manifest(
            second["api_runtime_source_sha256"]
        )
    elif change == "prompt":
        second["turns"][0]["user_message"] = "different prompt"
    elif change == "context":
        second["turns"][1]["trip_context"]["dates"] = "different dates"
    else:
        for report in (first, second):
            for turn in report["turns"]:
                turn.pop("assistant_reply")
                turn.pop("assistant_reply_complete")
                turn.pop("user_message")
                turn.pop("trip_context")

    first_path, second_path = tmp_path / "first.json", tmp_path / "second.json"
    first_path.write_text(json.dumps(first), encoding="utf-8")
    second_path.write_text(json.dumps(second), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        evaluation.prepare_review(first_path, second_path)


def test_review_cli_refuses_to_overwrite_sheet_or_private_key(monkeypatch, tmp_path):
    existing = tmp_path / "existing.json"
    existing.write_text("{}", encoding="utf-8")
    key_path = tmp_path / "key.json"
    monkeypatch.setattr(
        "sys.argv", ["prepare_product_iteration_review", str(existing), str(key_path)],
    )

    with pytest.raises(SystemExit) as error:
        evaluation.main()

    assert error.value.code == 2
    assert not key_path.exists()
