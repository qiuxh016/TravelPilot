from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from travelpilot_api.api.v1 import advisor as advisor_api


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class _Database:
    def __init__(self, rows):
        self.rows = rows

    async def scalars(self, statement):
        params = statement.compile().params
        assert params["owner_id_1"] == self.rows[0].owner_id
        assert params["session_id_1"] == self.rows[0].session_id
        assert statement._limit_clause.value == 250
        return _Rows(self.rows)


@pytest.mark.asyncio
async def test_diagnostics_endpoint_returns_only_owner_scoped_redacted_events(monkeypatch):
    owner_id = uuid4()
    session_id = uuid4()
    event = SimpleNamespace(
        owner_id=owner_id,
        session_id=session_id,
        phase="tool",
        operation="search_places",
        status="SUCCEEDED",
        duration_ms=12,
        token_usage=0,
        error_code=None,
        created_at=datetime.now(UTC),
        metadata_json={"query": "private query", "transitions": [{
            "tool": "search_places", "status": "VERIFIED", "latency_ms": 12,
            "input_data": {"keyword": "private query"},
        }]},
    )
    row = SimpleNamespace(id=session_id)

    async def owned_session(_db, requested_id, requested_owner):
        assert requested_id == session_id
        assert requested_owner == owner_id
        return row

    monkeypatch.setattr(advisor_api, "owned_session", owned_session)
    result = await advisor_api.export_advisor_diagnostics(
        session_id, _Database([event]), SimpleNamespace(id=owner_id),
    )
    assert result["format"] == "travelpilot-advisor-diagnostics-v1"
    assert len(result["events"]) == 1
    assert "private query" not in str(result)
    assert result["events"][0]["metadata"]["transitions"] == [{
        "tool": "search_places", "status": "VERIFIED", "latency_ms": 12,
    }]
