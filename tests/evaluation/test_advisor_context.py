from datetime import date

from travelpilot_api.advisor.context import planning_state
from travelpilot_infra.db.models.advisor import AdvisorSessionModel


def test_planning_state_marks_trip_facts_and_user_inputs_with_sources():
    row = AdvisorSessionModel(
        trip_id=None,
        destination="成都",
        start_date=date(2026, 11, 2),
        end_date=date(2026, 11, 3),
        budget=None,
        pace_level=2,
        preference_text="轻松、美食",
        context={"planning_inputs": {"origin": "杭州", "companions": ["朋友"]}},
    )

    result = planning_state(row)

    assert result["missing_required"] == []
    assert result["slots"]["destination"] == {
        "value": "成都", "source": "session_input", "confidence": 1.0, "status": "known",
    }
    assert result["slots"]["start_date"]["value"] == "2026-11-02"
    assert result["slots"]["budget"]["status"] == "missing"
    assert result["slots"]["origin"]["value"] == "杭州"
    assert result["slots"]["companions"]["value"] == ["朋友"]


def test_planning_state_asks_only_for_missing_required_trip_facts():
    row = AdvisorSessionModel(
        trip_id=None, destination="", start_date=None, end_date=None,
        preference_text="想看海", context={},
    )

    result = planning_state(row)

    assert result["missing_required"] == ["destination", "start_date", "end_date"]
    assert [item["slot"] for item in result["unresolved_questions"]] == result["missing_required"]
