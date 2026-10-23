import json

import pytest

from tests.evaluation import advisor_matched_schedule_v8_live_eval as live


def test_v8_arm_allocations_fit_the_authorized_limits():
    assert sum(live.ARM_REQUEST_CAPS.values()) == 48
    assert sum(live.ARM_OUTPUT_BUDGETS.values()) == 12_288
    assert live.PROVIDER_REQUEST_OUTPUT_CAP <= 512
    assert live.ARM_REQUEST_CAPS == {
        "B1_FIXED_WORKFLOW": 24,
        "B2_AGENT_LOOP": 12,
        "B3_VERIFIER_RECOVERY": 12,
    }


def test_v8_preflight_rejects_unfrozen_or_overwrite_dataset(tmp_path):
    frozen = json.loads(live.DATASET.read_text(encoding="utf-8"))
    live._validate_frozen_inputs(frozen, tmp_path / "new.json")

    with pytest.raises(RuntimeError, match="dataset contract"):
        live._validate_frozen_inputs({"status": "DRAFT", "cases": []}, tmp_path / "draft.json")

    existing = tmp_path / "existing.json"
    existing.write_text("{}", encoding="utf-8")
    with pytest.raises(FileExistsError, match="overwrite"):
        live._validate_frozen_inputs(frozen, existing)
