from tests.evaluation.skill_applicability_eval import run_evaluation


def test_skill_applicability_scenarios_record_selection_versions_and_reasons():
    report = run_evaluation()

    assert report["scenario_count"] == 4
    assert report["all_expectations_match"] is True
    rejected = [row for row in report["results"] if not row["selected"]]
    assert {row["reason_code"] for row in rejected} == {
        "MISSING_REQUIREMENT", "EXCLUDED_CONTEXT"
    }
    assert all(row["skill_version"] is None for row in rejected)
