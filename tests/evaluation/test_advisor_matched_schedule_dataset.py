import json
import math
import unicodedata
from pathlib import Path

from tests.evaluation.advisor_product_live_eval import SCENARIOS

DATASET = Path(__file__).parent / "cases" / "advisor_matched_schedule_b1_v1.json"
CASES_DIR = DATASET.parent


def _normalize(value: str) -> str:
    return "".join(character for character in unicodedata.normalize("NFKC", value).casefold() if character.isalnum())


def _collect_user_prompts(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"user", "user_message", "prompt"} and isinstance(item, str):
                yield item
            yield from _collect_user_prompts(item)
    elif isinstance(value, list):
        for item in value:
            yield from _collect_user_prompts(item)


def test_matched_baseline_dataset_has_four_two_turn_supported_schedule_tasks():
    dataset = json.loads(DATASET.read_text(encoding="utf-8"))

    assert dataset["dataset_id"] == "advisor_matched_schedule_b1_v1"
    assert dataset["status"] == "FROZEN_FOR_PREAUTHORIZATION"
    assert dataset["provider_mode"] == "synthetic_product_integration"
    assert dataset["arms"]["B1"] == "0c4a8998896bc5b27877ff9f49cfbf77ec0128d7"
    assert len(dataset["cases"]) == dataset["case_count"] == 4
    assert sum(len(case["turns"]) for case in dataset["cases"]) == dataset["turn_count"] == 8

    seen_ids = set()
    seen_prompts = set()
    for case in dataset["cases"]:
        assert case["id"] not in seen_ids
        seen_ids.add(case["id"])
        assert case["state"]["trip_dates"]
        assert len(case["state"]["saved_places"]) >= 1
        assert len(case["turns"]) == 2
        assert case["acceptance"]
        assert case["state"]["hard_constraints"]
        for place in case["state"]["saved_places"]:
            assert math.isfinite(place["latitude"])
            assert math.isfinite(place["longitude"])
        for prompt in case["turns"]:
            normalized = _normalize(prompt)
            assert normalized and normalized not in seen_prompts
            seen_prompts.add(normalized)


def test_matched_baseline_prompts_do_not_exactly_repeat_existing_case_prompts():
    dataset = json.loads(DATASET.read_text(encoding="utf-8"))
    new_prompts = {
        _normalize(prompt)
        for case in dataset["cases"]
        for prompt in case["turns"]
    }

    historical_prompts = set()
    for path in CASES_DIR.glob("*.json"):
        if path == DATASET:
            continue
        try:
            prior = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        historical_prompts.update(
            _normalize(prompt) for prompt in _collect_user_prompts(prior)
        )
    for scenario in SCENARIOS.values():
        historical_prompts.update(
            _normalize(prompt) for prompt in scenario.get("turn_inputs", ())
        )

    assert not new_prompts & historical_prompts


def test_matched_baseline_budget_is_bounded_and_forbids_formal_writes():
    dataset = json.loads(DATASET.read_text(encoding="utf-8"))
    limits = dataset["run_limits"]

    assert limits["api_turns_max"] == 24
    assert limits["provider_requests_max"] == 48
    assert limits["completion_tokens_per_api_turn_max"] == 512
    assert limits["completion_tokens_total_max"] == 12288
    assert limits["formal_itinerary_writes"] == 0
    assert limits["maps_weather_mode"] == "local synthetic fixtures only"
