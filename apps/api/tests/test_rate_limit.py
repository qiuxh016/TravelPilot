import pytest
from fastapi import HTTPException, Request
from redis.exceptions import RedisError
from travelpilot_api.rate_limit import (
    _client_ip_hash,
    consume_email_send_budget,
    enforce_identity_rate_limit,
    enforce_ip_rate_limit,
)
from travelpilot_api.settings import settings


class RedisCounterStub:
    def __init__(self):
        self.counts = {}
        self.email_budget_counts = {}
        self.keys = []

    async def eval(self, script, _key_count, key, *args):
        self.keys.append(key)
        if "ZREMRANGEBYSCORE" in script:
            count = self.email_budget_counts.get(key, 0)
            if count >= int(args[2]):
                return 0
            self.email_budget_counts[key] = count + 1
            return 1
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]


def make_request(ip: str = "192.0.2.44") -> Request:
    return Request({
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/v1/auth/register",
        "query_string": b"",
        "headers": [],
        "server": ("localhost", 8000),
        "client": (ip, 12345),
    })


@pytest.mark.asyncio
async def test_ip_limit_hashes_client_ip_and_returns_retry_after():
    redis = RedisCounterStub()
    request = make_request()

    await enforce_ip_rate_limit(
        redis, request, bucket="register", limit=2, window_seconds=60,
    )
    await enforce_ip_rate_limit(
        redis, request, bucket="register", limit=2, window_seconds=60,
    )
    with pytest.raises(HTTPException) as error:
        await enforce_ip_rate_limit(
            redis, request, bucket="register", limit=2, window_seconds=60,
        )

    assert error.value.status_code == 429
    assert 1 <= int(error.value.headers["Retry-After"]) <= 60
    assert all("192.0.2.44" not in key for key in redis.keys)


@pytest.mark.asyncio
async def test_identity_limit_isolated_by_identity_and_returns_retry_after():
    redis = RedisCounterStub()
    await enforce_identity_rate_limit(
        redis, "user-one", bucket="advisor-destinations", limit=1, window_seconds=600,
    )
    await enforce_identity_rate_limit(
        redis, "user-two", bucket="advisor-destinations", limit=1, window_seconds=600,
    )
    with pytest.raises(HTTPException) as error:
        await enforce_identity_rate_limit(
            redis, "user-one", bucket="advisor-destinations", limit=1, window_seconds=600,
        )

    assert error.value.status_code == 429
    assert 1 <= int(error.value.headers["Retry-After"]) <= 600
    assert all("user-one" not in key and "user-two" not in key for key in redis.keys)


@pytest.mark.asyncio
async def test_email_send_budget_caps_hourly_sends(monkeypatch):
    redis = RedisCounterStub()
    monkeypatch.setattr(settings, "email_send_limit_per_hour", 2)

    assert await consume_email_send_budget(redis) is True
    assert await consume_email_send_budget(redis) is True
    assert await consume_email_send_budget(redis) is False


@pytest.mark.asyncio
async def test_rate_limit_fails_closed_when_redis_is_unavailable():
    class UnavailableRedis:
        async def eval(self, *_args):
            raise RedisError("private connection details")

    with pytest.raises(HTTPException) as error:
        await enforce_ip_rate_limit(
            UnavailableRedis(), make_request(), bucket="login", limit=30, window_seconds=60,
        )

    assert error.value.status_code == 503
    assert "private connection details" not in error.value.detail


def test_ip_hash_uses_the_configured_secret(monkeypatch):
    request = make_request()
    monkeypatch.setattr(settings, "auth_rate_limit_hash_secret", "first-test-secret")
    first = _client_ip_hash(request)
    monkeypatch.setattr(settings, "auth_rate_limit_hash_secret", "second-test-secret")
    second = _client_ip_hash(request)

    assert first != second
    assert "192.0.2.44" not in first
