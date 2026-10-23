import pytest

from tests.evaluation.blind_review import build_blind_review, summarize_blind_review


def _paired_records():
    return [
        {
            "case_id": "case-1", "turn": 1, "arm": arm,
            "user_goal": "帮我规划两日游",
            "trip_context": "成都，已有两个收藏地点，日期已确认",
            "response": "根据现有约束整理后的建议。",
            "expected_action": "hidden from reviewer",
            "tool_trace": [{"tool": "hidden from reviewer"}],
        }
        for arm in ("B1_FIXED", "B2_AGENT_LOOP", "B3_VERIFIER_RECOVERY")
    ]


def test_blind_sheet_excludes_arm_labels_and_scoring_leakage():
    counter = iter(f"review-{index}" for index in range(3))
    sheet, key = build_blind_review(_paired_records(), id_factory=lambda: next(counter))

    assert len(sheet["responses"]) == 3
    assert all(set(row) == {
        "review_id", "case_id", "turn", "user_goal", "trip_context", "response", "scores",
    } for row in sheet["responses"])
    assert not any("B1_FIXED" in str(row) or "B2_AGENT_LOOP" in str(row)
                   or "B3_VERIFIER_RECOVERY" in str(row) for row in sheet["responses"])
    assert all(row["scores"]["factual_grounding"] is None for row in sheet["responses"])
    assert set(sheet["rubric"]["dimensions"]) == {
        "factual_grounding", "constraint_safety", "task_progress",
        "clarification_quality", "transparency",
    }
    assert set(sheet["rubric"]["scale"]) == {"1", "3", "5"}
    assert "Do not infer" in sheet["rubric"]["reviewer_instructions"]
    assert {entry["arm"] for entry in key["mapping"]} == {
        "B1_FIXED", "B2_AGENT_LOOP", "B3_VERIFIER_RECOVERY",
    }


def test_blind_review_rejects_unpaired_responses():
    with pytest.raises(ValueError, match="incomplete"):
        build_blind_review(_paired_records()[:2])


@pytest.mark.parametrize("field", ["user_goal", "trip_context"])
def test_blind_review_rejects_arms_with_different_task_inputs(field):
    records = _paired_records()
    records[1][field] = "不同实验输入"

    with pytest.raises(ValueError, match="same user goal and trip context"):
        build_blind_review(records)


def test_blind_review_rejects_duplicate_arm_and_oversized_response():
    records = _paired_records()
    records[1]["arm"] = records[0]["arm"]
    with pytest.raises(ValueError, match="duplicate"):
        build_blind_review(records)

    records = _paired_records()
    records[0]["response"] = "x" * 1601
    with pytest.raises(ValueError, match="response"):
        build_blind_review(records)


def test_blind_review_rejects_unknown_or_non_string_arm():
    records = _paired_records()
    records[0]["arm"] = ["unexpected"]
    with pytest.raises(ValueError, match="known arm"):
        build_blind_review(records)


def test_completed_blind_review_returns_paired_means_without_claiming_significance():
    counter = iter(f"review-{index}" for index in range(3))
    sheet, key = build_blind_review(_paired_records(), id_factory=lambda: next(counter))
    for row in sheet["responses"]:
        row["scores"] = {
            "factual_grounding": 5 if key["mapping"][sheet["responses"].index(row)]["arm"] == "B3_VERIFIER_RECOVERY" else 3,
            "constraint_safety": 4,
            "task_progress": 3,
            "clarification_quality": 4,
            "transparency": 5,
            "reviewer_notes": "",
        }

    summary = summarize_blind_review(sheet, key)
    assert summary["paired_case_turns"] == 1
    assert summary["per_arm_mean"]["B3_VERIFIER_RECOVERY"]["factual_grounding"] == 5
    assert summary["interpretation"].startswith("Descriptive reviewer scores")


def test_blind_review_summary_rejects_incomplete_scores_and_mismatched_key():
    sheet, key = build_blind_review(_paired_records())
    with pytest.raises((ValueError, TypeError), match="score"):
        summarize_blind_review(sheet, key)

    sheet["responses"][0]["review_id"] = "unknown"
    with pytest.raises(ValueError, match="IDs do not match"):
        summarize_blind_review(sheet, key)
