import json
from pathlib import Path

from tests.evaluation.advisor_memory_transfer_eval import evaluate_cases


def test_confirmed_memory_transfer_contract_cases_pass():
    path = Path(__file__).parent / "cases" / "advisor_memory_transfer_v1.json"
    cases = json.loads(path.read_text(encoding="utf-8"))["cases"]

    report = evaluate_cases(cases)

    assert report["case_count"] == 12
    assert report["passed"] == 12
    assert report["failed"] == 0
    assert "not a user-utility" in report["claim_boundary"]
