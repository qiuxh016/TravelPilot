"""Bounded live-model v8 matched schedule evaluation (B1/B2/B3).

The fixed workflow is run from its pinned revision against an isolated
disposable database. B2/B3 use the current constrained runtime with local
synthetic tools. Three independent loopback proxies enforce per-arm budgets;
no provider path bypasses those proxies.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from urllib.request import urlopen
from uuid import uuid4

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]
DATASET = ROOT / "tests/evaluation/cases/advisor_matched_schedule_b1_v1.json"
B1_REVISION = "0c4a8998896bc5b27877ff9f49cfbf77ec0128d7"
TOTAL_REQUEST_CAP = 48
TOTAL_OUTPUT_TOKEN_CAP = 12_288
PROVIDER_REQUEST_OUTPUT_CAP = 256
ARM_REQUEST_CAPS = {"B1_FIXED_WORKFLOW": 24, "B2_AGENT_LOOP": 12, "B3_VERIFIER_RECOVERY": 12}
ARM_OUTPUT_BUDGETS = {name: count * PROVIDER_REQUEST_OUTPUT_CAP for name, count in ARM_REQUEST_CAPS.items()}


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _get_json(url: str) -> dict[str, Any]:
    with urlopen(url, timeout=3) as response:
        return json.loads(response.read())


async def _wait_http(url: str, process: asyncio.subprocess.Process) -> dict[str, Any]:
    for _ in range(120):
        if process.returncode is not None:
            raise RuntimeError(f"local process exited before ready ({process.returncode})")
        try:
            return await asyncio.to_thread(_get_json, url)
        except OSError:
            await asyncio.sleep(0.25)
    raise TimeoutError("local process did not become ready")


async def _stop(process: asyncio.subprocess.Process | None) -> None:
    if process is None or process.returncode is not None:
        return
    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=10)
    except TimeoutError:
        process.kill()
        await asyncio.wait_for(process.wait(), timeout=5)


def _docker(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *args], cwd=ROOT, text=True, capture_output=True,
        check=check, timeout=60,
    )


def _git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=ROOT, text=True, capture_output=True,
        check=check, timeout=60,
    )


async def _start_proxy(
    *, port: int, upstream: str, request_cap: int, token_cap: int,
) -> asyncio.subprocess.Process:
    env = dict(os.environ)
    env["EVAL_MODEL_UPSTREAM_BASE_URL"] = upstream
    return await asyncio.create_subprocess_exec(
        sys.executable, str(ROOT / "tests/evaluation/bounded_model_proxy.py"),
        "--host", "127.0.0.1", "--port", str(port),
        "--max-requests", str(request_cap),
        "--max-requested-output-tokens", str(token_cap),
        "--max-output-tokens-per-request", str(PROVIDER_REQUEST_OUTPUT_CAP),
        cwd=ROOT, env=env,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
    )


def _validate_frozen_inputs(dataset: dict[str, Any], output: Path) -> None:
    if dataset.get("status") != "FROZEN_FOR_PREAUTHORIZATION" or len(dataset.get("cases", [])) != 4:
        raise RuntimeError("frozen four-case v8 dataset contract failed")
    if output.exists():
        raise FileExistsError(f"refusing to overwrite evaluation report: {output}")
    if sum(ARM_REQUEST_CAPS.values()) != TOTAL_REQUEST_CAP:
        raise RuntimeError("arm request allocations do not equal the authorized ceiling")
    if sum(ARM_OUTPUT_BUDGETS.values()) != TOTAL_OUTPUT_TOKEN_CAP:
        raise RuntimeError("arm token allocations do not equal the authorized ceiling")


async def _run_runtime_arm(
    *, dataset: dict[str, Any], arm: str, base_url: str, api_key: str, model: str,
) -> dict[str, Any]:
    from tests.evaluation.advisor_matched_schedule_runtime_preflight import _adapt_case
    from tests.evaluation.advisor_unseen_multiturn_eval import _run_turn

    quota = ARM_REQUEST_CAPS[arm]
    budget = {"requests": 0, "output_tokens": 0, "request_cap": quota,
              "exhausted": 0, "reported_overrun": 0}
    results = []
    runtime_arm = "B2_AGENT_LOOP" if arm == "B2_AGENT_LOOP" else "B3_VERIFIER_RECOVERY"
    for frozen_case in dataset["cases"]:
        case = _adapt_case(frozen_case)
        history: list[dict[str, Any]] = []
        turns = []
        for index, turn in enumerate(case["turns"]):
            history.append({"role": "user", "content": turn["user"]})
            row = await _run_turn(
                case=case, turn=turn, turn_index=index, arm=runtime_arm,
                history=history, api_key=api_key, model=model, base_url=base_url,
                output_token_cap=PROVIDER_REQUEST_OUTPUT_CAP, global_budget=budget,
            )
            turns.append(row)
            action = row.get("action") or {}
            history.append({"role": "assistant", "action": action,
                            "content": action.get("summary", "")})
            history.extend(row.get("tool_observations", []))
            if budget["exhausted"] or budget["reported_overrun"]:
                break
        results.append({"case_id": frozen_case["id"], "turns": turns,
                        "turn_sequence_completed": len(turns) == 2,
                        "task_all_turns_matched": len(turns) == 2 and all(
                            item.get("expected_action_match") is True for item in turns
                        ),
                        "formal_write_detected": any(
                            item.get("formal_write") is not False for item in turns
                        )})
        if budget["exhausted"] or budget["reported_overrun"]:
            break
    return {
        "arm": arm,
        "case_count_completed": sum(item["turn_sequence_completed"] for item in results),
        "complete_task_matches": sum(item["task_all_turns_matched"] for item in results),
        "action_matches": sum(
            turn.get("expected_action_match") is True
            for case in results for turn in case["turns"]
        ),
        "scorable_decisions": sum(
            turn.get("expected_action_match") is not None
            for case in results for turn in case["turns"]
        ),
        "invalid_model_actions": sum(
            int(turn.get("invalid_model_actions", 0))
            for case in results for turn in case["turns"]
        ),
        "provider_requests_runner_count": budget["requests"],
        "output_tokens_reported": budget["output_tokens"],
        "formal_writes": 0, "request_budget_exhausted": bool(budget["exhausted"]),
        "provider_reported_token_overrun": bool(budget["reported_overrun"]),
        "cases": results,
    }


async def _run_b1(
    *, dataset: dict[str, Any], base_url: str, model: str, upstream_base_url: str,
    output_dir: Path, proxy_port: int,
) -> dict[str, Any]:
    from travelpilot_api import build_info
    from travelpilot_api.settings import settings as evaluator_settings
    from tests.evaluation import advisor_product_live_eval as product_eval

    evaluator_settings.llm_base_url = base_url
    evaluator_settings.llm_model = model
    original_runtime_check = product_eval._verify_api_runtime
    original_caps_check = product_eval._verify_server_token_caps
    original_evaluator_base_url = evaluator_settings.llm_base_url
    original_evaluator_model = evaluator_settings.llm_model

    def verify_pinned_health(response, _fingerprint):
        if response.status_code != 200:
            raise RuntimeError(f"B1_HEALTH_HTTP_{response.status_code}")
        return f"pinned-legacy:{B1_REVISION}"

    def verify_legacy_caps(response, *, max_output_tokens_per_turn,
                           max_output_tokens_per_session, expected_model=None):
        if response.status_code != 200:
            raise RuntimeError(f"B1_STATUS_HTTP_{response.status_code}")
        body = response.json()
        output_limit = body.get("output_token_limit")
        if body.get("llm_status") != "CONFIGURED" or not isinstance(output_limit, int):
            raise RuntimeError("B1_LEGACY_STATUS_INVALID")
        if output_limit > max_output_tokens_per_turn:
            raise RuntimeError("B1_OUTPUT_LIMIT_EXCEEDS_AUTHORIZATION")
        if expected_model and body.get("model_name") != expected_model:
            raise RuntimeError("B1_MODEL_MISMATCH")
        return {"model_name": body.get("model_name"),
                "output_token_limit": output_limit,
                "session_output_token_limit": max_output_tokens_per_session,
                "legacy_status_schema": True}

    product_eval._verify_api_runtime = verify_pinned_health
    product_eval._verify_server_token_caps = verify_legacy_caps
    reports = []
    try:
        for case in dataset["cases"]:
            dates = case["state"]["trip_dates"]
            scenario = {
                "destination": case["state"]["destination"],
                "turn_inputs": tuple(case["turns"]),
                "saved_places": tuple((p["name"], p["latitude"], p["longitude"])
                                       for p in case["state"]["saved_places"]),
                "confirmed_plan_setup": True,
            }
            path = output_dir / f"b1_{case['id']}.json"
            report = await product_eval.run_eval(
                base_url=base_url.replace("/v1", ""), output=path,
                server_output_cap=512, server_session_cap=1024,
                trip_start_date=date.fromisoformat(dates[0]),
                trip_end_date=date.fromisoformat(dates[1]),
                pace_level=case["state"]["pace_level"],
                scenario_id=case["id"], scenario_override=scenario,
                assessment_scenario_id="schedule-two-turn",
                model_is_bounded_proxy=True,
            )
            report["arm"] = "B1_FIXED_WORKFLOW"
            report["pinned_revision"] = B1_REVISION
            report["legacy_api_health_validation"] = "HTTP 200; detached worktree pinned to revision"
            report["effective_provider_output_cap"] = PROVIDER_REQUEST_OUTPUT_CAP
            path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            reports.append(report)
            checkpoint = {
                "arm": "B1_FIXED_WORKFLOW", "pinned_revision": B1_REVISION,
                "completed_cases": len(reports),
                "case_reports": [str(output_dir / f"b1_{item['scenario_id']}.json") for item in reports],
                "proxy_audit": _get_json(f"http://127.0.0.1:{proxy_port}/budget"),
            }
            (output_dir / "b1_checkpoint.json").write_text(
                json.dumps(checkpoint, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
            )
            if not report["temporary_account_deleted"]:
                raise RuntimeError("B1 temporary account cleanup failed")
            if report.get("resources", {}).get("formal_write_prevented") is not True:
                raise RuntimeError("B1 formal itinerary changed during evaluation")
    finally:
        product_eval._verify_api_runtime = original_runtime_check
        product_eval._verify_server_token_caps = original_caps_check
        evaluator_settings.llm_base_url = original_evaluator_base_url
        evaluator_settings.llm_model = original_evaluator_model
    return {
        "arm": "B1_FIXED_WORKFLOW", "pinned_revision": B1_REVISION,
        "case_count_completed": len(reports), "formal_writes": 0,
        "temporary_accounts_deleted": all(item["temporary_account_deleted"] for item in reports),
        "cases": reports,
    }


async def run_live(output: Path, *, skip_b1: bool = False) -> dict[str, Any]:
    from travelpilot_api.settings import settings

    dataset = json.loads(DATASET.read_text(encoding="utf-8"))
    output = output.resolve()
    _validate_frozen_inputs(dataset, output)
    if not settings.llm_api_key.strip() or not settings.llm_model.strip():
        raise RuntimeError("DeepSeek credentials/model are not configured")
    upstream = urlsplit(settings.llm_base_url)
    if (upstream.scheme != "https" or upstream.hostname != "api.deepseek.com"
            or upstream.path.rstrip("/") not in {"", "/v1"} or upstream.query
            or upstream.fragment or upstream.username or upstream.password):
        raise RuntimeError("configured model endpoint is not DeepSeek HTTPS")
    if PROVIDER_REQUEST_OUTPUT_CAP > 512:
        raise RuntimeError("per-request proxy cap exceeds authorization")

    arms: dict[str, Any] = {}
    proxy_audit: dict[str, Any] = {}
    upstream_base_url = settings.llm_base_url
    temporary_root = Path(tempfile.mkdtemp(prefix="travelpilot-v8-"))
    worktree = temporary_root / "b1-pinned"
    db_name = f"tp_eval_{secrets.token_hex(5)}"
    db_url = f"postgresql+asyncpg://travelpilot:travelpilot@127.0.0.1:5432/{db_name}"
    base_env = {key: value for key, value in dotenv_values(ROOT / ".env").items()
                if isinstance(value, str)}
    base_env.update(os.environ)
    db_created = False
    worktree_created = False
    redis_db_claimed = False
    try:
        if skip_b1:
            # A previous B1 attempt was bounded to its full 24-request/6144-token
            # share but its report was lost during local cleanup. Reserve that
            # entire share in accounting and do not repeat the paid requests.
            proxy_audit["B1_FIXED_WORKFLOW"] = {
                "status": "FIRST_ATTEMPT_REPORT_NOT_RETAINED",
                "admitted_requests_upper_bound": ARM_REQUEST_CAPS["B1_FIXED_WORKFLOW"],
                "requested_output_tokens_upper_bound": ARM_OUTPUT_BUDGETS["B1_FIXED_WORKFLOW"],
            }
            arms["B1_FIXED_WORKFLOW"] = {
                "status": "UNSCORED_FIRST_ATTEMPT",
                "case_count_completed": None,
                "formal_writes": 0,
                "note": "B1 completed before a proxy startup bug stopped the next arm; its temporary per-case report directory was removed. Conservatively account for its entire authorized arm budget; do not infer its score.",
            }
        else:
            redis_empty = _docker("exec", "travelpilot-redis-1", "redis-cli", "-n", "14", "DBSIZE").stdout.strip()
            if redis_empty != "0":
                raise RuntimeError("isolated Redis DB 14 is not empty; refusing to use it")
            redis_db_claimed = True
            _docker("exec", "travelpilot-postgres-1", "psql", "-U", "travelpilot", "-d", "postgres",
                    "-v", "ON_ERROR_STOP=1", "-c", f'CREATE DATABASE "{db_name}"')
            db_created = True
            _git("worktree", "add", "--detach", str(worktree), B1_REVISION)
            worktree_created = True

            b1_env = dict(base_env)
            b1_env.update({"DATABASE_URL": db_url, "REDIS_URL": "redis://127.0.0.1:6380/14",
                           "MAPS_PROVIDER": "fake", "WEATHER_PROVIDER": "fake",
                           "AMAP_API_KEY": "", "LLM_MAX_TOKENS_PER_TURN": "512",
                           "LLM_MAX_OUTPUT_TOKENS_PER_SESSION": "1024",
                           "SMTP_HOST": "", "EMAIL_SEND_LIMIT_PER_HOUR": "10000"})
            b1_paths = [worktree / "apps/api/src", worktree / "packages/core/src",
                        worktree / "packages/infrastructure/src", worktree / "packages/infra/src"]
            b1_env["PYTHONPATH"] = os.pathsep.join(map(str, b1_paths))
            b1_env["DATABASE_URL"] = db_url
            migration = subprocess.run(
                [str(ROOT / ".venv/Scripts/python.exe"), "-m", "alembic", "upgrade", "head"],
                cwd=worktree, env=b1_env, text=True, capture_output=True, timeout=180,
            )
            if migration.returncode:
                raise RuntimeError(f"B1 isolated database migration failed: {migration.stderr[-500:]}")

            b1_proxy_port, api_port = _free_port(), _free_port()
            b1_proxy = await _start_proxy(port=b1_proxy_port, upstream=upstream_base_url,
                                          request_cap=ARM_REQUEST_CAPS["B1_FIXED_WORKFLOW"],
                                          token_cap=ARM_OUTPUT_BUDGETS["B1_FIXED_WORKFLOW"])
            api = None
            try:
                await _wait_http(f"http://127.0.0.1:{b1_proxy_port}/budget", b1_proxy)
                b1_env.update({"LLM_API_KEY": settings.llm_api_key,
                               "LLM_MODEL": settings.llm_model,
                               "LLM_BASE_URL": f"http://127.0.0.1:{b1_proxy_port}/v1"})
                api = await asyncio.create_subprocess_exec(
                    str(ROOT / ".venv/Scripts/python.exe"), "-m", "uvicorn",
                    "travelpilot_api.main:app", "--host", "127.0.0.1", "--port", str(api_port),
                    cwd=worktree, env=b1_env, stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                health = await _wait_http(f"http://127.0.0.1:{api_port}/health", api)
                if not isinstance(health, dict):
                    raise RuntimeError("B1 pinned API health check failed")
                arms["B1_FIXED_WORKFLOW"] = await _run_b1(
                    dataset=dataset, base_url=f"http://127.0.0.1:{api_port}",
                    model=settings.llm_model, upstream_base_url=upstream_base_url,
                    output_dir=output.with_name(f"{output.stem}_B1_cases"),
                    proxy_port=b1_proxy_port,
                )
                proxy_audit["B1_FIXED_WORKFLOW"] = _get_json(f"http://127.0.0.1:{b1_proxy_port}/budget")
            finally:
                await _stop(api)
                await _stop(b1_proxy)

        # B2 and B3 each have a separate enforced share of the same total cap.
        for arm in ("B2_AGENT_LOOP", "B3_VERIFIER_RECOVERY"):
            proxy_port = _free_port()
            proxy = await _start_proxy(port=proxy_port, upstream=upstream_base_url,
                                       request_cap=ARM_REQUEST_CAPS[arm],
                                       token_cap=ARM_OUTPUT_BUDGETS[arm])
            try:
                await _wait_http(f"http://127.0.0.1:{proxy_port}/budget", proxy)
                arms[arm] = await _run_runtime_arm(
                    dataset=dataset, arm=arm, base_url=f"http://127.0.0.1:{proxy_port}/v1",
                    api_key=settings.llm_api_key, model=settings.llm_model,
                )
                proxy_audit[arm] = _get_json(f"http://127.0.0.1:{proxy_port}/budget")
                checkpoint = output.with_name(f"{output.stem}_{arm.lower()}.json")
                checkpoint.write_text(
                    json.dumps({"arm": arm, "result": arms[arm], "proxy_audit": proxy_audit[arm]},
                               ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
                )
            finally:
                await _stop(proxy)

        total_requests = sum(int(item.get("admitted_requests", item.get("admitted_requests_upper_bound", 0)))
                             for item in proxy_audit.values())
        total_reserved_tokens = sum(int(item.get("requested_output_tokens", item.get("requested_output_tokens_upper_bound", 0)))
                                    for item in proxy_audit.values())
        if total_requests > TOTAL_REQUEST_CAP or total_reserved_tokens > TOTAL_OUTPUT_TOKEN_CAP:
            raise RuntimeError("proxy audit exceeds the authorized aggregate budget")
        report = {
            "evaluation_id": "advisor_matched_schedule_b1_v8_live",
            "dataset_id": dataset["dataset_id"],
            "dataset_sha256": __import__("hashlib").sha256(DATASET.read_bytes()).hexdigest(),
            "provider_mode": "LIVE_DEEPSEEK_THROUGH_SEPARATE_BUDGETED_LOOPBACK_PROXIES",
            "model": settings.llm_model,
            "arms": arms,
            "arm_definitions": {
                "B1_FIXED_WORKFLOW": f"Pinned fixed workflow API at {B1_REVISION} with isolated disposable database",
                "B2_AGENT_LOOP": "Current production constrained AgentRuntime with local synthetic tools; verifier/recovery disabled",
                "B3_VERIFIER_RECOVERY": "Same current AgentRuntime with deterministic verifier and bounded recovery",
            },
            "request_cap": TOTAL_REQUEST_CAP, "requests_used": total_requests,
            "per_provider_request_output_cap": PROVIDER_REQUEST_OUTPUT_CAP,
            "authorized_per_provider_request_max": 512,
            "aggregate_output_token_cap": TOTAL_OUTPUT_TOKEN_CAP,
            "aggregate_requested_output_reservation": total_reserved_tokens,
            "accounting_status": "CONSERVATIVE_UPPER_BOUND_INCLUDES_UNRECOVERABLE_B1_FIRST_ATTEMPT"
                if skip_b1 else "EXACT_PROXY_RESERVATIONS",
            "proxy_audit": proxy_audit,
            "formal_itinerary_writes": sum(int(item.get("formal_writes", 0)) for item in arms.values()),
            "maps_weather_mode": "LOCAL_SYNTHETIC_FIXTURES_ONLY",
            "run_at_utc": datetime.now(UTC).isoformat(),
            "limitations": [
                "Four cases are diagnostic only and do not establish statistical significance.",
                "B1 is an authenticated user-facing API; B2/B3 are production runtime action harnesses, so compare their task/action outcomes with this interface difference in mind.",
                "Each arm received an independently capped budget; unused budget was not transferred between arms.",
                "Model output token count is provider-reported; enforced request reservations are authoritative upper bounds.",
                "This does not include independent human response-quality ratings.",
            ],
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"output": str(output), "requests_used": total_requests,
                          "aggregate_reserved_tokens": total_reserved_tokens,
                          "arms": {key: value.get("case_count_completed") for key, value in arms.items()}},
                         ensure_ascii=False, indent=2))
        return report
    finally:
        # No successful run leaves an eval account because run_eval cleans up in finally.
        if worktree_created:
            _git("worktree", "remove", "--force", str(worktree), check=False)
            _git("worktree", "prune", check=False)
        if db_created:
            _docker("exec", "travelpilot-postgres-1", "psql", "-U", "travelpilot", "-d", "postgres",
                    "-v", "ON_ERROR_STOP=1", "-c",
                    f'SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = \'{db_name}\';',
                    check=False)
            _docker("exec", "travelpilot-postgres-1", "psql", "-U", "travelpilot", "-d", "postgres",
                    "-v", "ON_ERROR_STOP=1", "-c", f'DROP DATABASE IF EXISTS "{db_name}"', check=False)
        if redis_db_claimed:
            _docker("exec", "travelpilot-redis-1", "redis-cli", "-n", "14", "FLUSHDB", check=False)
        # The temporary directory contains only evaluator output for this run.
        import shutil
        shutil.rmtree(temporary_root, ignore_errors=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-live", action="store_true", help="required for real model calls")
    parser.add_argument("--skip-b1", action="store_true", help="resume only the unused B2/B3 shares after a lost B1 report")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.run_live:
        parser.error("--run-live is required")
    asyncio.run(run_live(args.output, skip_b1=args.skip_b1))


if __name__ == "__main__":
    main()
