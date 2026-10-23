"""Repeatable local-stub preflight for matched B2/B3 schedule cases.

This executes the production AgentRuntime with synthetic map fixtures and a
loopback-only OpenAI-compatible action stub. It is harness verification, not
model-quality evidence and not an authenticated product-API test.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

from tests.evaluation.advisor_unseen_multiturn_eval import _run_turn

ROOT = Path(__file__).resolve().parents[2]
DATASET = ROOT / "tests/evaluation/cases/advisor_matched_schedule_b1_v1.json"
REQUEST_CAP = 48
OUTPUT_TOKEN_CAP = 128
TOTAL_OUTPUT_TOKEN_CAP = 12_288
ARMS = ("B2_AGENT_LOOP", "B3_VERIFIER_RECOVERY")


class _LocalActionStub(BaseHTTPRequestHandler):
    server_version = "TravelPilotLocalEvaluationStub/1.0"

    def do_POST(self) -> None:
        state = self.server.evaluation_state  # type: ignore[attr-defined]
        try:
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            prompt = json.loads(request["messages"][-1]["content"])
            observation = prompt["observation"]
            allowed_tools = prompt["allowed_tools"]
            history = observation.get("conversation_history", [])
            matrix_available = any(
                isinstance(item, dict)
                and item.get("role") == "tool_observation"
                and item.get("tool") == "get_distance_matrix"
                for item in history
            )
            if "build_schedule_draft" in allowed_tools and matrix_available:
                action = {
                    "kind": "CALL_TOOL",
                    "tool": "build_schedule_draft",
                    "arguments": {},
                }
            elif "get_distance_matrix" in allowed_tools:
                places = observation["trip_context"]["saved_place_details"]
                action = {
                    "kind": "CALL_TOOL",
                    "tool": "get_distance_matrix",
                    "arguments": {
                        "points": [
                            [place["latitude"], place["longitude"]]
                            for place in places
                        ],
                    },
                }
            else:
                action = {"kind": "FINISH", "summary": "没有可执行动作"}

            state["requests"] += 1
            state["output_tokens"] += state["completion_tokens_per_request"]
            content = json.dumps(action, ensure_ascii=False)
            body = json.dumps(
                {
                    "choices": [
                        {"message": {"content": content}, "finish_reason": "stop"},
                    ],
                    "usage": {
                        "prompt_tokens": 20,
                        "completion_tokens": state["completion_tokens_per_request"],
                        "total_tokens": 30,
                    },
                },
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            self.send_error(400, "invalid local evaluation request")

    def log_message(self, _format: str, *_args: Any) -> None:
        return


def _adapt_case(case: dict[str, Any]) -> dict[str, Any]:
    state = case["state"]
    places = [
        {
            "id": place["id"],
            "name": place["name"],
            "latitude": place["latitude"],
            "longitude": place["longitude"],
        }
        for place in state["saved_places"]
    ]
    return {
        "id": case["id"],
        "task_type": "schedule_existing_trip_places",
        "initial_state": {
            "destination": state["destination"],
            "trip_dates": state["trip_dates"],
            "pace_level": state["pace_level"],
            "known_places": places,
            "saved_place_details": places,
            "saved_places": len(places),
        },
        "turns": [
            {
                "user": case["turns"][0],
                "expected_action": "GET_DISTANCE_MATRIX",
                "allowed_tools": ["get_distance_matrix", "build_schedule_draft"],
            },
            {
                "user": case["turns"][1],
                "expected_action": "BUILD_SCHEDULE_DRAFT",
                "allowed_tools": ["get_distance_matrix", "build_schedule_draft"],
            },
        ],
    }


async def _evaluate_with_stub(dataset_path: Path) -> dict[str, Any]:
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    if len(dataset.get("cases", [])) != 4:
        raise ValueError("matched local preflight requires exactly four frozen cases")
    state = {"requests": 0, "output_tokens": 0, "completion_tokens_per_request": 10}
    server = HTTPServer(("127.0.0.1", 0), _LocalActionStub)
    server.evaluation_state = state  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}/v1"
    global_budget = {
        "requests": 0,
        "output_tokens": 0,
        "request_cap": REQUEST_CAP,
        "exhausted": 0,
        "reported_overrun": 0,
    }
    results: list[dict[str, Any]] = []
    try:
        for frozen_case in dataset["cases"]:
            case = _adapt_case(frozen_case)
            arm_results: dict[str, Any] = {}
            for arm in ARMS:
                history: list[dict[str, Any]] = []
                turn_results = []
                for index, turn in enumerate(case["turns"]):
                    history.append({"role": "user", "content": turn["user"]})
                    result = await _run_turn(
                        case=case,
                        turn=turn,
                        turn_index=index,
                        arm=arm,
                        history=history,
                        api_key="loopback-only",
                        model="local-stub",
                        base_url=base_url,
                        output_token_cap=OUTPUT_TOKEN_CAP,
                        global_budget=global_budget,
                    )
                    turn_results.append(result)
                    action = result.get("action") or {}
                    history.append(
                        {
                            "role": "assistant",
                            "action": action,
                            "content": action.get("summary", ""),
                        },
                    )
                    history.extend(result.get("tool_observations", []))

                if any(item.get("expected_action_match") is not True for item in turn_results):
                    raise AssertionError(f"{frozen_case['id']} {arm}: expected action mismatch")
                if arm == "B3_VERIFIER_RECOVERY" and any(
                    item.get("verification_failures") != 0 for item in turn_results
                ):
                    raise AssertionError(
                        f"{frozen_case['id']} {arm}: synthetic fixtures must pass their verifier"
                    )
                if any(item.get("formal_write") is not False for item in turn_results):
                    raise AssertionError(f"{frozen_case['id']} {arm}: formal write detected")
                arm_results[arm] = {
                    "actions": [item["action"].get("tool") for item in turn_results],
                    "turns": turn_results,
                    "formal_writes": 0,
                }

            if arm_results[ARMS[0]]["actions"] != arm_results[ARMS[1]]["actions"]:
                raise AssertionError(f"{frozen_case['id']}: B2/B3 action paths diverged")
            results.append(
                {
                    "case_id": frozen_case["id"],
                    "arms": arm_results,
                    "matched_action_path": True,
                },
            )

        if state["requests"] != global_budget["requests"]:
            raise AssertionError("loopback provider request accounting mismatch")
        if state["requests"] > REQUEST_CAP:
            raise AssertionError("provider request ceiling exceeded")
        if state["output_tokens"] > TOTAL_OUTPUT_TOKEN_CAP:
            raise AssertionError("aggregate output-token ceiling exceeded")
        return {
            "evaluation_id": "advisor_matched_schedule_runtime_local_preflight_v1",
            "dataset_id": dataset["dataset_id"],
            "provider_mode": "LOOPBACK_STUB_AND_SYNTHETIC_TOOLS",
            "request_cap": REQUEST_CAP,
            "requests_used": state["requests"],
            "per_request_output_cap": OUTPUT_TOKEN_CAP,
            "output_tokens_reported": state["output_tokens"],
            "aggregate_output_token_cap": TOTAL_OUTPUT_TOKEN_CAP,
            "formal_writes": 0,
            "cases": results,
            "limitation": (
                "The stub selects tools from the allowlist and prior observations. "
                "This validates loop integration and budget guards, not model quality "
                "or authenticated product-API behavior."
            ),
        }
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def evaluate_local_stub(dataset_path: Path = DATASET) -> dict[str, Any]:
    return asyncio.run(_evaluate_with_stub(dataset_path))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DATASET)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = evaluate_local_stub(args.dataset)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(
        json.dumps(
            {
                "evaluation_id": report["evaluation_id"],
                "requests_used": report["requests_used"],
                "output_tokens_reported": report["output_tokens_reported"],
                "case_count": len(report["cases"]),
            },
            ensure_ascii=False,
        ),
    )


if __name__ == "__main__":
    main()
