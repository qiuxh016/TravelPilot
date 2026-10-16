import hashlib
import hmac
import secrets
import time
from collections.abc import AsyncIterator

from fastapi import HTTPException, Request
from redis.asyncio import Redis
from redis.exceptions import RedisError

from travelpilot_api.settings import settings

_LOCAL_HASH_KEY = secrets.token_bytes(32)
_INCREMENT_WITH_TTL = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then
  redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return count
"""
_RESERVE_EMAIL_SEND = """
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now - window)
if redis.call('ZCARD', KEYS[1]) >= limit then
  return 0
end
redis.call('ZADD', KEYS[1], now, ARGV[4])
redis.call('EXPIRE', KEYS[1], window + 60)
return 1
"""


async def get_rate_limit_redis() -> AsyncIterator[Redis]:
    client = Redis.from_url(
        settings.redis_url,
        socket_connect_timeout=2,
        socket_timeout=2,
    )
    try:
        yield client
    finally:
        await client.aclose()


def _client_ip_hash(request: Request) -> str:
    client_ip = request.client.host if request.client else "unknown"
    key = settings.auth_rate_limit_hash_secret.encode("utf-8") or _LOCAL_HASH_KEY
    return hmac.new(key, client_ip.encode("utf-8"), hashlib.sha256).hexdigest()


def _retry_after(window_seconds: int, now: int) -> int:
    return max(1, window_seconds - now % window_seconds)


async def _increment(redis: Redis, key: str, ttl_seconds: int) -> int:
    try:
        return int(await redis.eval(_INCREMENT_WITH_TTL, 1, key, ttl_seconds))
    except (RedisError, TimeoutError, OSError) as exc:
        raise HTTPException(
            status_code=503,
            detail="认证服务暂不可用，请稍后重试。",
        ) from exc


async def enforce_ip_rate_limit(
    redis: Redis,
    request: Request,
    *,
    bucket: str,
    limit: int,
    window_seconds: int,
) -> None:
    now = int(time.time())
    window = now // window_seconds
    key = f"travelpilot:rate:{bucket}:{window}:{_client_ip_hash(request)}"
    count = await _increment(redis, key, window_seconds * 2)
    if count > limit:
        raise HTTPException(
            status_code=429,
            detail="请求过于频繁，请稍后重试。",
            headers={"Retry-After": str(_retry_after(window_seconds, now))},
        )


async def consume_email_send_budget(redis: Redis) -> bool:
    now = int(time.time())
    window_seconds = 60 * 60
    try:
        reserved = await redis.eval(
            _RESERVE_EMAIL_SEND,
            1,
            "travelpilot:email-budget",
            now,
            window_seconds,
            settings.email_send_limit_per_hour,
            secrets.token_hex(16),
        )
    except (RedisError, TimeoutError, OSError) as exc:
        raise HTTPException(
            status_code=503,
            detail="认证服务暂不可用，请稍后重试。",
        ) from exc
    return bool(reserved)
