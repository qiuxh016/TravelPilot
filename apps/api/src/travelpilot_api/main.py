import logging
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from uuid import UUID, uuid4

from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from alembic.util.exc import CommandError
from fastapi import Depends, FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.routing import Match
from travelpilot_infra.db.session import get_session
from travelpilot_infra.worker_status import WORKER_HEARTBEAT_PREFIX

from travelpilot_api.api.v1.advisor import router as advisor_router
from travelpilot_api.api.v1.agent import router as agent_router
from travelpilot_api.api.v1.auth import router as auth_router
from travelpilot_api.api.v1.content import router as content_router
from travelpilot_api.api.v1.events import router as events_router
from travelpilot_api.api.v1.maps import router as maps_router
from travelpilot_api.api.v1.memory import router as memory_router
from travelpilot_api.api.v1.moments import router as moments_router
from travelpilot_api.api.v1.plans import router as plans_router
from travelpilot_api.api.v1.social import router as social_router
from travelpilot_api.api.v1.trip_members import router as trip_members_router
from travelpilot_api.api.v1.trips import router as trips_router
from travelpilot_api.api.v1.weather import router as weather_router
from travelpilot_api.api.v1.wishlist import (
    trip_wishlist_router,
    wishlist_item_router,
)
from travelpilot_api.auth import require_request_scope
from travelpilot_api.build_info import advisor_runtime_source_manifest, fingerprint_manifest
from travelpilot_api.settings import settings

ADVISOR_RUNTIME_SOURCE_MANIFEST = advisor_runtime_source_manifest()
ADVISOR_RUNTIME_FINGERPRINT = fingerprint_manifest(ADVISOR_RUNTIME_SOURCE_MANIFEST)


class HealthResponse(BaseModel):
    status: str
    service: str
    timestamp: datetime


class ReadinessResponse(BaseModel):
    status: str
    service: str
    timestamp: datetime
    checks: dict[str, str]


class WorkerReadinessResponse(BaseModel):
    status: str
    service: str
    timestamp: datetime


app = FastAPI(
    title="TravelPilot API",
    version="0.1.0",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

logger = logging.getLogger("travelpilot.api")


def _route_template(request: Request) -> str:
    matched_route = request.scope.get("route")
    if matched_route is not None:
        return getattr(matched_route, "path", "unmatched")
    for route in request.app.router.routes:
        match, _child_scope = route.matches(request.scope)
        if match is Match.FULL:
            return getattr(route, "path", "unmatched")
    return "unmatched"


@lru_cache(maxsize=1)
def _expected_migration_heads() -> set[str]:
    repository_root = Path(__file__).resolve().parents[4]
    config_path = repository_root / "alembic.ini"
    if config_path.is_file():
        config = AlembicConfig(str(config_path))
    else:
        import travelpilot_infra

        migrations = Path(travelpilot_infra.__file__).resolve().parent / "db" / "migrations"
        config = AlembicConfig()
        config.set_main_option("script_location", str(migrations))
    return set(ScriptDirectory.from_config(config).get_heads())


@app.middleware("http")
async def request_trace(request: Request, call_next):
    supplied_request_id = request.headers.get("x-request-id", "")
    try:
        request_id = str(UUID(supplied_request_id)) if supplied_request_id else str(uuid4())
    except ValueError:
        request_id = str(uuid4())
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception as exc:
        route = _route_template(request)
        logger.error("request_failed request_id=%s method=%s route=%s error_type=%s",
                     request_id, request.method, route, type(exc).__name__)
        raise
    route = _route_template(request)
    response.headers["x-request-id"] = request_id
    if response.status_code == 429:
        logger.warning(
            "security_event=rate_limited request_id=%s method=%s route=%s status=%s",
            request_id, request.method, route, response.status_code,
        )
    elif response.status_code >= 500:
        logger.warning(
            "security_event=server_error request_id=%s method=%s route=%s status=%s",
            request_id, request.method, route, response.status_code,
        )
    logger.info("request request_id=%s method=%s route=%s status=%s latency_ms=%s",
                request_id, request.method, route, response.status_code,
                round((time.perf_counter() - started) * 1000))
    return response


@app.get(
    "/health",
    response_model=HealthResponse,
    tags=["system"],
)
async def health(response: Response) -> HealthResponse:
    response.headers["X-Advisor-Runtime-Fingerprint"] = ADVISOR_RUNTIME_FINGERPRINT
    return HealthResponse(
        status="ok",
        service="travelpilot-api",
        timestamp=datetime.now(UTC),
    )


async def get_readiness_redis() -> AsyncIterator[Redis]:
    client = Redis.from_url(
        settings.redis_url,
        socket_connect_timeout=2,
        socket_timeout=2,
    )
    try:
        yield client
    finally:
        await client.aclose()


@app.get("/ready", response_model=ReadinessResponse, tags=["system"])
async def readiness(
    response: Response,
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_readiness_redis),
) -> ReadinessResponse:
    checks = {"database": "ok", "migrations": "ok", "queue": "ok"}
    try:
        await session.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        checks["database"] = "unavailable"
        checks["migrations"] = "unavailable"
        logger.warning("readiness_dependency_failed dependency=database error_type=%s", type(exc).__name__)
    else:
        try:
            applied_result = await session.execute(text("SELECT version_num FROM alembic_version"))
            applied_heads = set(applied_result.scalars().all())
            checks["migrations"] = (
                "ok" if applied_heads == _expected_migration_heads() else "out_of_date"
            )
        except (SQLAlchemyError, OSError, CommandError, ValueError) as exc:
            checks["migrations"] = "unavailable"
            logger.warning("readiness_dependency_failed dependency=migrations error_type=%s",
                           type(exc).__name__)
    try:
        if not await redis.ping():
            raise RedisError("Redis ping returned false")
    except (RedisError, TimeoutError, OSError) as exc:
        checks["queue"] = "unavailable"
        logger.warning("readiness_dependency_failed dependency=queue error_type=%s", type(exc).__name__)

    is_ready = all(value == "ok" for value in checks.values())
    if not is_ready:
        response.status_code = 503
    return ReadinessResponse(
        status="ready" if is_ready else "not_ready",
        service="travelpilot-api",
        timestamp=datetime.now(UTC),
        checks=checks,
    )


@app.get("/ready/worker", response_model=WorkerReadinessResponse, tags=["system"])
async def worker_readiness(
    response: Response,
    redis: Redis = Depends(get_readiness_redis),
) -> WorkerReadinessResponse:
    worker_online = False
    try:
        async for _key in redis.scan_iter(match=f"{WORKER_HEARTBEAT_PREFIX}*", count=10):
            worker_online = True
            break
    except (RedisError, TimeoutError, OSError) as exc:
        logger.warning("readiness_dependency_failed dependency=worker_heartbeat error_type=%s",
                       type(exc).__name__)

    if not worker_online:
        response.status_code = 503
    return WorkerReadinessResponse(
        status="ready" if worker_online else "not_ready",
        service="travelpilot-worker",
        timestamp=datetime.now(UTC),
    )


# Trip CRUD
app.include_router(auth_router, prefix="/api/v1")
app.include_router(social_router, prefix="/api/v1")
app.include_router(trip_members_router, prefix="/api/v1")
app.include_router(moments_router, prefix="/api/v1")
app.include_router(advisor_router, prefix="/api/v1")
app.include_router(weather_router, prefix="/api/v1")

app.include_router(
    trips_router,
    prefix="/api/v1",
    dependencies=[Depends(require_request_scope)],
)


# 创建和查询某个 Trip 下的 Wishlist
app.include_router(
    trip_wishlist_router,
    prefix="/api/v1",
    dependencies=[Depends(require_request_scope)],
)


# 修改和删除单个 WishlistItem
app.include_router(
    wishlist_item_router,
    prefix="/api/v1",
    dependencies=[Depends(require_request_scope)],
)

app.include_router(
    maps_router,
    prefix="/api/v1",
    dependencies=[Depends(require_request_scope)],
)

app.include_router(plans_router, prefix="/api/v1", dependencies=[Depends(require_request_scope)])

app.include_router(content_router, prefix="/api/v1", dependencies=[Depends(require_request_scope)])
app.include_router(agent_router, prefix="/api/v1", dependencies=[Depends(require_request_scope)])
app.include_router(events_router, prefix="/api/v1", dependencies=[Depends(require_request_scope)])
app.include_router(memory_router, prefix="/api/v1", dependencies=[Depends(require_request_scope)])
