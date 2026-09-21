"""Verify queue → worker process → versioned replan with isolated Redis."""

import asyncio
import sys
from uuid import UUID

import httpx
from redis.asyncio import Redis
from sqlalchemy import delete
from travelpilot_api.main import app
from travelpilot_api.settings import settings
from travelpilot_infra.db.models.poi import PoiModel
from travelpilot_infra.db.session import AsyncSessionFactory


async def run():
    redis_url = "redis://localhost:6380/0"
    settings.redis_url = redis_url
    redis = Redis.from_url(redis_url)
    await redis.ping()
    trip_id = None
    poi_ids: set[UUID] = set()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://testserver") as client:
        async def call(method, path, **kwargs):
            response = await client.request(method, path, **kwargs)
            if response.is_error:
                raise AssertionError(f"{method} {path}: {response.status_code} {response.text[:500]}")
            return response.json() if response.content else None

        try:
            trip = await call("POST", "/api/v1/trips", json={
                "destination": "成都", "start_date": "2026-10-01",
                "end_date": "2026-10-02", "pace_level": 2,
            })
            trip_id = trip["id"]
            wishlist = await call("POST", f"/api/v1/trips/{trip_id}/wishlist", json={
                "name": "宽窄巷子", "category": "ATTRACTION",
                "latitude": 30.6633, "longitude": 104.0647,
                "suggested_duration_min": 120,
            })
            poi_ids.add(UUID(wishlist["poi_id"]))
            await call("POST", f"/api/v1/trips/{trip_id}/plan-runs")
            event = await call("POST", f"/api/v1/trips/{trip_id}/events", json={
                "event_type": "WEATHER_CHANGE", "effective_at": "2026-10-01T09:00:00+08:00",
                "payload": {},
            })
            queued = await call("POST", f"/api/v1/trips/{trip_id}/replan-jobs",
                                json={"event_id": event["id"]})
            assert queued["status"] == "QUEUED"
            process = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "travelpilot_worker.main",
                "--redis-url", redis_url, "--once",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            _stdout, stderr = await process.communicate()
            if process.returncode != 0:
                raise AssertionError(f"Worker exited {process.returncode}: {stderr.decode()[:500]}")
            status = await call("GET", f"/api/v1/trips/{trip_id}/replan-jobs/{queued['job_id']}")
            assert status["status"] == "SUCCEEDED", status
            plans = await call("GET", f"/api/v1/trips/{trip_id}/plans")
            assert plans[0]["version"] == 2
            print("WORKER FLOW OK: Redis queue → worker process → Plan v2")
        finally:
            if trip_id:
                await call("DELETE", f"/api/v1/trips/{trip_id}")
            if poi_ids:
                async with AsyncSessionFactory() as session:
                    await session.execute(delete(PoiModel).where(PoiModel.id.in_(poi_ids)))
                    await session.commit()
            await redis.aclose()


if __name__ == "__main__":
    asyncio.run(run())
