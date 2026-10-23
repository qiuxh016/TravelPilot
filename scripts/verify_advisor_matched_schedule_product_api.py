"""Run v8 schedule cases through the authenticated current Advisor API locally.

This is a loopback-model/Fake-Maps preflight. It checks the full user-facing
message path and disposable-account cleanup; it is not live-model evidence.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import UTC, date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import verify_advisor_product_local_suite as harness
from tests.evaluation.advisor_product_live_eval import run_eval
from tests.evaluation.provenance import evaluation_provenance

DATASET = ROOT / "tests/evaluation/cases/advisor_matched_schedule_b1_v1.json"
DEFAULT_OUTPUT = (
    ROOT / "tests/evaluation/results/"
    "advisor_matched_schedule_product_api_local_preflight_v1.json"
)
PER_TURN_CAP = 512
SESSION_CAP = 1024
LOCAL_STUB_COMPLETION_TOKENS_PER_REQUEST = 20


def _scenario(case: dict) -> dict:
    state = case["state"]
    return {
        "destination": state["destination"],
        "turn_inputs": tuple(case["turns"]),
        "saved_places": tuple(
            (place["name"], place["latitude"], place["longitude"])
            for place in state["saved_places"]
        ),
        "confirmed_plan_setup": True,
    }


def _stub_stats(url: str) -> dict[str, int]:
    payload = json.loads(harness._read_url(url))
    keys = ("agent_decisions", "extraction_requests", "reply_requests")
    if any(not isinstance(payload.get(key), int) for key in keys):
        raise RuntimeError("local model stub returned invalid request counters")
    return {key: payload[key] for key in keys}


async def run_preflight(output: Path) -> dict:
    output = output.resolve()
    if output.exists():
        raise FileExistsError(f"refusing to overwrite preflight report: {output}")
    dataset = json.loads(DATASET.read_text(encoding="utf-8"))
    if dataset.get("status") != "FROZEN_FOR_PREAUTHORIZATION":
        raise RuntimeError("matched schedule dataset is not frozen for preauthorization")
    cases = dataset.get("cases")
    if not isinstance(cases, list) or len(cases) != 4:
        raise RuntimeError("expected the frozen four-case matched schedule dataset")

    stub_port = harness._free_port()
    api_port = harness._free_port()
    stub_env = dict(os.environ)
    required_paths = [
        str(ROOT / "apps/api/src"),
        str(ROOT / "packages/core/src"),
        str(ROOT / "packages/infrastructure/src"),
        str(ROOT / "packages/infra/src"),
        str(ROOT),
    ]
    stub_env.update({
        "PYTHONPATH": os.pathsep.join(required_paths),
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
        sys.executable, str(ROOT / "scripts/local_openai_chat_stub.py"),
        "--port", str(stub_port), cwd=ROOT, env=stub_env,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
    )
    api = None
    reports: list[dict] = []
    try:
        await harness._wait_http(f"http://127.0.0.1:{stub_port}/stats", stub)
        stats_url = f"http://127.0.0.1:{stub_port}/stats"
        api = await harness._start_api(
            "travelpilot_api.main:app", api_port, stub_port=stub_port,
            per_turn_token_cap=PER_TURN_CAP, session_token_cap=SESSION_CAP,
            maps_provider="fake",
        )
        await harness._wait_http(f"http://127.0.0.1:{api_port}/health", api)
        for case in cases:
            state = case["state"]
            trip_start, trip_end = map(date.fromisoformat, state["trip_dates"])
            case_output = output.with_name(f"{output.stem}_{case['id']}.json")
            stub_before = _stub_stats(stats_url)
            report = await run_eval(
                base_url=f"http://127.0.0.1:{api_port}",
                output=case_output,
                server_output_cap=PER_TURN_CAP,
                server_session_cap=SESSION_CAP,
                trip_start_date=trip_start,
                trip_end_date=trip_end,
                scenario_id=case["id"],
                scenario_override=_scenario(case),
                assessment_scenario_id="schedule-two-turn",
                model_is_local_stub=True,
            )
            stub_after = _stub_stats(stats_url)
            report["local_stub_usage"] = {
                key: stub_after[key] - stub_before[key]
                for key in stub_before
            }
            report["local_stub_request_count"] = sum(report["local_stub_usage"].values())
            resources = report["resources"]
            report["matched_schedule_preflight_checks"] = {
                "initial_confirmed_plan_created": (
                    resources.get("initial_plan_id") is not None
                    and resources.get("initial_plan_version") == 1
                    and resources.get("formal_plan_count_before") == 1
                ),
                "formal_plan_id_and_version_unchanged": (
                    resources.get("formal_plan_versions_before")
                    == resources.get("formal_plan_versions_after")
                ),
                "temporary_account_deleted": report["temporary_account_deleted"],
                "product_contract_passed": report["product_assessment"]["passed"],
            }
            report["matched_schedule_preflight_passed"] = all(
                report["matched_schedule_preflight_checks"].values()
            )
            case_output.write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            reports.append(report)

        result = {
            "evaluation_id": "advisor_matched_schedule_product_api_local_preflight_v1",
            "dataset_id": dataset["dataset_id"],
            "provider_mode": "LOCAL_MODEL_STUB_FAKE_MAPS_NO_WEATHER",
            "run_at_utc": datetime.now(UTC).isoformat(),
            "request_scope": "authenticated current Advisor API; no formal plan confirmation",
            "per_turn_output_token_cap": PER_TURN_CAP,
            "per_session_output_token_cap": SESSION_CAP,
            "case_count": len(reports),
            "user_turn_count": sum(report["turn_inputs_count"] for report in reports),
            "local_stub_request_count": sum(
                report["local_stub_request_count"] for report in reports
            ),
            "local_stub_reported_output_tokens": sum(
                report["local_stub_request_count"]
                * LOCAL_STUB_COMPLETION_TOKENS_PER_REQUEST
                for report in reports
            ),
            "all_cases_passed": len(reports) == len(cases) and all(
                report["matched_schedule_preflight_passed"] for report in reports
            ),
            "summary": {
                report["scenario_id"]: {
                    "passed": report["matched_schedule_preflight_passed"],
                    "checks": report["matched_schedule_preflight_checks"],
                    "provider_mode": report["provider_mode"],
                    "temporary_account_deleted": report["temporary_account_deleted"],
                    "turn_count": report["turn_inputs_count"],
                    "local_stub_request_count": report["local_stub_request_count"],
                    "local_stub_usage": report["local_stub_usage"],
                    "formal_plan_versions_before": report["resources"].get(
                        "formal_plan_versions_before"
                    ),
                    "formal_plan_versions_after": report["resources"].get(
                        "formal_plan_versions_after"
                    ),
                    "failure": report["failure"],
                }
                for report in reports
            },
            "scenario_reports": [
                str(output.with_name(f"{output.stem}_{report['scenario_id']}.json"))
                for report in reports
            ],
            "source_provenance": evaluation_provenance(
                "advisor_matched_schedule_product_api_local_preflight"
            ),
            "limitations": [
                "The local response stub is deterministic and does not establish model quality.",
                "This validates the current B3 product API only; it is not a matched B1/B2/B3 model comparison.",
                "Fake Maps were used; no weather, external provider, or real model request occurred.",
            ],
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if not result["all_cases_passed"]:
            raise SystemExit(1)
        print(json.dumps({"output": str(output), **result}, ensure_ascii=False, indent=2))
        return result
    finally:
        await harness._stop(api)
        await harness._stop(stub)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    asyncio.run(run_preflight(args.output))


if __name__ == "__main__":
    main()
