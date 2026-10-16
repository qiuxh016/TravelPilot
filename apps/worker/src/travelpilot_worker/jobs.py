"""Redis backed background execution for replan jobs."""

import json
import logging
import time
from uuid import UUID, uuid4

from fastapi import HTTPException
from pydantic import BaseModel
from redis.asyncio import Redis
from travelpilot_api.schemas.events import ReplanRequest
from travelpilot_infra.db.session import AsyncSessionFactory
from travelpilot_infra.worker_status import (
    WORKER_HEARTBEAT_INTERVAL_SECONDS,
    WORKER_HEARTBEAT_PREFIX,
    WORKER_HEARTBEAT_TTL_SECONDS,
)

from travelpilot_worker.maintenance import cleanup_expired_auth_data

QUEUE = "travelpilot:replan:queue"
RESULT_PREFIX = "travelpilot:replan:result:"
logger = logging.getLogger("travelpilot.worker")


class ReplanJob(BaseModel):
    id: UUID
    trip_id: UUID
    event_id: UUID


async def process_job(redis: Redis, job: ReplanJob) -> dict:
    key = RESULT_PREFIX + str(job.id)
    await redis.setex(key, 86400, json.dumps({"status": "RUNNING", "trip_id": str(job.trip_id)}))
    try:
        from travelpilot_api.api.v1.events import create_replan

        async with AsyncSessionFactory() as session:
            result = await create_replan(job.trip_id, ReplanRequest(event_id=job.event_id), session)
        outcome = {"status": "SUCCEEDED", "trip_id": str(job.trip_id), "plan_id": str(result["plan"].id),
                   "run_id": str(result["run_id"])}
    except HTTPException as exc:
        outcome = {"status": "FAILED", "trip_id": str(job.trip_id), "error": str(exc.detail)}
    except Exception:
        logger.exception("replan_job_failed job_id=%s", job.id)
        outcome = {"status": "FAILED", "trip_id": str(job.trip_id), "error": "WORKER_ERROR"}
    await redis.setex(key, 86400, json.dumps(outcome))
    return outcome


async def work(redis: Redis, *, once: bool = False) -> None:
    worker_key = WORKER_HEARTBEAT_PREFIX + str(uuid4())
    next_auth_cleanup = 0.0
    next_heartbeat = 0.0
    try:
        while True:
            if time.monotonic() >= next_heartbeat:
                await redis.setex(worker_key, WORKER_HEARTBEAT_TTL_SECONDS, "online")
                next_heartbeat = time.monotonic() + WORKER_HEARTBEAT_INTERVAL_SECONDS

            if time.monotonic() >= next_auth_cleanup:
                try:
                    removed = await cleanup_expired_auth_data()
                    logger.info(
                        "auth_data_cleanup_complete sessions=%s email_tokens=%s login_attempts=%s",
                        removed["sessions"], removed["email_tokens"], removed["login_attempts"],
                    )
                except Exception as exc:  # noqa: BLE001 - maintenance failure must not stop job processing
                    logger.warning("auth_data_cleanup_failed error_type=%s", type(exc).__name__)
                next_auth_cleanup = time.monotonic() + 60 * 60

            item = await redis.blpop(QUEUE, timeout=5)
            if item is not None:
                try:
                    job = ReplanJob.model_validate_json(item[1])
                    await process_job(redis, job)
                except Exception:
                    logger.exception("invalid_replan_job")
            if once:
                return
    finally:
        try:
            await redis.delete(worker_key)
        except Exception as exc:  # noqa: BLE001 - expiring heartbeat is safe if Redis is unavailable
            logger.warning("worker_heartbeat_cleanup_failed error_type=%s", type(exc).__name__)
