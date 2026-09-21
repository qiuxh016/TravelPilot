"""Redis backed background execution for replan jobs."""

import json
import logging
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel
from redis.asyncio import Redis
from travelpilot_api.schemas.events import ReplanRequest
from travelpilot_infra.db.session import AsyncSessionFactory

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
    while True:
        item = await redis.blpop(QUEUE, timeout=5)
        if item is not None:
            try:
                job = ReplanJob.model_validate_json(item[1])
                await process_job(redis, job)
            except Exception:
                logger.exception("invalid_replan_job")
        if once:
            return
