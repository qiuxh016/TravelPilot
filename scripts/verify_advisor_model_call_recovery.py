"""Verify an interrupted Advisor model call can be retried without duplicate messages.

The model endpoint is a local OpenAI-compatible stub. This is a process-recovery
contract check, not a live-model quality or provider-idempotency test.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.request import Request, urlopen
from uuid import UUID, uuid4

from sqlalchemy import delete, select
from travelpilot_infra.db.models.advisor import (
    AdvisorMessageModel,
    AdvisorRunEventModel,
    AdvisorSessionModel,
)
from travelpilot_infra.db.models.user import UserModel
from travelpilot_infra.db.session import AsyncSessionFactory

ROOT = Path(__file__).resolve().parents[1]
STUB = ROOT / "scripts" / "local_openai_chat_stub.py"


async def _invoke(*, phase: str, owner_id: UUID, session_id: UUID,
                  key: str, content: str) -> None:
    from travelpilot_api.api.v1 import advisor_messages
    from travelpilot_api.schemas.advisor import AdvisorMessageCreate
    from travelpilot_infra.providers.maps.fake import FakeMapsProvider

    async with AsyncSessionFactory() as db:
        user = await db.scalar(select(UserModel).where(UserModel.id == owner_id))
        assert user is not None
        await advisor_messages.send_message(
            session_id=session_id,
            payload=AdvisorMessageCreate(content=content),
            db=db,
            user=user,
            maps=FakeMapsProvider(),
            weather=None,
            idempotency_key=key,
        )


def _child_process(*, phase: str, owner_id: UUID, session_id: UUID,
                   key: str, content: str, base_url: str) -> None:
    env = dict(os.environ)
    env.update({
        "LLM_API_KEY": "local-test-key",
        "LLM_MODEL": "local-stub",
        "LLM_BASE_URL": base_url,
        "LLM_MAX_TOKENS_PER_TURN": "1200",
        "LLM_MAX_OUTPUT_TOKENS_PER_SESSION": "12000",
        "MAPS_PROVIDER": "fake",
        "AMAP_API_KEY": "",
        "SMTP_HOST": "",
    })
    command = [
        sys.executable, str(Path(__file__).resolve()), "--phase", phase,
        "--owner-id", str(owner_id), "--session-id", str(session_id),
        "--key", key, "--content", content,
    ]
    result = subprocess.run(
        command, cwd=ROOT, env=env, capture_output=True, text=True,
        timeout=90, check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"{phase} child exited {result.returncode}; "
            f"stdout={result.stdout[-2000:]}; stderr={result.stderr[-4000:]}"
        )


async def _interrupt_during_model_request(
    *, owner_id: UUID, session_id: UUID, key: str, content: str,
    base_url: str, stats_url: str, counter: str = "extraction_requests",
) -> None:
    env = dict(os.environ)
    env.update({
        "LLM_API_KEY": "local-test-key",
        "LLM_MODEL": "local-stub",
        "LLM_BASE_URL": base_url,
        "LLM_MAX_TOKENS_PER_TURN": "1200",
        "LLM_MAX_OUTPUT_TOKENS_PER_SESSION": "12000",
        "MAPS_PROVIDER": "fake",
        "AMAP_API_KEY": "",
        "SMTP_HOST": "",
    })
    child = await asyncio.create_subprocess_exec(
        sys.executable, str(Path(__file__).resolve()), "--phase", "IN_FLIGHT",
        "--owner-id", str(owner_id), "--session-id", str(session_id),
        "--key", key, "--content", content,
        cwd=ROOT, env=env,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        for _ in range(160):
            if child.returncode is not None:
                raise RuntimeError(f"model-call child exited before interruption: {child.returncode}")
            if _stats(stats_url)[counter] >= 1:
                await asyncio.sleep(0.1)
                child.terminate()
                await asyncio.wait_for(child.wait(), timeout=5)
                return
            await asyncio.sleep(0.025)
        raise TimeoutError(f"local stub did not receive the in-flight {counter} request")
    finally:
        if child.returncode is None:
            child.kill()
            await asyncio.wait_for(child.wait(), timeout=5)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _stats(url: str) -> dict:
    with urlopen(url, timeout=1) as response:
        return json.loads(response.read())


def _reset_stub(url: str) -> None:
    request = Request(url, method="POST")
    with urlopen(request, timeout=2):
        pass


async def verify() -> None:
    owner_id, session_id, reply_session_id = uuid4(), uuid4(), uuid4()
    key = f"model-retry-{uuid4().hex[:20]}"
    reply_key = f"reply-retry-{uuid4().hex[:20]}"
    content = "帮我想想成都适合怎样的旅行体验"
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}/v1"
    stub = await asyncio.create_subprocess_exec(
        sys.executable, str(STUB), "--port", str(port), "--delay-seconds", "3",
        cwd=ROOT, stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    now = datetime.now(UTC)
    try:
        for _ in range(100):
            if stub.returncode is not None:
                raise RuntimeError(f"local model stub exited with {stub.returncode}")
            try:
                _stats(f"http://127.0.0.1:{port}/stats")
                break
            except OSError:
                await asyncio.sleep(0.05)
        else:
            raise TimeoutError("local model stub did not start")

        async with AsyncSessionFactory() as db:
            db.add(UserModel(
                id=owner_id,
                email=f"{key}@example.invalid",
                username=f"m{owner_id.hex[:23]}",
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
                mode="discover",
                destination="成都",
                pace_level=3,
                preference_text="",
                candidates=[],
                context={},
                status="ACTIVE",
                created_at=now,
                updated_at=now,
            ))
            db.add(AdvisorSessionModel(
                id=reply_session_id,
                owner_id=owner_id,
                mode="discover",
                destination="成都",
                pace_level=3,
                preference_text="",
                candidates=[],
                context={},
                status="ACTIVE",
                created_at=now,
                updated_at=now,
            ))
            await db.commit()

        stats_url = f"http://127.0.0.1:{port}/stats"
        await _interrupt_during_model_request(
            owner_id=owner_id, session_id=session_id, key=key, content=content,
            base_url=base_url, stats_url=stats_url,
        )
        first_stats = _stats(stats_url)
        assert first_stats["extraction_requests"] == 1
        async with AsyncSessionFactory() as db:
            rows = (await db.scalars(select(AdvisorMessageModel).where(
                AdvisorMessageModel.session_id == session_id,
                AdvisorMessageModel.owner_id == owner_id,
            ))).all()
            assert len(rows) == 1 and rows[0].role == "user"
            assert rows[0].idempotency_key == key

        _child_process(
            phase="IN_FLIGHT", owner_id=owner_id, session_id=session_id,
            key=key, content=content, base_url=base_url,
        )
        final_stats = _stats(stats_url)
        assert final_stats["extraction_requests"] >= 2
        async with AsyncSessionFactory() as db:
            rows = (await db.scalars(select(AdvisorMessageModel).where(
                AdvisorMessageModel.session_id == session_id,
                AdvisorMessageModel.owner_id == owner_id,
            ))).all()
            user_rows = [row for row in rows if row.role == "user"]
            assistant_rows = [row for row in rows if row.role == "assistant"]
            assert len(user_rows) == len(assistant_rows) == 1
            assert user_rows[0].idempotency_key == key
            assert assistant_rows[0].payload["response_to_idempotency_key"] == key

        await asyncio.to_thread(_reset_stub, f"http://127.0.0.1:{port}/reset")
        reply_content = "结合当前成都旅行信息，给我一些总体建议"
        await _interrupt_during_model_request(
            owner_id=owner_id, session_id=reply_session_id, key=reply_key,
            content=reply_content, base_url=base_url, stats_url=stats_url,
            counter="reply_requests",
        )
        interrupted_reply_stats = _stats(stats_url)
        assert interrupted_reply_stats["extraction_requests"] == 1
        assert interrupted_reply_stats["reply_requests"] == 1
        async with AsyncSessionFactory() as db:
            checkpoint = await db.scalar(select(AdvisorRunEventModel).where(
                AdvisorRunEventModel.owner_id == owner_id,
                AdvisorRunEventModel.session_id == reply_session_id,
                AdvisorRunEventModel.phase == "intent_recovery",
                AdvisorRunEventModel.operation == "advisor_intent_result",
            ))
            assert checkpoint is not None and checkpoint.status == "COMPLETED"

        _child_process(
            phase="IN_FLIGHT", owner_id=owner_id, session_id=reply_session_id,
            key=reply_key, content=reply_content, base_url=base_url,
        )
        reply_retry_stats = _stats(stats_url)
        assert reply_retry_stats["extraction_requests"] == 1
        assert reply_retry_stats["reply_requests"] == 2
        async with AsyncSessionFactory() as db:
            retry_rows = (await db.scalars(select(AdvisorMessageModel).where(
                AdvisorMessageModel.session_id == reply_session_id,
                AdvisorMessageModel.owner_id == owner_id,
            ))).all()
            retry_users = [row for row in retry_rows if row.role == "user"]
            retry_assistants = [row for row in retry_rows if row.role == "assistant"]
            assert len(retry_users) == len(retry_assistants) == 1
            assert retry_users[0].idempotency_key == reply_key
            retry_payload = retry_assistants[0].payload
            assert retry_payload["response_to_idempotency_key"] == reply_key
            assert retry_payload["extraction_checkpoint_recovered"] is True
            assert retry_payload["extraction_source"] == "RECOVERED_CHECKPOINT"
            assert retry_payload["llm_output_tokens"] == 40
            remaining_checkpoint = await db.scalar(select(AdvisorRunEventModel.id).where(
                AdvisorRunEventModel.owner_id == owner_id,
                AdvisorRunEventModel.session_id == reply_session_id,
                AdvisorRunEventModel.phase == "intent_recovery",
                AdvisorRunEventModel.operation == "advisor_intent_result",
            ))
            assert remaining_checkpoint is None
        print(
            "PASS: the API process was killed while local LLM extraction was in flight; "
            "same-key retry completed with one user message and one linked assistant reply."
        )
        print(
            "PASS: the API process was killed during reply composition after extraction "
            "checkpoint commit; retry reused extraction, repeated only reply composition, "
            "restored token usage, and stored one linked assistant reply."
        )
        print(
            "LIMIT: the interrupted in-flight provider request remains at-least-once and "
            "may incur a duplicate charge; provider idempotency was not tested."
        )
    finally:
        async with AsyncSessionFactory() as db:
            await db.execute(delete(UserModel).where(UserModel.id == owner_id))
            await db.commit()
        stub.terminate()
        try:
            await asyncio.wait_for(stub.wait(), timeout=5)
        except TimeoutError:
            stub.kill()
            await asyncio.wait_for(stub.wait(), timeout=5)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("IN_FLIGHT",))
    parser.add_argument("--owner-id")
    parser.add_argument("--session-id")
    parser.add_argument("--key")
    parser.add_argument("--content")
    args = parser.parse_args()
    if args.phase:
        if not all((args.owner_id, args.session_id, args.key, args.content)):
            parser.error("child phase requires owner, session, key and content")
        asyncio.run(_invoke(
            phase=args.phase,
            owner_id=UUID(args.owner_id),
            session_id=UUID(args.session_id),
            key=args.key,
            content=args.content,
        ))
    else:
        asyncio.run(verify())
