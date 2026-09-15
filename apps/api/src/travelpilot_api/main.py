from datetime import datetime, timezone

from fastapi import FastAPI
from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    service: str
    timestamp: datetime


app = FastAPI(title="TravelPilot API", version="0.1.0")


@app.get("/health", response_model=HealthResponse, tags=["system"])
async def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        service="travelpilot-api",
        timestamp=datetime.now(timezone.utc),
    )


@app.get("/api/v1/trips", tags=["trips"])
async def list_trips() -> dict[str, list[dict[str, str]]]:
    """Placeholder endpoint for the first Trip CRUD slice."""
    return {"items": []}

