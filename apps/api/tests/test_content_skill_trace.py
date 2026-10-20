from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from travelpilot_api.api.v1 import content as content_api
from travelpilot_api.api.v1.content import create_content, retry_content_extraction
from travelpilot_api.schemas.content import ContentCreate
from travelpilot_core.agents.extraction import Extraction, TextExtractionFailure
from travelpilot_infra.db.models.workflow import ContentSourceModel


class _Rows:
    def all(self):
        return []


class _Session:
    def __init__(self):
        self.source = None
        self.items = []

    def add(self, row):
        if isinstance(row, ContentSourceModel):
            self.source = row
        else:
            self.items.append(row)

    async def flush(self):
        self.source.id = self.source.id or uuid4()
        self.source.created_at = self.source.created_at or datetime.now(UTC)
        self.source.token_usage = self.source.token_usage or 0

    async def commit(self):
        return None

    async def get(self, model, _identity):
        return self.source if model is ContentSourceModel else None

    async def scalars(self, _statement):
        return _Rows()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "selected", "reason"),
    [
        ("  ", False, "MISSING_REQUIREMENT"),
        ("成都攻略", True, "APPLICABLE"),
    ],
)
async def test_content_source_persists_skill_selection_and_redacted_applicability(
    monkeypatch, text, selected, reason
):
    async def local_empty_extraction(*_args, **_kwargs):
        return Extraction(places=[])

    monkeypatch.setattr(content_api, "extract_places", local_empty_extraction)
    session = _Session()
    result = await create_content(
        uuid4(),
        ContentCreate(text=text),
        session=session,
        maps=object(),
        _trip=SimpleNamespace(),
    )

    assert result.skill_applicability == {
        "slug": "content-import",
        "selected": selected,
        "skill_name": "攻略导入" if selected else None,
        "skill_version": "1.1.0" if selected else None,
        "reason_code": reason,
        "missing_requirements": [] if selected else ["user_content"],
        "matched_exclusions": [],
    }
    assert session.source.skill_applicability == result.skill_applicability
    assert result.skill_name == ("攻略导入" if selected else None)
    assert text not in str(result.skill_applicability)


@pytest.mark.asyncio
async def test_text_extraction_failure_logs_only_redacted_provider_diagnostics(monkeypatch, caplog):
    async def failed_extraction(*_args, **_kwargs):
        try:
            raise ValueError("private guide text and api secret")
        except ValueError as cause:
            raise TextExtractionFailure(
                response_shape={"content_type": "str", "ends_with_object": False},
                provider_metadata={"http_status": 502, "finish_reason": "length"},
            ) from cause

    monkeypatch.setattr(content_api, "extract_places", failed_extraction)
    session = _Session()
    result = await create_content(
        uuid4(), ContentCreate(text="A private guide that should not be logged"),
        session=session, maps=object(), _trip=SimpleNamespace(),
    )

    assert result.status == "FAILED"
    assert result.error == "模型输出达到长度上限；本段未完成，可重试失败片段。"
    assert "cause_type=ValueError" in caplog.text
    assert "'http_status': 502" in caplog.text
    assert "'finish_reason': 'length'" in caplog.text
    assert "private guide" not in caplog.text
    assert "api secret" not in caplog.text


@pytest.mark.asyncio
async def test_content_import_uses_configured_per_turn_output_budget(monkeypatch):
    captured = {}
    monkeypatch.setattr(content_api.settings, "llm_base_url", "https://api.deepseek.com/v1")

    async def local_empty_extraction(*_args, **kwargs):
        captured.update(kwargs)
        return Extraction(places=[])

    monkeypatch.setattr(content_api, "extract_places", local_empty_extraction)
    session = _Session()
    await create_content(
        uuid4(), ContentCreate(text="梅州一日游攻略"), session=session,
        maps=object(), _trip=SimpleNamespace(),
    )

    assert captured["token_budget"] == content_api.settings.llm_max_tokens_per_turn
    assert captured["thinking_mode"] == "disabled"
    assert captured["guide_import"] is True


@pytest.mark.asyncio
async def test_retry_content_extraction_retries_only_failed_range(monkeypatch):
    trip_id = uuid4()
    source = ContentSourceModel(
        id=uuid4(), trip_id=trip_id, content_type="TEXT", text="A" * 1000 + "FAILED RANGE",
        status="PARTIAL", created_at=datetime.now(UTC), token_usage=20,
        guide_facts=[{"id": str(uuid4()), "kind": "TIP", "title": "保存内容", "content": "已有结果", "confidence": 0.8}],
        extraction_metadata={
            "total_chunks": 2, "completed_chunks": 1, "complete": False,
            "failed_chunks": [{"index": 1, "start": 1000, "end": 1012,
                               "failure_code": "MODEL_RESPONSE_INVALID", "finish_reason": "length"}],
        },
    )
    requested = []

    async def extract(text, **_kwargs):
        requested.append(text)
        return Extraction(places=[], guide_facts=[])

    monkeypatch.setattr(content_api, "extract_places", extract)

    class Session(_Session):
        async def get(self, model, _identity):
            return source if model is ContentSourceModel else None

    result = await retry_content_extraction(
        source.id, session=Session(), maps=object(), source=source,
    )

    assert requested == ["FAILED RANGE"]
    assert result.status == "NEEDS_CONFIRMATION"
    assert result.extraction_metadata == {
        "total_chunks": 2, "completed_chunks": 2, "complete": True, "failed_chunks": [],
    }
