from datetime import UTC, datetime

from sqlalchemy.dialects import postgresql
from travelpilot_infra.worker_status import WORKER_HEARTBEAT_TTL_SECONDS
from travelpilot_worker import jobs
from travelpilot_worker.maintenance import cleanup_expired_auth_data


async def test_auth_cleanup_deletes_only_records_with_expired_or_stale_windows():
    now = datetime(2026, 10, 16, tzinfo=UTC)

    class Result:
        def __init__(self, rowcount):
            self.rowcount = rowcount

    class SessionStub:
        def __init__(self):
            self.statements = []
            self.committed = False

        async def execute(self, statement):
            self.statements.append(statement.compile(dialect=postgresql.dialect()))
            return Result(len(self.statements))

        async def commit(self):
            self.committed = True

    class SessionContext:
        def __init__(self, session):
            self.session = session

        async def __aenter__(self):
            return self.session

        async def __aexit__(self, *_args):
            return None

    session = SessionStub()

    class SessionFactory:
        def __call__(self):
            return SessionContext(session)

    removed = await cleanup_expired_auth_data(SessionFactory(), now=now)

    statements = [str(statement) for statement in session.statements]
    assert len(statements) == 3
    assert 'DELETE FROM user_sessions WHERE user_sessions.expires_at <=' in statements[0]
    assert "DELETE FROM email_tokens WHERE" in statements[1]
    assert "email_tokens.expires_at" in statements[1]
    assert "email_tokens.consumed_at" in statements[1]
    assert "DELETE FROM login_attempts WHERE" in statements[2]
    assert "login_attempts.window_started_at" in statements[2]
    assert "login_attempts.blocked_until" in statements[2]
    assert session.committed
    assert removed == {"sessions": 1, "email_tokens": 2, "login_attempts": 3}


async def test_worker_publishes_and_removes_its_heartbeat(monkeypatch):
    calls = []

    async def cleanup():
        return {"sessions": 0, "email_tokens": 0, "login_attempts": 0}

    class RedisStub:
        async def setex(self, key, ttl, value):
            calls.append(("setex", key, ttl, value))

        async def blpop(self, _queue, timeout):
            assert timeout == 5

        async def delete(self, key):
            calls.append(("delete", key))

    monkeypatch.setattr(jobs, "cleanup_expired_auth_data", cleanup)
    await jobs.work(RedisStub(), once=True)

    assert calls[0][0] == "setex"
    assert calls[0][2:] == (WORKER_HEARTBEAT_TTL_SECONDS, "online")
    assert calls[1] == ("delete", calls[0][1])
