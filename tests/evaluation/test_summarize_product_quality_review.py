from copy import deepcopy

import pytest

from tests.evaluation.summarize_product_quality_review import summarize_review


def _sheet():
    return {
        "review_protocol": "advisor_single_system_quality_review_v1",
        "responses": [
            {
                "review_id": "r1",
                "conversation": [
                    {"user_message": "请安排两日游", "advisor_reply": "这是待确认草案"},
                    {"user_message": "第二天上午留空", "advisor_reply": "已调整"},
                ],
                "scores": {
                    "factual_grounding": 4,
                    "constraint_safety": 5,
                    "task_progress": 3,
                    "clarification_quality": 4,
                    "transparency": 4,
                    "reviewer_notes": "第二轮可以摘要具体改动",
                },
            },
        ],
    }


def test_summarize_completed_single_system_quality_review():
    summary = summarize_review(_sheet())

    assert summary["reviewed_conversations"] == 1
    assert summary["reviewed_turns"] == 2
    assert summary["mean_score_by_dimension"]["constraint_safety"] == 5
    assert summary["overall_mean"] == 4
    assert "no comparison" in summary["assessment_type"]


def test_summarizer_rejects_unfilled_or_out_of_range_ratings():
    for bad_score in (None, 0, 6, True, 3.5):
        sheet = deepcopy(_sheet())
        sheet["responses"][0]["scores"]["task_progress"] = bad_score
        with pytest.raises(ValueError):
            summarize_review(sheet)


def test_summarizer_rejects_duplicate_ids_and_incomplete_turns():
    sheet = _sheet()
    sheet["responses"].append(deepcopy(sheet["responses"][0]))
    with pytest.raises(ValueError, match="unique"):
        summarize_review(sheet)

    sheet = _sheet()
    sheet["responses"][0]["conversation"][0]["advisor_reply"] = " "
    with pytest.raises(ValueError, match="incomplete"):
        summarize_review(sheet)
