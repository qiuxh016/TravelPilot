"""Run the bounded Advisor product scenarios through local API/model fixtures.

This composes the existing per-scenario evaluator with short-lived API processes.
It uses a loopback model stub, Fake Maps and explicit synthetic weather/route
failure apps; it never calls live model or travel-data providers.
"""

from __future__ import annotations

import argparse
import asyncio
import json
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

from travelpilot_api.build_info import advisor_runtime_source_manifest, fingerprint_manifest

from tests.evaluation.advisor_product_live_eval import SCENARIOS, run_eval
from tests.evaluation.provenance import evaluation_provenance

STUB = ROOT / "scripts" / "local_openai_chat_stub.py"
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
    "destination-discovery",
    "general-trip-question",
    "weather-preparation",
    "route-weather-preparation",
    "unsupported-intercity-weather",
    "route-weather-route-outage",
    "route-weather-weather-outage",
    "weather-indoor-search",
    "route-provider-outage",
)
APP_MODULES = {
    "default": "travelpilot_api.main:app",
    "weather": "tests.evaluation.local_advisor_weather_app:app",
    "route-weather-failure": "tests.evaluation.advisor_route_weather_failure_app:app",
    "route-weather-weather-failure": "tests.evaluation.advisor_route_weather_weather_failure_app:app",
    "route-outage": "tests.evaluation.advisor_route_outage_app:app",
    "destination-discovery": "tests.evaluation.local_advisor_destination_app:app",
}


def _source_provenance() -> dict:
    runtime_manifest = advisor_runtime_source_manifest(ROOT)
    return {
        **evaluation_provenance("advisor_product_local_suite"),
        "advisor_runtime_source_sha256": runtime_manifest,
        "advisor_runtime_fingerprint": fingerprint_manifest(runtime_manifest),
    }


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


async def _wait_http(url: str, process: asyncio.subprocess.Process) -> None:
    for _ in range(240):
        if process.returncode is not None:
            raise RuntimeError(f"local API exited before becoming ready (code={process.returncode})")
        try:
            await asyncio.to_thread(_read_url, url)
            return
        except (OSError, URLError):
            await asyncio.sleep(0.25)
    raise TimeoutError("local API did not become ready within 60 seconds")


def _read_url(url: str) -> bytes:
    with urlopen(url, timeout=2) as response:
        return response.read()


async def _start_api(
    app_module: str, port: int, *, stub_port: int,
    per_turn_token_cap: int, session_token_cap: int, maps_provider: str = "fake",
) -> asyncio.subprocess.Process:
    env = dict(os.environ)
    existing_pythonpath = env.get("PYTHONPATH", "")
    required_paths = [
        str(ROOT / "apps/api/src"),
        str(ROOT / "packages/core/src"),
        str(ROOT / "packages/infrastructure/src"),
        str(ROOT / "packages/infra/src"),
        str(ROOT),
    ]
    env["PYTHONPATH"] = os.pathsep.join(
        [*required_paths, *([existing_pythonpath] if existing_pythonpath else [])]
    )
    env.update({
        "LLM_API_KEY": "local-evaluation-only",
        "LLM_MODEL": "local-stub",
        "LLM_BASE_URL": f"http://127.0.0.1:{stub_port}/v1",
        "MAPS_PROVIDER": maps_provider,
        "WEATHER_PROVIDER": "fake",
        "AMAP_API_KEY": "local-evaluation-only" if maps_provider == "amap" else "",
        "SMTP_HOST": "",
        "EMAIL_SEND_LIMIT_PER_HOUR": "10000",
        "LLM_MAX_TOKENS_PER_TURN": str(per_turn_token_cap),
        "LLM_MAX_OUTPUT_TOKENS_PER_SESSION": str(session_token_cap),
    })
    return await asyncio.create_subprocess_exec(
        sys.executable, "-m", "uvicorn", app_module,
        "--host", "127.0.0.1", "--port", str(port),
        cwd=ROOT,
        env=env,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=(
            None if os.environ.get("TRAVELPILOT_LOCAL_EVAL_API_LOGS")
            else asyncio.subprocess.DEVNULL
        ),
    )


async def _stop(process: asyncio.subprocess.Process | None) -> None:
    if process is None or process.returncode is not None:
        return
    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=8)
    except TimeoutError:
        process.kill()
        await asyncio.wait_for(process.wait(), timeout=5)


async def _run(
    *, output: Path, scenarios: tuple[str, ...],
    per_turn_token_cap: int, session_token_cap: int,
) -> dict:
    output = output.resolve()
    if output.exists():
        raise FileExistsError(f"refusing to overwrite suite report: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    source_provenance = _source_provenance()

    stub_port = _free_port()
    stub_env = dict(os.environ)
    stub_env.update({
        "PYTHONPATH": os.pathsep.join([
            str(ROOT / "apps/api/src"), str(ROOT / "packages/core/src"),
            str(ROOT / "packages/infrastructure/src"), str(ROOT / "packages/infra/src"),
            str(ROOT),
        ]),
        "LLM_API_KEY": "local-evaluation-only",
        "LLM_MODEL": "local-stub",
        "LLM_BASE_URL": f"http://127.0.0.1:{stub_port}/v1",
        "MAPS_PROVIDER": "fake",
        "WEATHER_PROVIDER": "fake",
        "AMAP_API_KEY": "",
        "SMTP_HOST": "",
        "EMAIL_SEND_LIMIT_PER_HOUR": "10000",
        "TRAVELPILOT_STUB_PORT": str(stub_port),
    })
    stub = await asyncio.create_subprocess_exec(
        sys.executable, str(STUB), "--port", str(stub_port),
        cwd=ROOT,
        env=stub_env,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    api: asyncio.subprocess.Process | None = None
    reports = []
    try:
        await _wait_http(f"http://127.0.0.1:{stub_port}/stats", stub)
        active_app_group = None
        api_port = 0
        for scenario_id in scenarios:
            if scenario_id not in SCENARIOS:
                raise RuntimeError(f"product scenario is not registered: {scenario_id}")
            app_group = (
                "weather" if scenario_id in {
                    "weather-indoor-search", "weather-preparation",
                    "lunch-weather-preparation", "route-weather-preparation",
                    "schedule-route-weather", "unsupported-intercity-weather",
                }
                else "route-weather-failure" if scenario_id == "route-weather-route-outage"
                else "route-weather-weather-failure" if scenario_id in {
                    "route-weather-weather-outage", "lunch-weather-weather-outage",
                }
                else "route-outage" if scenario_id == "route-provider-outage"
                else "destination-discovery" if scenario_id == "destination-discovery"
                else "default"
            )
            if app_group != active_app_group:
                await _stop(api)
                api_port = _free_port()
                api = await _start_api(
                    APP_MODULES[app_group], api_port, stub_port=stub_port,
                    per_turn_token_cap=per_turn_token_cap,
                    session_token_cap=session_token_cap,
                    maps_provider=(
                        "amap" if scenario_id == "destination-discovery" else "fake"
                    ),
                )
                await _wait_http(f"http://127.0.0.1:{api_port}/health", api)
                active_app_group = app_group

            scenario_output = output.with_name(f"{output.stem}_{scenario_id}.json")
            report = await run_eval(
                base_url=f"http://127.0.0.1:{api_port}",
                output=scenario_output,
                scenario_id=scenario_id,
                model_is_local_stub=True,
                server_output_cap=per_turn_token_cap,
                server_session_cap=session_token_cap,
            )
            reports.append(report)

        summary = {
            report["scenario_id"]: {
                "passed": report["product_assessment"]["passed"],
                "checks": report["product_assessment"]["checks"],
                "account_deleted": report["temporary_account_deleted"],
                "turn_count": len(report["turns"]),
                "failure": report["failure"],
            }
            for report in reports
        }
        result = {
            "evaluation_id": "advisor_product_local_suite_v1",
            "provider_mode": "LOCAL_MODEL_STUB_FAKE_MAPS_SYNTHETIC_WEATHER_AND_ROUTE_FAILURE",
            "run_at_utc": datetime.now(UTC).isoformat(),
            "source_provenance": source_provenance,
            "scenario_count": len(reports),
            "all_scenarios_passed": len(reports) == len(scenarios)
            and all(item["passed"] and item["account_deleted"] for item in summary.values()),
            "summary": summary,
            "scenario_reports": [
                str(output.with_name(f"{output.stem}_{report['scenario_id']}.json"))
                for report in reports
            ],
            "limitations": [
                "Deterministic local model responses do not establish live-model quality.",
                "Fake Maps and synthetic weather/failure providers do not establish live provider quality.",
                "The suite verifies these bounded service journeys, not broad task coverage or human utility.",
            ],
        }
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"output": str(output), **result}, ensure_ascii=False, indent=2))
        if not result["all_scenarios_passed"]:
            raise SystemExit(1)
        return result
    finally:
        await _stop(api)
        await _stop(stub)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "tests/evaluation/results/advisor_product_local_suite_20261019.json",
    )
    parser.add_argument("--scenario", action="append", choices=SCENARIO_ORDER)
    parser.add_argument("--per-turn-token-cap", type=int, default=1200)
    parser.add_argument("--session-token-cap", type=int, default=12000)
    args = parser.parse_args()
    if args.per_turn_token_cap < 128 or args.session_token_cap < args.per_turn_token_cap:
        parser.error("token caps must be >=128 and session cap >= per-turn cap")
    asyncio.run(_run(
        output=args.output,
        scenarios=tuple(args.scenario or SCENARIO_ORDER),
        per_turn_token_cap=args.per_turn_token_cap,
        session_token_cap=args.session_token_cap,
    ))


if __name__ == "__main__":
    main()
