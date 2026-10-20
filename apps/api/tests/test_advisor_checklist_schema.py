import pytest
from pydantic import ValidationError
from travelpilot_api.schemas.advisor import AdvisorChecklistDecision


@pytest.mark.parametrize(
    "item_key",
    [
        "MEAL_MISSING:2026-10-18:LUNCH",
        "OPEN_GAP:2026-10-18:GAP:1030-1400",
        "LODGING_MISSING:2026-10-18",
    ],
)
def test_checklist_decision_accepts_supported_stable_keys(item_key):
    result = AdvisorChecklistDecision(item_key=item_key, decision="LATER", draft_version=2)

    assert result.item_key == item_key


def test_checklist_decision_rejects_arbitrary_or_oversized_keys():
    for item_key in ("meal:2026-10-18", "OPEN_GAP:2026-10-18:GAP:" + "x" * 100):
        with pytest.raises(ValidationError):
            AdvisorChecklistDecision(item_key=item_key, decision="NOT_NEEDED", draft_version=1)
