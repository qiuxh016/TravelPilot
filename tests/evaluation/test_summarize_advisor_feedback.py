import json

import pytest

from tests.evaluation.summarize_advisor_feedback import summarize_feedback


def test_summarize_account_export_counts_latest_ratings_without_content():
    report = summarize_feedback({
        "data": {
            "advisor_messages": [
                {"role": "user", "content": "private user prompt"},
                {"role": "assistant", "content": "private model reply", "payload": {
                    "advisor_feedback": {"rating": "HELPFUL", "reason": None},
                }},
                {"role": "assistant", "content": "another private reply", "payload": {
                    "advisor_feedback": {"rating": "NOT_HELPFUL", "reason": "TOO_VERBOSE"},
                }},
                {"role": "assistant", "content": "not yet rated", "payload": None},
            ],
        },
    })

    assert report["assistant_reply_count"] == 3
    assert report["rated_reply_count"] == 2
    assert report["unrated_reply_count"] == 1
    assert report["helpful_count"] == 1
    assert report["not_helpful_count"] == 1
    assert report["helpful_rate_percent"] == 50.0
    assert report["not_helpful_reasons"] == {"TOO_VERBOSE": 1}
    assert "private" not in json.dumps(report)


def test_summarize_session_messages_and_empty_denominator():
    report = summarize_feedback({"messages": [{"role": "assistant", "payload": {}}]})

    assert report["assistant_reply_count"] == 1
    assert report["rated_reply_count"] == 0
    assert report["helpful_rate_percent"] is None


@pytest.mark.parametrize("feedback", [
    {"rating": "UNKNOWN"},
    {"rating": "HELPFUL", "reason": "INACCURATE"},
    {"rating": "NOT_HELPFUL", "reason": "FREE_TEXT"},
])
def test_rejects_invalid_feedback_contract(feedback):
    with pytest.raises(ValueError):
        summarize_feedback({"messages": [{
            "role": "assistant", "payload": {"advisor_feedback": feedback},
        }]})


def test_rejects_missing_message_collection():
    with pytest.raises(TypeError, match="must be an account export"):
        summarize_feedback({"trips": []})
