from datetime import datetime, timezone

from fastapi import FastAPI
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
from travelpilot_api.api.v1.trips import router as trips_router
from fastapi.middleware.cors import CORSMiddleware
from travelpilot_api.api.v1.wishlist import (
    trip_wishlist_router,
    wishlist_item_router,
)
from travelpilot_api.api.v1.maps import router as maps_router
from travelpilot_api.api.v1.plans import router as plans_router


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

@app.get(
    "/health",
    response_model=HealthResponse,
    tags=["system"],
)
async def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        service="travelpilot-api",
        timestamp=datetime.now(timezone.utc),
    )


# Trip CRUD
app.include_router(
    trips_router,
    prefix="/api/v1",
)


# 创建和查询某个 Trip 下的 Wishlist
app.include_router(
    trip_wishlist_router,
    prefix="/api/v1",
)


# 修改和删除单个 WishlistItem
app.include_router(
    wishlist_item_router,
    prefix="/api/v1",
)

app.include_router(
    maps_router,
    prefix="/api/v1",
)

app.include_router(plans_router, prefix="/api/v1")
