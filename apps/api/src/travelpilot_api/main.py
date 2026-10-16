import logging
import time
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

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
from travelpilot_api.api.v1.trips import router as trips_router
from travelpilot_api.api.v1.wishlist import (
    trip_wishlist_router,
    wishlist_item_router,
)
from travelpilot_api.auth import require_request_scope


class HealthResponse(BaseModel):
    status: str
    service: str
    timestamp: datetime


app = FastAPI(
    title="TravelPilot API",
    version="0.1.0",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:3001",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

logger = logging.getLogger("travelpilot.api")


@app.middleware("http")
async def request_trace(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or str(uuid4())
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception as exc:
        logger.error("request_failed request_id=%s method=%s path=%s error_type=%s",
                     request_id, request.method, request.url.path, type(exc).__name__)
        raise
    response.headers["x-request-id"] = request_id
    logger.info("request request_id=%s method=%s path=%s status=%s latency_ms=%s",
                request_id, request.method, request.url.path, response.status_code,
                round((time.perf_counter() - started) * 1000))
    return response


@app.get(
    "/health",
    response_model=HealthResponse,
    tags=["system"],
)
async def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        service="travelpilot-api",
        timestamp=datetime.now(UTC),
    )


# Trip CRUD
app.include_router(auth_router, prefix="/api/v1")
app.include_router(social_router, prefix="/api/v1")
app.include_router(moments_router, prefix="/api/v1")
app.include_router(advisor_router, prefix="/api/v1")

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
