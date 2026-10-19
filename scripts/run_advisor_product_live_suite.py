"""Run the frozen Advisor product suite against the configured live LLM.

The real model is used, while Maps and Weather are forcibly replaced by local
fixtures. Each scenario gets a disposable API process and temporary account.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import socket
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from travelpilot_api.settings import settings

from tests.evaluation.advisor_product_live_eval import SCENARIOS, run_eval

APP_MODULES = {
    "default": "travelpilot_api.main:app",
    "weather": "tests.evaluation.local_advisor_weather_app:app",
    "route-weather-failure": "tests.evaluation.advisor_route_weather_failure_app:app",
    "route-weather-weather-failure": "tests.evaluation.advisor_route_weather_weather_failure_app:app",
    "route-outage": "tests.evaluation.advisor_route_outage_app:app",
}
SCENARIO_ORDER = (
    "schedule-two-turn",
    "schedule-route-weather",
    "schedule-proposal-edit",
    "lunch-only",
    "lunch-weather-preparation",
    "lunch-weather-weather-outage",
    "mixed-attraction-lunch",
    "mixed-attraction-lunch-draft",
    "lodging-only",
    "general-trip-question",
    "weather-preparation",
    "route-weather-preparation",
    "route-weather-route-outage",
    "route-weather-weather-outage",
    "weather-indoor-search",
    "route-provider-outage",
)
DEFAULT_TOTAL_OUTPUT_TOKEN_CAP = 3584
MAX_SUPPORTED_TOTAL_OUTPUT_TOKEN_CAP = 10240


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _read_url(url: str) -> bytes:
    with urlopen(url, timeout=2) as response:
        return response.read()


async def _wait_http(url: str, process: asyncio.subprocess.Process) -> None:
    for _ in range(240):
        if process.returncode is not None:
            raise RuntimeError(f"isolated API exited before ready (code={process.returncode})")
        try:
            await asyncio.to_thread(_read_url, url)
            return
        except (OSError, URLError):
            await asyncio.sleep(0.25)
    raise TimeoutError("isolated API did not become ready within 60 seconds")


async def _stop(process: asyncio.subprocess.Process | None) -> None:
    if process is None or process.returncode is not None:
        return
    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=8)
    except TimeoutError:
        process.kill()
        await process.wait()


def _app_group(scenario_id: str) -> str:
    if scenario_id in {
        "weather-indoor-search", "weather-preparation",
        "lunch-weather-preparation", "route-weather-preparation",
        "schedule-route-weather",
    }:
        return "weather"
    if scenario_id == "route-weather-route-outage":
        return "route-weather-failure"
    if scenario_id in {"route-weather-weather-outage", "lunch-weather-weather-outage"}:
        return "route-weather-weather-failure"
    if scenario_id == "route-provider-outage":
        return "route-outage"
    return "default"


def _maximum_output_for_scenarios(
    scenarios: tuple[str, ...], per_turn_cap: int, session_cap: int,
) -> int:
    return sum(
        min(
            session_cap,
            per_turn_cap * len(SCENARIOS[scenario_id]["turn_inputs"]),
        )
        for scenario_id in scenarios
    )


def _scenario_summary(report: dict) -> dict:
    turns = report.get("turns", [])
    if not isinstance(turns, list):
        turns = []
    actions = []
    stages = []
    decisions = []
    for turn in turns:
        if not isinstance(turn, dict):
            continue
        for source, destination in (
            (turn.get("actions"), actions),
            (turn.get("stage_statuses"), stages),
            (turn.get("stage_decision_history"), decisions),
        ):
            if isinstance(source, list):
                destination.extend(item for item in source if isinstance(item, dict))
    def token_total(field: str) -> int:
        total = 0
        for turn in turns:
            usage = turn.get("model_usage") if isinstance(turn, dict) else None
            value = usage.get(field) if isinstance(usage, dict) else None
            if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                total += value
        return total

    output_tokens = token_total("output_tokens")
    input_tokens = token_total("input_tokens")
    latencies = [
        turn.get("request_latency_ms")
        for turn in turns if isinstance(turn, dict)
        if isinstance(turn.get("request_latency_ms"), (int, float))
        and not isinstance(turn.get("request_latency_ms"), bool)
        and turn["request_latency_ms"] >= 0
    ]
    return {
        "passed": report["product_assessment"]["passed"],
        "checks": report["product_assessment"]["checks"],
        "turn_count": len(turns),
        "output_tokens": output_tokens,
        "input_tokens": input_tokens,
        "request_latency_ms_per_turn": latencies,
        "tool_action_count": len(actions),
        "verified_tool_call_count": sum(action.get("status") == "VERIFIED" for action in actions),
        "failed_tool_call_count": sum(action.get("status") == "FAILED" for action in actions),
        "stage_success_count": sum(stage.get("status") == "SUCCEEDED" for stage in stages),
        "stage_failure_count": sum(stage.get("status") == "FAILED" for stage in stages),
        "model_stage_decision_count": sum(
            decision.get("decision_source") == "MODEL" for decision in decisions
        ),
        "rule_recovery_decision_count": sum(
            decision.get("decision_source") == "RULE_RECOVERY" for decision in decisions
        ),
        "clarification_turn_count": sum(
            bool(turn.get("pending_question"))
            or turn.get("task_status") == "WAITING_USER"
            for turn in turns if isinstance(turn, dict)
        ),
        "account_deleted": report["temporary_account_deleted"],
        "formal_write_prevented": report.get("resources", {}).get("formal_write_prevented"),
        "failure": report["failure"],
    }


def _aggregate_product_metrics(scenarios: list[dict]) -> dict:
    fields = (
        "input_tokens", "output_tokens", "tool_action_count",
        "verified_tool_call_count", "failed_tool_call_count",
        "stage_success_count", "stage_failure_count",
        "model_stage_decision_count", "rule_recovery_decision_count",
        "clarification_turn_count",
    )
    totals = {
        field: sum(row.get(field, 0) for row in scenarios)
        for field in fields
    }
    tool_count = totals["tool_action_count"]
    totals["verified_tool_call_rate"] = (
        round(totals["verified_tool_call_count"] / tool_count, 4)
        if tool_count else None
    )
    latencies = sorted(
        latency
        for row in scenarios
        for latency in row.get("request_latency_ms_per_turn", [])
        if isinstance(latency, (int, float)) and not isinstance(latency, bool)
    )
    totals["latency_sample_count"] = len(latencies)
    totals["mean_request_latency_ms"] = (
        round(sum(latencies) / len(latencies), 2) if latencies else None
    )
    totals["p95_request_latency_ms"] = (
        latencies[max(0, math.ceil(0.95 * len(latencies)) - 1)] if latencies else None
    )
    totals["scenario_count"] = len(scenarios)
    totals["turn_count"] = sum(row.get("turn_count", 0) for row in scenarios)
    return totals


async def _run(
    output: Path,
    scenarios: tuple[str, ...],
    per_turn_cap: int,
    session_cap: int,
    aggregate_cap: int,
) -> dict:
    if not settings.llm_api_key or not settings.llm_model:
        raise RuntimeError("LLM_API_KEY and LLM_MODEL must be configured in .env")
    if output.exists():
        raise FileExistsError(f"refusing to overwrite report: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    reports = []
    api: asyncio.subprocess.Process | None = None
    active_group = None
    try:
        for scenario_id in scenarios:
            if scenario_id not in SCENARIOS:
                raise ValueError(f"unknown scenario: {scenario_id}")
            group = _app_group(scenario_id)
            if group != active_group:
                await _stop(api)
                port = _free_port()
                env = dict(os.environ)
                paths = [
                    ROOT / "apps/api/src", ROOT / "packages/core/src",
                    ROOT / "packages/infrastructure/src", ROOT / "packages/infra/src", ROOT,
                ]
                env["PYTHONPATH"] = os.pathsep.join(
                    [*(str(path) for path in paths), *([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])]
                )
                env.update({
                    "MAPS_PROVIDER": "fake",
                    "WEATHER_PROVIDER": "fake",
                    "AMAP_API_KEY": "",
                    "FORCE_DEVELOPMENT_EMAIL_OUTBOX": "true",
                    "REDIS_URL": "redis://localhost:6379/15",
                    "LLM_MAX_TOKENS_PER_TURN": str(per_turn_cap),
                    "LLM_MAX_OUTPUT_TOKENS_PER_SESSION": str(session_cap),
                    "SMTP_HOST": "",
                })
                api = await asyncio.create_subprocess_exec(
                    sys.executable, "-m", "uvicorn", APP_MODULES[group],
                    "--host", "127.0.0.1", "--port", str(port),
                    cwd=ROOT, env=env,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await _wait_http(f"http://127.0.0.1:{port}/health", api)
                active_group = group
            scenario_output = output.with_name(f"{output.stem}_{scenario_id}.json")
            report = await run_eval(
                base_url=f"http://127.0.0.1:{port}",
                output=scenario_output,
                scenario_id=scenario_id,
                model_is_local_stub=False,
                server_output_cap=per_turn_cap,
                server_session_cap=session_cap,
            )
            reports.append(report)
        summary = {
            item["scenario_id"]: _scenario_summary(item)
            for item in reports
        }
        turns = [turn for item in reports for turn in item["turns"]]
        completion_tokens = [
            int(turn.get("model_usage", {}).get("output_tokens", 0)) for turn in turns
        ]
        session_completion_tokens = [
            int(turn.get("model_usage_session_total", {}).get("output_tokens", 0))
            for turn in turns
        ]
        token_caps_respected = all(
            per_turn_cap >= output_tokens
            and session_cap >= session_tokens
            for output_tokens, session_tokens in zip(
                completion_tokens, session_completion_tokens, strict=True
            )
        ) and sum(completion_tokens) <= aggregate_cap
        result = {
            "evaluation_id": "advisor_product_live_suite_v1",
            "model": settings.llm_model,
            "run_at_utc": datetime.now(UTC).isoformat(),
            "scenario_count": len(reports),
            "turn_count": sum(item["turn_count"] for item in summary.values()),
            "total_output_tokens": sum(completion_tokens),
            "max_turn_output_tokens": max(completion_tokens, default=0),
            "max_session_output_tokens": max(session_completion_tokens, default=0),
            "token_caps_respected": token_caps_respected,
            "passed_scenario_count": sum(item["passed"] for item in summary.values()),
            "temporary_accounts_deleted": all(item["account_deleted"] for item in summary.values()),
            "all_scenarios_passed": len(reports) == len(scenarios)
            and all(item["passed"] and item["account_deleted"] for item in summary.values())
            and token_caps_respected,
            "maps_weather_mode": "FAKE_MAPS_AND_LOCAL_SYNTHETIC_WEATHER",
            "per_turn_output_cap": per_turn_cap,
            "per_session_output_cap": session_cap,
            "aggregate_output_token_cap": aggregate_cap,
            "summary": summary,
            "product_metrics": _aggregate_product_metrics(list(summary.values())),
            "scenario_reports": [
                str(output.with_name(f"{output.stem}_{item['scenario_id']}.json"))
                for item in reports
            ],
        }
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"output": str(output), **result}, ensure_ascii=False, indent=2))
        return result
    finally:
        await _stop(api)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--scenario", action="append", choices=SCENARIO_ORDER)
    parser.add_argument("--per-turn-token-cap", type=int, default=128)
    parser.add_argument("--session-token-cap", type=int, default=256)
    parser.add_argument("--aggregate-token-cap", type=int, default=DEFAULT_TOTAL_OUTPUT_TOKEN_CAP)
    args = parser.parse_args()
    if not 128 <= args.per_turn_token_cap <= 512:
        parser.error("per-turn output cap must be between 128 and 512")
    if args.session_token_cap < args.per_turn_token_cap or args.session_token_cap > 1024:
        parser.error("session output cap must be >= per-turn cap and <= 1024")
    if args.aggregate_token_cap > MAX_SUPPORTED_TOTAL_OUTPUT_TOKEN_CAP:
        parser.error("aggregate output cap cannot exceed 10240")
    if args.aggregate_token_cap < args.per_turn_token_cap:
        parser.error("aggregate output cap must be >= per-turn cap")
    scenarios = tuple(args.scenario or SCENARIO_ORDER)
    maximum_required = _maximum_output_for_scenarios(
        scenarios, args.per_turn_token_cap, args.session_token_cap,
    )
    if args.aggregate_token_cap < maximum_required:
        parser.error(
            f"aggregate output cap must cover scenario turn/session maxima ({maximum_required})"
        )
    asyncio.run(_run(
        args.output.resolve(), scenarios,
        args.per_turn_token_cap, args.session_token_cap, args.aggregate_token_cap,
    ))


if __name__ == "__main__":
    main()
