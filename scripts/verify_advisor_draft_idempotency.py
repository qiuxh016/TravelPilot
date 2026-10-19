"""Verify committed Advisor draft retries stay idempotent across processes."""

import argparse
import asyncio
import os
import subprocess
import sys
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import delete, select
from travelpilot_api.api.v1.advisor_drafts import create_draft
from travelpilot_api.schemas.advisor import AdvisorDraftCreate
from travelpilot_infra.db.models.advisor import (
    AdvisorMessageModel,
    AdvisorRunEventModel,
    AdvisorSessionModel,
)
from travelpilot_infra.db.models.plan import TripPlanModel, TripPlanNodeModel
from travelpilot_infra.db.models.trip import TripModel
from travelpilot_infra.db.models.user import UserModel
from travelpilot_infra.db.session import AsyncSessionFactory
from travelpilot_infra.providers.maps.fake import FakeMapsProvider


class FakeWeatherProvider:
    async def forecast(self, city, start_date, end_date):
        return {
            "status": "OUT_OF_RANGE",
            "city": city,
            "timezone": "Asia/Shanghai",
            "source": "Open-Meteo",
            "attribution": "Weather by Open-Meteo",
            "fetched_at": datetime.now(UTC),
            "message": "Synthetic verifier forecast.",
            "days": [],
        }


def _payload(candidate_id: UUID, *, start_time: str = "10:00") -> AdvisorDraftCreate:
    day = date.today() + timedelta(days=3)
    return AdvisorDraftCreate(
        candidate_ids=[candidate_id],
        schedules=[{
            "candidate_id": candidate_id,
            "day": day,
            "start_time": time.fromisoformat(start_time),
        }],
    )


async def _child(*, phase: str, owner_id: UUID, session_id: UUID,
                 candidate_id: UUID, key: str) -> None:
    from travelpilot_api.api.v1 import advisor_drafts

    async with AsyncSessionFactory() as db:
        user = await db.scalar(select(UserModel).where(UserModel.id == owner_id))
        assert user is not None
        original_read = advisor_drafts.read_session
        if phase == "INTERRUPT":
            async def read_then_exit(read_db, row):
                await original_read(read_db, row)
                # create_draft commits the draft and idempotency record before
                # this response boundary. Simulate losing the response.
                os._exit(76)

            advisor_drafts.read_session = read_then_exit
        try:
            result = await create_draft(
                session_id=session_id,
                payload=_payload(candidate_id, start_time="11:00" if phase == "MISMATCH" else "10:00"),
                db=db,
                user=user,
                maps=FakeMapsProvider(),
                weather=FakeWeatherProvider(),
                idempotency_key=key,
            )
            if phase == "MISMATCH":
                raise AssertionError("reusing one key for a different draft request must fail")
            assert result.draft_version == 1
        except HTTPException as exc:
            if phase != "MISMATCH" or exc.status_code != 409:
                raise
        finally:
            advisor_drafts.read_session = original_read


async def _confirm_child(*, phase: str, owner_id: UUID, session_id: UUID) -> None:
    from travelpilot_api.advisor import confirm_service
    from travelpilot_api.api.v1 import advisor_confirm
    from travelpilot_api.schemas.advisor import AdvisorConfirm

    async with AsyncSessionFactory() as db:
        user = await db.scalar(select(UserModel).where(UserModel.id == owner_id))
        assert user is not None
        original_confirm = advisor_confirm.confirm_advisor_draft
        if phase == "CONFIRM_INTERRUPT":
            async def confirm_then_exit(*args, **kwargs):
                await confirm_service.confirm_advisor_draft(*args, **kwargs)
                # The confirmation service committed the trip and plan; the
                # process exits before the API response can reach the client.
                os._exit(77)

            advisor_confirm.confirm_advisor_draft = confirm_then_exit
        try:
            result = await advisor_confirm.confirm_draft(
                session_id=session_id,
                payload=AdvisorConfirm(draft_version=1),
                db=db,
                user=user,
            )
            if phase == "CONFIRM_INTERRUPT":
                raise AssertionError("confirmation interrupt hook should exit after commit")
            assert result.already_confirmed is True
            assert result.plan_version == 1
        finally:
            advisor_confirm.confirm_advisor_draft = original_confirm


def _child_process(*, phase: str, owner_id: UUID, session_id: UUID,
                   candidate_id: UUID | None = None, key: str | None = None) -> None:
    env = dict(os.environ)
    env.update({"LLM_API_KEY": "", "LLM_BASE_URL": "", "MAPS_PROVIDER": "fake"})
    command = [
        sys.executable, str(Path(__file__).resolve()), "--phase", phase,
        "--owner-id", str(owner_id), "--session-id", str(session_id),
    ]
    if candidate_id is not None:
        command.extend(("--candidate-id", str(candidate_id)))
    if key is not None:
        command.extend(("--key", key))
    result = subprocess.run(
        command,
        cwd=Path(__file__).resolve().parents[1], env=env,
        capture_output=True, text=True, timeout=60, check=False,
    )
    expected = 76 if phase == "INTERRUPT" else 77 if phase == "CONFIRM_INTERRUPT" else 0
    if result.returncode != expected:
        raise RuntimeError(
            f"draft {phase} process exited {result.returncode}; "
            f"stdout={result.stdout[-2000:]}; stderr={result.stderr[-4000:]}"
        )


async def verify() -> None:
    owner_id, session_id, candidate_id = uuid4(), uuid4(), uuid4()
    key = f"draft-retry-{uuid4().hex[:20]}"
    trip_day = date.today() + timedelta(days=3)
    now = datetime.now(UTC)
    try:
        async with AsyncSessionFactory() as db:
            db.add(UserModel(
                id=owner_id,
                email=f"{key}@example.invalid",
                username=f"d{owner_id.hex[:23]}",
                password_hash="integration-test-only",
                display_name="",
                home_city="",
                bio="",
                is_active=True,
                email_verified=True,
                created_at=now,
            ))
            await db.commit()
        async with AsyncSessionFactory() as db:
            db.add(AdvisorSessionModel(
                id=session_id,
                owner_id=owner_id,
                mode="existing",
                destination="成都",
                start_date=trip_day,
                end_date=trip_day + timedelta(days=1),
                pace_level=3,
                preference_text="",
                candidates=[{
                    "id": str(candidate_id),
                    "provider": "fake",
                    "provider_id": "draft-recovery-place",
                    "name": "恢复验证景点",
                    "address": "成都",
                    "category": "ATTRACTION",
                    "planning_category": "ATTRACTION",
                    "latitude": 30.66,
                    "longitude": 104.06,
                    "duration_min": 60,
                    "confidence": 0.8,
                    "verification_status": "SIMULATED",
                    "verified_at": now.isoformat(),
                }],
                context={},
                status="ACTIVE",
                draft_version=0,
                created_at=now,
                updated_at=now,
            ))
            await db.commit()

        _child_process(
            phase="INTERRUPT", owner_id=owner_id, session_id=session_id,
            candidate_id=candidate_id, key=key,
        )
        async with AsyncSessionFactory() as db:
            row = await db.scalar(select(AdvisorSessionModel).where(
                AdvisorSessionModel.id == session_id,
                AdvisorSessionModel.owner_id == owner_id,
            ))
            assert row is not None and row.draft_version == 1
            assert row.draft and row.draft["version"] == 1
            assert len((await db.scalars(select(AdvisorMessageModel).where(
                AdvisorMessageModel.session_id == session_id,
                AdvisorMessageModel.owner_id == owner_id,
                AdvisorMessageModel.role == "assistant",
            ))).all()) == 1

        _child_process(
            phase="RETRY", owner_id=owner_id, session_id=session_id,
            candidate_id=candidate_id, key=key,
        )
        _child_process(
            phase="MISMATCH", owner_id=owner_id, session_id=session_id,
            candidate_id=candidate_id, key=key,
        )
        async with AsyncSessionFactory() as db:
            row = await db.scalar(select(AdvisorSessionModel).where(
                AdvisorSessionModel.id == session_id,
                AdvisorSessionModel.owner_id == owner_id,
            ))
            assert row is not None and row.draft_version == 1
            assert row.draft and row.draft["version"] == 1
            messages = (await db.scalars(select(AdvisorMessageModel).where(
                AdvisorMessageModel.session_id == session_id,
                AdvisorMessageModel.owner_id == owner_id,
                AdvisorMessageModel.role == "assistant",
            ))).all()
            assert len(messages) == 1 and messages[0].payload["draft_version"] == 1
            events = (await db.scalars(select(AdvisorRunEventModel).where(
                AdvisorRunEventModel.owner_id == owner_id,
                AdvisorRunEventModel.session_id == session_id,
                AdvisorRunEventModel.operation == "create_draft_idempotency",
            ))).all()
            assert len(events) == 1 and events[0].metadata_json["draft_version"] == 1
        print(
            "PASS: a draft committed before response loss was replayed across processes "
            "without creating another version; changed-payload key reuse was rejected."
        )

        _child_process(
            phase="CONFIRM_INTERRUPT", owner_id=owner_id, session_id=session_id,
        )
        async with AsyncSessionFactory() as db:
            confirmed = await db.scalar(select(AdvisorSessionModel).where(
                AdvisorSessionModel.id == session_id,
                AdvisorSessionModel.owner_id == owner_id,
            ))
            assert confirmed is not None and confirmed.status == "CONFIRMED"
            assert confirmed.confirmed_trip_id and confirmed.confirmed_plan_id
            plans_before = (await db.scalars(select(TripPlanModel).join(TripModel).where(
                TripModel.owner_id == owner_id,
            ))).all()
            nodes_before = (await db.scalars(select(TripPlanNodeModel).where(
                TripPlanNodeModel.plan_id == confirmed.confirmed_plan_id,
            ))).all()
            assert len(plans_before) == 1 and plans_before[0].version == 1
            assert len(nodes_before) == 1

        _child_process(
            phase="CONFIRM_RETRY", owner_id=owner_id, session_id=session_id,
        )
        async with AsyncSessionFactory() as db:
            confirmed = await db.scalar(select(AdvisorSessionModel).where(
                AdvisorSessionModel.id == session_id,
                AdvisorSessionModel.owner_id == owner_id,
            ))
            assert confirmed is not None and confirmed.status == "CONFIRMED"
            plans_after = (await db.scalars(select(TripPlanModel).join(TripModel).where(
                TripModel.owner_id == owner_id,
            ))).all()
            nodes_after = (await db.scalars(select(TripPlanNodeModel).where(
                TripPlanNodeModel.plan_id == confirmed.confirmed_plan_id,
            ))).all()
            confirm_events = (await db.scalars(select(AdvisorRunEventModel).where(
                AdvisorRunEventModel.owner_id == owner_id,
                AdvisorRunEventModel.session_id == session_id,
                AdvisorRunEventModel.operation == "confirm_draft",
            ))).all()
            confirm_messages = (await db.scalars(select(AdvisorMessageModel).where(
                AdvisorMessageModel.session_id == session_id,
                AdvisorMessageModel.owner_id == owner_id,
                AdvisorMessageModel.payload["plan_id"].as_string()
                == str(confirmed.confirmed_plan_id),
            ))).all()
            assert len(plans_after) == 1 and plans_after[0].version == 1
            assert len(nodes_after) == 1
            assert len(confirm_events) == 1 and len(confirm_messages) == 1
        print(
            "PASS: a committed trip confirmation retried from another process returned "
            "the same plan without creating a duplicate trip, plan, node, event, or message."
        )
    finally:
        async with AsyncSessionFactory() as db:
            await db.execute(delete(UserModel).where(UserModel.id == owner_id))
            await db.commit()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=(
        "INTERRUPT", "RETRY", "MISMATCH", "CONFIRM_INTERRUPT", "CONFIRM_RETRY",
    ))
    parser.add_argument("--owner-id")
    parser.add_argument("--session-id")
    parser.add_argument("--candidate-id")
    parser.add_argument("--key")
    args = parser.parse_args()
    if args.phase:
        if not all((args.owner_id, args.session_id)):
            parser.error("child phase requires owner and session")
        if args.phase.startswith("CONFIRM_"):
            asyncio.run(_confirm_child(
                phase=args.phase,
                owner_id=UUID(args.owner_id),
                session_id=UUID(args.session_id),
            ))
        else:
            if not all((args.candidate_id, args.key)):
                parser.error("draft child phase requires candidate and key")
            asyncio.run(_child(
                phase=args.phase,
                owner_id=UUID(args.owner_id),
                session_id=UUID(args.session_id),
                candidate_id=UUID(args.candidate_id),
                key=args.key,
            ))
    else:
        asyncio.run(verify())
