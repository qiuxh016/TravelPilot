from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from travelpilot_api.api.v1 import content as content_api
from travelpilot_api.api.v1.content import create_content
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
    assert result.error == "模型输出达到长度上限，攻略尚未解析完成。请缩短攻略内容后重试。"
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
