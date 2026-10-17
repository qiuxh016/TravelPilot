import asyncio
import json

import pytest
from pydantic import BaseModel, ValidationError

from tests.evaluation.live_skill_ablation import _safe_failure_details


@pytest.mark.asyncio
async def test_mocked_live_ablation_records_success_latency_usage_and_finish_reason(monkeypatch):
    from travelpilot_api.settings import settings
    from travelpilot_core.agents.extraction import Extraction

    from tests.evaluation import live_skill_ablation

    monkeypatch.setattr(settings, "llm_api_key", "test-key")
    monkeypatch.setattr(settings, "llm_model", "test-model")

    async def fake_extract(_text, **_kwargs):
        return Extraction(
            places=[],
            token_usage=12,
            output_tokens=4,
            finish_reason="stop",
            provider_http_status=200,
        )

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(live_skill_ablation, "extract_places", fake_extract)
    monkeypatch.setattr(asyncio, "sleep", no_sleep)

    report = await live_skill_ablation.run_live_ablation(per_call_output_cap=160)

    assert report["request_count"] == 6
    assert report["maximum_generated_tokens"] == 960
    assert report["actual_generated_tokens"] == 24
    assert report["successful_pair_count"] == 3
    assert report["successful_positive_case_pair_count"] == 2
    assert report["token_totals"]["baseline_total_tokens"] == 36
    assert report["token_totals"]["skill_total_tokens"] == 36
    assert report["token_totals"]["delta_total_tokens"] == 0
    assert all(pair["delta_total_tokens"] == 0 for pair in report["paired_comparison"])
    assert report["thinking_mode"] == "provider_default"
    assert all(row["latency_ms"] >= 0 for row in report["results"])
    assert all(row["http_status"] == 200 for row in report["results"])
    assert all(row["finish_reason"] == "stop" for row in report["results"])


@pytest.mark.asyncio
async def test_live_ablation_can_explicitly_disable_provider_thinking(monkeypatch):
    from travelpilot_api.settings import settings
    from travelpilot_core.agents.extraction import Extraction

    from tests.evaluation import live_skill_ablation

    monkeypatch.setattr(settings, "llm_api_key", "test-key")
    monkeypatch.setattr(settings, "llm_model", "test-model")
    seen_modes = []

    async def fake_extract(_text, **kwargs):
        seen_modes.append(kwargs["thinking_mode"])
        return Extraction(places=[])

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(live_skill_ablation, "extract_places", fake_extract)
    monkeypatch.setattr(asyncio, "sleep", no_sleep)

    report = await live_skill_ablation.run_live_ablation(
        per_call_output_cap=160, thinking_mode="disabled", evaluation_id="mock_v1",
    )

    assert report["evaluation_id"] == "mock_v1"
    assert report["thinking_mode"] == "disabled"
    assert seen_modes == ["disabled"] * 6


def test_validation_failure_summary_excludes_returned_input_and_error_text():
    class Payload(BaseModel):
        place_name: str

    with pytest.raises(ValidationError) as raised:
        Payload.model_validate({"private_response_fragment": "private-token-like-value"})

    wrapped = RuntimeError("TEXT_EXTRACTION_FAILED")
    wrapped.__cause__ = raised.value
    summary = _safe_failure_details(wrapped)
    serialized = json.dumps(summary)

    assert summary["cause_type"] == "ValidationError"
    assert summary["validation_issues"] == [{"type": "missing", "location": ["place_name"]}]
    assert "private-token-like-value" not in serialized
    assert "private_response_fragment" not in serialized
    assert "input_value" not in serialized


def test_safe_failure_details_includes_only_extraction_response_shape():
    from travelpilot_core.agents.extraction import TextExtractionFailure

    wrapped = TextExtractionFailure({
        "content_type": "str",
        "character_count": 23,
        "json_fence": False,
        "starts_with_object": False,
        "ends_with_object": False,
    })
    summary = _safe_failure_details(wrapped)

    assert summary["response_shape"]["character_count"] == 23
    assert "private model output" not in repr(summary)


def test_safe_failure_details_includes_only_approved_provider_metadata():
    from travelpilot_core.agents.extraction import TextExtractionFailure

    wrapped = TextExtractionFailure(
        {"character_count": 0},
        {"http_status": 200, "finish_reason": "length", "usage": {"completion_tokens": 0}},
    )
    summary = _safe_failure_details(wrapped)

    assert summary["http_status"] == 200
    assert summary["error_code"] == "MODEL_OUTPUT_TRUNCATED"
    assert summary["provider_metadata"] == {
        "http_status": 200, "finish_reason": "length", "usage": {"completion_tokens": 0},
    }
    assert "response body" not in repr(summary)
