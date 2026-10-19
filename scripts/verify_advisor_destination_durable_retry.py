"""Prove destination-discovery evidence survives a real PostgreSQL process crash.

This verifier uses an AMap-shaped local fixture and the deterministic no-key
policy. It never calls DeepSeek, AMap, weather, SMTP, or a formal plan endpoint.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import socket
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.request import urlopen
from uuid import UUID, uuid4

from sqlalchemy import delete, select
from travelpilot_core.ports.maps import Place
from travelpilot_infra.db.models.advisor import (
    AdvisorMessageModel,
    AdvisorRunEventModel,
    AdvisorSessionModel,
)
from travelpilot_infra.db.models.user import UserModel
from travelpilot_infra.db.session import AsyncSessionFactory


class AmapProvider:
    """Small deterministic fixture; the retry can forbid repeating park lookup."""

    def __init__(
        self, *, retry: bool = False, interrupt_after_decision: bool = False,
        owner_id: UUID | None = None, session_id: UUID | None = None,
        idempotency_key: str | None = None,
    ):
        self.retry = retry
        self.interrupt_after_decision = interrupt_after_decision
        self.owner_id = owner_id
        self.session_id = session_id
        self.idempotency_key = idempotency_key
        self.calls: list[str] = []

    async def search_place(self, keyword: str, city: str | None = None) -> list[Place]:
        self.calls.append(keyword)
        if self.interrupt_after_decision:
            from travelpilot_infra.db.models.advisor import AdvisorRunEventModel

            async with AsyncSessionFactory() as db:
                decision = await db.scalar(select(AdvisorRunEventModel).where(
                    AdvisorRunEventModel.owner_id == self.owner_id,
                    AdvisorRunEventModel.session_id == self.session_id,
                    AdvisorRunEventModel.operation == "readonly_tool_decision",
                    AdvisorRunEventModel.metadata_json["task_ref"].as_string()
                    == hashlib.sha256(self.idempotency_key.encode()).hexdigest()[:24],
                ).limit(1))
                if decision is None or decision.status != "RECOVERABLE":
                    raise RuntimeError("model-selected tool decision was not committed before search")
                safe = decision.metadata_json
                if not isinstance(safe.get("action_ref"), str) or "arguments" in safe:
                    raise AssertionError("tool checkpoint must contain only the opaque approved action")
            os._exit(78)
        if self.retry and keyword == "公园":
            raise AssertionError("recovered destination search was repeated")
        if keyword == "公园":
            return [Place(
                provider="amap", provider_id="durable-park", name="成都公园",
                address="成都本地评测区", category="公园", latitude=30.67,
                longitude=104.06, city_code="510100",
            )]
        if keyword == "美食":
            return [Place(
                provider="amap", provider_id="durable-food", name="成都小吃街",
                address="成都本地评测区", category="餐饮服务", latitude=30.68,
                longitude=104.07, city_code="510100",
            )]
        return []


def _stub_stats(url: str) -> dict:
    with urlopen(url, timeout=2) as response:
        return json.loads(response.read())


async def _child(args: argparse.Namespace) -> int:
    from travelpilot_api.advisor import destination_service

    owner_id, session_id = UUID(args.owner_id), UUID(args.session_id)
    key = args.idempotency_key
    maps = AmapProvider(
        retry=args.phase == "retry",
        interrupt_after_decision=args.phase == "decision-interrupt",
        owner_id=owner_id, session_id=session_id, idempotency_key=key,
    )
    if args.phase == "interrupt":
        original_writer = destination_service.search_evidence_checkpoint_writer

        def interrupt_after_search_commit(**kwargs):
            writer = original_writer(**kwargs)

            async def write_then_exit(evidence):
                await writer(evidence)
                async with AsyncSessionFactory() as check_db:
                    task_ref = hashlib.sha256(
                        kwargs["task_id"].encode("utf-8")
                    ).hexdigest()[:24]
                    saved = await check_db.scalar(select(AdvisorRunEventModel.id).where(
                        AdvisorRunEventModel.owner_id == owner_id,
                        AdvisorRunEventModel.session_id == session_id,
                        AdvisorRunEventModel.operation == "map_search_snapshot",
                        AdvisorRunEventModel.metadata_json["task_ref"].as_string() == task_ref,
                    ).limit(1))
                if saved is None:
                    raise RuntimeError("destination evidence checkpoint was not committed")
                os._exit(77)

            return write_then_exit

        destination_service.search_evidence_checkpoint_writer = interrupt_after_search_commit

    async with AsyncSessionFactory() as db:
        user = await db.get(UserModel, owner_id)
        row = await db.get(AdvisorSessionModel, session_id)
        if user is None or row is None:
            raise RuntimeError("temporary owner or exploration session is missing")
        user_goal = (
            "只想去公园"
            if args.phase in {"decision-interrupt", "decision-retry"}
            else "推荐适合周末出行的城市，喜欢自然和当地美食"
        )
        result = await destination_service.start_destination_recommendation_from_chat(
            db=db,
            row=row,
            user=user,
            maps=maps,
            user_goal=user_goal,
            idempotency_key=key,
            output_token_budget=128,
        )
        if args.phase == "interrupt":
            raise AssertionError("interrupt point was not reached")

        assistant_messages = [item for item in result.messages if item.role == "assistant"]
        if len(assistant_messages) != 1:
            raise AssertionError(f"expected one assistant reply, got {len(assistant_messages)}")
        payload = assistant_messages[0].payload or {}
        recommendations = payload.get("recommendations", [])
        provider_ids = {
            place.get("provider_id")
            for item in recommendations
            for place in item.get("places", [])
        }
        expected_provider_ids = (
            {"durable-park"} if args.phase == "decision-retry"
            else {"durable-park", "durable-food"}
        )
        if provider_ids != expected_provider_ids:
            raise AssertionError(f"recovered result lost map evidence: {provider_ids!r}")
        if payload.get("response_to_idempotency_key") != key:
            raise AssertionError("assistant reply is not linked to the original request")
        expected_calls = ["公园"] if args.phase == "decision-retry" else ["美食"]
        if maps.calls != expected_calls:
            raise AssertionError(f"retry called the wrong unfinished query: {maps.calls!r}")
        print(json.dumps({
            "phase": "retry",
            "provider_calls": maps.calls,
            "recovered_and_new_provider_ids": sorted(provider_ids),
            "recommendation_count": len(recommendations),
            "formal_trip_id": result.trip_id,
            "assistant_reply_count": len(assistant_messages),
        }, ensure_ascii=True))
    return 0


async def _parent() -> None:
    owner_id, session_id = uuid4(), uuid4()
    key = f"dest-retry-{uuid4().hex[:24]}"
    email = f"destination-retry-{uuid4().hex[:16]}@example.invalid"
    username = f"d{uuid4().hex[:20]}"
    now = datetime.now(UTC)
    async with AsyncSessionFactory() as db:
        db.add(UserModel(
            id=owner_id, email=email, username=username, password_hash="verifier-only",
            display_name="Destination recovery verifier", is_active=True,
            email_verified=True, created_at=now,
        ))
        await db.commit()
        db.add(AdvisorSessionModel(
            id=session_id, owner_id=owner_id, mode="discover", destination="",
            pace_level=3, preference_text="", candidates=[],
            context={
                "mode": "discover", "revision": 0,
                "destination_discovery_city": "成都",
                "planning_inputs": {"hard_constraints": []},
            },
            status="ACTIVE", created_at=now, updated_at=now,
        ))
        await db.commit()
        db.add(AdvisorMessageModel(
            session_id=session_id, owner_id=owner_id, role="user",
            content="推荐适合周末出行的城市，喜欢自然和当地美食",
            idempotency_key=key, payload={}, created_at=now,
        ))
        await db.commit()

    child_env = dict(os.environ)
    child_env.update({
        "MAPS_PROVIDER": "amap",
        "AMAP_API_KEY": "local-durable-recovery-fixture",
        "LLM_API_KEY": "",
        "LLM_MODEL": "",
        "LLM_BASE_URL": "",
        "SMTP_HOST": "",
        "SMTP_FROM_EMAIL": "",
        "FORCE_DEVELOPMENT_EMAIL_OUTBOX": "true",
        "PYTHONIOENCODING": "utf-8",
    })
    common = [
        sys.executable, str(Path(__file__).resolve()), "--child",
        "--owner-id", str(owner_id), "--session-id", str(session_id),
        "--idempotency-key", key,
    ]
    try:
        crashed = await asyncio.to_thread(subprocess.run,
            [*common, "--phase", "interrupt"], cwd=Path(__file__).resolve().parents[1],
            env=child_env, capture_output=True, text=True, encoding="utf-8", timeout=45, check=False,
        )
        if crashed.returncode != 77:
            raise RuntimeError(
                f"first child should exit after checkpoint commit with 77; "
                f"code={crashed.returncode}; stderr={crashed.stderr[-1500:]}"
            )

        async with AsyncSessionFactory() as db:
            task_ref = hashlib.sha256(f"destination:{key}".encode()).hexdigest()[:24]
            persisted = (await db.scalars(select(AdvisorRunEventModel).where(
                AdvisorRunEventModel.owner_id == owner_id,
                AdvisorRunEventModel.session_id == session_id,
                AdvisorRunEventModel.operation == "map_search_snapshot",
                AdvisorRunEventModel.metadata_json["task_ref"].as_string() == task_ref,
            ))).all()
            if len(persisted) != 1 or persisted[0].status != "RECOVERABLE":
                raise AssertionError("first search evidence was not durably committed")
            snapshot = persisted[0].metadata_json.get("search", {})
            places = snapshot.get("places", [])
            if not places or places[0].get("provider") != "amap":
                raise AssertionError("persisted search checkpoint has the wrong provider scope")

        recovered = await asyncio.to_thread(subprocess.run,
            [*common, "--phase", "retry"], cwd=Path(__file__).resolve().parents[1],
            env=child_env, capture_output=True, text=True, encoding="utf-8", timeout=60, check=False,
        )
        if recovered.returncode != 0:
            raise RuntimeError(
                f"fresh process failed to recover destination discovery; "
                f"code={recovered.returncode}; stderr={recovered.stderr[-2000:]}"
            )
        result = json.loads(recovered.stdout.strip().splitlines()[-1])
        if result["formal_trip_id"] is not None or result["assistant_reply_count"] != 1:
            raise AssertionError(f"unexpected write/reply state: {result!r}")

        async with AsyncSessionFactory() as db:
            messages = (await db.scalars(select(AdvisorMessageModel).where(
                AdvisorMessageModel.session_id == session_id,
                AdvisorMessageModel.owner_id == owner_id,
            ))).all()
            replies = [item for item in messages if item.role == "assistant"]
            users = [item for item in messages if item.role == "user"]
            checkpoints = (await db.scalars(select(AdvisorRunEventModel).where(
                AdvisorRunEventModel.owner_id == owner_id,
                AdvisorRunEventModel.session_id == session_id,
                AdvisorRunEventModel.operation == "map_search_snapshot",
            ))).all()
            if len(users) != 1 or users[0].idempotency_key != key or len(replies) != 1:
                raise AssertionError("retry duplicated or lost the linked conversation messages")
            if replies[0].payload.get("response_to_idempotency_key") != key:
                raise AssertionError("recovered reply linkage is incorrect")
            if len(checkpoints) != 2:
                raise AssertionError(f"expected one durable checkpoint per search, got {len(checkpoints)}")

        # Recover a model-selected tool action after its checkpoint commits but
        # before the maps fixture returns any evidence.
        decision_session_id = uuid4()
        decision_key = f"dest-decision-{uuid4().hex[:20]}"
        async with AsyncSessionFactory() as db:
            db.add(AdvisorSessionModel(
                id=decision_session_id, owner_id=owner_id, mode="discover", destination="",
                pace_level=3, preference_text="", candidates=[],
                context={
                    "mode": "discover", "revision": 0,
                    "destination_discovery_city": "成都",
                    "planning_inputs": {"hard_constraints": []},
                },
                status="ACTIVE", created_at=now, updated_at=now,
            ))
            await db.commit()
            db.add(AdvisorMessageModel(
                session_id=decision_session_id, owner_id=owner_id, role="user",
                content="只想去公园", idempotency_key=decision_key, payload={}, created_at=now,
            ))
            await db.commit()

        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            stub_port = int(sock.getsockname()[1])
        stub_url = f"http://127.0.0.1:{stub_port}"
        stub_script = Path(__file__).resolve().parents[1] / "scripts" / "local_openai_chat_stub.py"
        stub = await asyncio.create_subprocess_exec(
            sys.executable, str(stub_script), "--port", str(stub_port),
            cwd=Path(__file__).resolve().parents[1],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        model_env = dict(child_env)
        model_env.update({
            "LLM_API_KEY": "local-test-key", "LLM_MODEL": "local-stub",
            "LLM_BASE_URL": f"{stub_url}/v1",
            "LLM_MAX_TOKENS_PER_TURN": "128",
            "LLM_MAX_OUTPUT_TOKENS_PER_SESSION": "1024",
        })
        try:
            for _ in range(100):
                if stub.returncode is not None:
                    raise RuntimeError(f"local destination model stub exited with {stub.returncode}")
                try:
                    await asyncio.to_thread(_stub_stats, f"{stub_url}/stats")
                    break
                except OSError:
                    await asyncio.sleep(0.05)
            else:
                raise TimeoutError("local destination model stub did not start")

            decision_args = [
                sys.executable, str(Path(__file__).resolve()), "--child",
                "--owner-id", str(owner_id), "--session-id", str(decision_session_id),
                "--idempotency-key", decision_key,
            ]
            interrupted_decision = await asyncio.to_thread(
                subprocess.run,
                [*decision_args, "--phase", "decision-interrupt"],
                cwd=Path(__file__).resolve().parents[1], env=model_env,
                capture_output=True, text=True, encoding="utf-8", timeout=45, check=False,
            )
            if interrupted_decision.returncode != 78:
                raise RuntimeError(
                    "decision crash hook did not fire after checkpoint commit; "
                    f"code={interrupted_decision.returncode}; "
                    f"stderr={interrupted_decision.stderr[-1500:]}"
                )
            stats_before_retry = await asyncio.to_thread(_stub_stats, f"{stub_url}/stats")
            if stats_before_retry["agent_decisions"] != 1:
                raise AssertionError(f"expected one model decision before crash: {stats_before_retry!r}")

            decision_task_ref = hashlib.sha256(decision_key.encode()).hexdigest()[:24]
            async with AsyncSessionFactory() as db:
                decision_rows = (await db.scalars(select(AdvisorRunEventModel).where(
                    AdvisorRunEventModel.owner_id == owner_id,
                    AdvisorRunEventModel.session_id == decision_session_id,
                    AdvisorRunEventModel.operation == "readonly_tool_decision",
                    AdvisorRunEventModel.metadata_json["task_ref"].as_string() == decision_task_ref,
                ))).all()
                if len(decision_rows) != 1 or decision_rows[0].status != "RECOVERABLE":
                    raise AssertionError("approved destination action was not checkpointed")
                decision_metadata = decision_rows[0].metadata_json
                if "arguments" in decision_metadata or "city" in decision_metadata:
                    raise AssertionError("decision checkpoint leaked raw tool arguments")
                search_snapshots = (await db.scalars(select(AdvisorRunEventModel.id).where(
                    AdvisorRunEventModel.owner_id == owner_id,
                    AdvisorRunEventModel.session_id == decision_session_id,
                    AdvisorRunEventModel.operation == "map_search_snapshot",
                    AdvisorRunEventModel.metadata_json["task_ref"].as_string() == decision_task_ref,
                ))).all()
                if search_snapshots:
                    raise AssertionError("provider evidence exists despite pre-provider interruption")

            retried_decision = await asyncio.to_thread(
                subprocess.run,
                [*decision_args, "--phase", "decision-retry"],
                cwd=Path(__file__).resolve().parents[1], env=model_env,
                capture_output=True, text=True, encoding="utf-8", timeout=60, check=False,
            )
            if retried_decision.returncode != 0:
                raise RuntimeError(
                    "fresh process failed to recover selected destination action; "
                    f"code={retried_decision.returncode}; "
                    f"stderr={retried_decision.stderr[-2000:]}"
                )
            decision_result = json.loads(retried_decision.stdout.strip().splitlines()[-1])
            stats_after_retry = await asyncio.to_thread(_stub_stats, f"{stub_url}/stats")
            if stats_after_retry["agent_decisions"] != 1:
                raise AssertionError(
                    "same approved action was not recovered; retry made another model decision: "
                    f"{stats_after_retry!r}"
                )
            if decision_result["formal_trip_id"] is not None:
                raise AssertionError("destination discovery wrote a formal trip")
            async with AsyncSessionFactory() as db:
                messages = (await db.scalars(select(AdvisorMessageModel).where(
                    AdvisorMessageModel.session_id == decision_session_id,
                    AdvisorMessageModel.owner_id == owner_id,
                ))).all()
                user_rows = [item for item in messages if item.role == "user"]
                assistant_rows = [item for item in messages if item.role == "assistant"]
                if len(user_rows) != 1 or len(assistant_rows) != 1:
                    raise AssertionError("model-decision recovery duplicated or lost messages")
                if assistant_rows[0].payload.get("response_to_idempotency_key") != decision_key:
                    raise AssertionError("recovered model-decision response linkage is incorrect")
            print(
                "PASS: model-selected destination action survived process exit after its "
                "opaque checkpoint and before provider evidence; same-key retry reused it "
                "without another model decision."
            )
        finally:
            stub.terminate()
            try:
                await asyncio.wait_for(stub.wait(), timeout=5)
            except TimeoutError:
                stub.kill()
                await asyncio.wait_for(stub.wait(), timeout=5)

        print("PASS: destination discovery survived a process crash after its first committed search.")
        print(json.dumps({
            "provider_mode": "LOCAL_AMAP_SHAPED_FIXTURE",
            "first_process_exit_code": crashed.returncode,
            "checkpoint_count": 1,
            **result,
            "final_search_checkpoint_count": 2,
            "user_message_count": 1,
            "assistant_reply_count": 1,
            "temporary_account_deleted": True,
            "external_calls": 0,
        }, ensure_ascii=True, indent=2))
    finally:
        async with AsyncSessionFactory() as db:
            await db.execute(delete(UserModel).where(UserModel.id == owner_id))
            await db.commit()
        async with AsyncSessionFactory() as db:
            remaining_owner = await db.get(UserModel, owner_id)
            if remaining_owner is not None:
                raise AssertionError("temporary destination-recovery account was not deleted")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--child", action="store_true")
    parser.add_argument(
        "--phase", choices=["interrupt", "retry", "decision-interrupt", "decision-retry"],
    )
    parser.add_argument("--owner-id")
    parser.add_argument("--session-id")
    parser.add_argument("--idempotency-key")
    args = parser.parse_args()
    if args.child:
        if not all([args.phase, args.owner_id, args.session_id, args.idempotency_key]):
            parser.error("child mode requires phase, owner, session and idempotency key")
        raise SystemExit(asyncio.run(_child(args)))
    asyncio.run(_parent())


if __name__ == "__main__":
    main()
