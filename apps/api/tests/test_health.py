from uuid import UUID

from fastapi.testclient import TestClient
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError
from travelpilot_api.main import (
    _expected_migration_heads,
    app,
    get_readiness_redis,
)
from travelpilot_infra.db.session import get_session


def test_health() -> None:
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_request_logs_use_route_templates_and_validated_request_ids(caplog):
    caplog.set_level("INFO", logger="travelpilot.api")
    email = "person@example.invalid"

    response = TestClient(app).get(
        f"/api/v1/auth/dev-mailbox/{email}",
        headers={"X-Request-ID": "untrusted@example.invalid"},
    )

    assert response.status_code == 404
    UUID(response.headers["x-request-id"])
    assert email not in caplog.text
    assert "untrusted@example.invalid" not in caplog.text
    assert "route=/auth/dev-mailbox/{email}" in caplog.text


def test_security_status_logs_are_warning_events_without_dynamic_path_values(caplog):
    from fastapi import HTTPException

    async def limited(secret: str):
        del secret
        raise HTTPException(status_code=429, detail="limited")

    async def failed(secret: str):
        del secret
        raise HTTPException(status_code=503, detail="unavailable")

    app.add_api_route("/test-security-event/limited/{secret}", limited, methods=["GET"])
    app.add_api_route("/test-security-event/failed/{secret}", failed, methods=["GET"])
    caplog.set_level("WARNING", logger="travelpilot.api")
    client = TestClient(app)

    limited_secret = "private-email@example.invalid"
    failed_secret = "private-token-value"
    assert client.get(f"/test-security-event/limited/{limited_secret}").status_code == 429
    assert client.get(f"/test-security-event/failed/{failed_secret}").status_code == 503

    assert "security_event=rate_limited" in caplog.text
    assert "security_event=server_error" in caplog.text
    assert "route=/test-security-event/limited/{secret}" in caplog.text
    assert "route=/test-security-event/failed/{secret}" in caplog.text
    assert limited_secret not in caplog.text
    assert failed_secret not in caplog.text


def test_readiness_reports_database_and_queue_health() -> None:
    class SessionStub:
        async def execute(self, _statement):
            class MigrationResult:
                def scalars(self):
                    return self

                def all(self):
                    return list(_expected_migration_heads())

            if "alembic_version" in str(_statement):
                return MigrationResult()
            return 1

    class RedisStub:
        async def ping(self):
            return True

    async def session_dependency():
        yield SessionStub()

    async def redis_dependency():
        return RedisStub()

    app.dependency_overrides[get_session] = session_dependency
    app.dependency_overrides[get_readiness_redis] = redis_dependency
    try:
        response = TestClient(app).get("/ready")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["status"] == "ready"
    assert response.json()["checks"] == {
        "database": "ok",
        "migrations": "ok",
        "queue": "ok",
    }


def test_readiness_returns_503_without_leaking_dependency_errors() -> None:
    class SessionStub:
        async def execute(self, _statement):
            raise SQLAlchemyError("database connection details")

    class RedisStub:
        async def ping(self):
            raise RedisError("redis connection details")

    async def session_dependency():
        yield SessionStub()

    async def redis_dependency():
        return RedisStub()

    app.dependency_overrides[get_session] = session_dependency
    app.dependency_overrides[get_readiness_redis] = redis_dependency
    try:
        response = TestClient(app).get("/ready")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"
    assert response.json()["checks"] == {
        "database": "unavailable",
        "migrations": "unavailable",
        "queue": "unavailable",
    }
    assert "connection details" not in response.text


def test_readiness_blocks_when_schema_is_not_at_the_expected_revision() -> None:
    class MigrationResult:
        def scalars(self):
            return self

        def all(self):
            return ["old-revision"]

    class SessionStub:
        async def execute(self, statement):
            return MigrationResult() if "alembic_version" in str(statement) else 1

    class RedisStub:
        async def ping(self):
            return True

    async def session_dependency():
        yield SessionStub()

    async def redis_dependency():
        return RedisStub()

    app.dependency_overrides[get_session] = session_dependency
    app.dependency_overrides[get_readiness_redis] = redis_dependency
    try:
        response = TestClient(app).get("/ready")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json()["checks"]["migrations"] == "out_of_date"


def test_worker_readiness_reports_heartbeat_state() -> None:
    class RedisStub:
        def __init__(self, online):
            self.online = online

        async def scan_iter(self, **_kwargs):
            if self.online:
                yield b"travelpilot:worker:heartbeat:test-instance"

    def request_readiness(online):
        async def redis_dependency():
            return RedisStub(online)

        app.dependency_overrides[get_readiness_redis] = redis_dependency
        try:
            return TestClient(app).get("/ready/worker")
        finally:
            app.dependency_overrides.clear()

    online = request_readiness(True)
    offline = request_readiness(False)

    assert online.status_code == 200
    assert online.json()["status"] == "ready"
    assert online.json()["service"] == "travelpilot-worker"
    assert offline.status_code == 503
    assert offline.json()["status"] == "not_ready"

