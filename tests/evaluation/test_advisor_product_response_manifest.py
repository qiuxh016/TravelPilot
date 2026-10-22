import hashlib
import json
from pathlib import Path

from scripts.run_advisor_product_live_suite import SCENARIO_ORDER, _proxy_budget_url
from tests.evaluation.advisor_product_live_eval import SCENARIOS

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "tests/evaluation/cases/advisor_product_response_v1.json"


def test_frozen_response_manifest_matches_current_suite_prompts():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert manifest["evaluation_id"] == "advisor_product_response_v1"
    assert manifest["status"] == "FROZEN_FOR_REVIEW_PROTOCOL_ONLY"
    assert [case["scenario_id"] for case in manifest["cases"]] == list(SCENARIO_ORDER)
    assert manifest["case_count"] == len(SCENARIO_ORDER) == 16

    actual_turn_count = 0
    for case in manifest["cases"]:
        turns = SCENARIOS[case["scenario_id"]]["turn_inputs"]
        expected_hashes = [hashlib.sha256(turn.encode("utf-8")).hexdigest() for turn in turns]
        assert case["turn_count"] == len(turns)
        assert case["user_turn_sha256"] == expected_hashes
        actual_turn_count += len(turns)

    assert manifest["user_turn_count"] == actual_turn_count == 20


def test_bounded_proxy_budget_url_accepts_only_local_plain_http_origin():
    assert _proxy_budget_url("http://127.0.0.1:8022/v1") == "http://127.0.0.1:8022/budget"
    for url in (
        "https://127.0.0.1:8022/v1",
        "http://example.com:8022/v1",
        "http://user:pass@127.0.0.1:8022/v1",
        "http://127.0.0.1:8022/v1?next=example",
    ):
        try:
            _proxy_budget_url(url)
        except RuntimeError:
            continue
        raise AssertionError(f"unsafe proxy base URL was accepted: {url}")
