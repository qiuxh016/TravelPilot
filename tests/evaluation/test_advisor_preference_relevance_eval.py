import json
from pathlib import Path

from travelpilot_api.advisor.interpretation import _destination_queries

from tests.evaluation.advisor_preference_relevance_eval import evaluate_cases


def test_current_intent_alignment_with_and_without_confirmed_memory():
    path = Path(__file__).parent / "cases" / "advisor_preference_relevance_v1.json"
    dataset = json.loads(path.read_text(encoding="utf-8"))
    report = evaluate_cases(dataset["cases"], dataset_id=dataset["dataset_id"])

    assert report["case_count"] == 3
    assert report["memory_alignment_passed"] == 3
    assert report["baseline_alignment_passed"] == 3
    assert report["explicit_scope_violations"] == 0


def test_expanded_preference_transfer_v2_contract_set():
    path = Path(__file__).parent / "cases" / "advisor_preference_relevance_v2.json"
    dataset = json.loads(path.read_text(encoding="utf-8"))
    report = evaluate_cases(dataset["cases"], dataset_id=dataset["dataset_id"])

    assert report["case_count"] == 12
    assert report["memory_alignment_passed"] == 12
    assert report["baseline_alignment_passed"] == 12
    assert report["explicit_scope_violations"] == 0


def test_destination_query_budget_preserves_user_interest_mention_order():
    queries = _destination_queries("想去海边拍照看风景", [])

    assert [label for label, _query in queries] == ["海滨休闲", "摄影与观景"]
