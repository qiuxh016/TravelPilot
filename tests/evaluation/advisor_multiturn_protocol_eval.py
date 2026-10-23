"""Exercise the production pending-task protocol against frozen multi-turn cases.

This measures state continuation and explicit-approval contract handling only.
It does not run the LLM, domain tools, planner, or score answer quality.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from travelpilot_api.advisor.state import (
    PendingAgentQuestion,
    cancelled_task_reply,
    cancels_pending_task,
    pending_question_matches_context,
    resume_pending_task,
)
from travelpilot_api.advisor.task_state import resolve_task_status

from tests.evaluation.advisor_multiturn_contracts import validate_dataset
from tests.evaluation.provenance import evaluation_provenance

ROOT = Path(__file__).resolve().parents[2]
DATASET = ROOT / "tests/evaluation/cases/advisor_multiturn_v5.json"
RESULT = ROOT / "tests/evaluation/results/advisor_multiturn_v5_protocol.json"
COMMIT_ACTIONS = {
    "COMMIT_CONFIRMED_DRAFT",
    "COMMIT_CONFIRMED_CANDIDATES",
    "SAVE_CONFIRMED_MEMORY",
    "UPDATE_CONFIRMED_MEMORY",
    "DELETE_CONFIRMED_MEMORY",
}


def evaluate_dataset(dataset: dict[str, Any]) -> dict[str, Any]:
    """Replay each frozen user turn through actual bounded Advisor state helpers."""
    contract = validate_dataset(dataset)
    resumed_turns = 0
    continuity_checks = 0
    continuity_failures: list[str] = []
    approval_cases = 0
    approval_failures: list[str] = []
    stale_checks = 0
    stale_failures: list[str] = []
    cancellation_checks = 0
    cancellation_failures: list[str] = []

    for case in dataset["cases"]:
        turns = case["turns"]
        pending: PendingAgentQuestion | None = None
        context = {"revision": case["initial_state"].get("context_revision", 7)}
        for index, turn in enumerate(turns):
            if pending is not None:
                before = pending
                changed_revision = turn.get("context_revision_after")
                if isinstance(changed_revision, int):
                    context["revision"] = changed_revision
                context_matches = pending_question_matches_context(before, context)
                expects_stale = turn.get("expected_action") == "REVALIDATE_TRIP_CONTEXT"
                if not context_matches or expects_stale:
                    stale_checks += 1
                    if (
                        context_matches
                        or turn.get("expected_status") != "STALE"
                        or not expects_stale
                    ):
                        stale_failures.append(f"{case['id']}:{index + 1}")
                elif cancels_pending_task(turn["user"]) or turn.get("expected_action") == "CANCEL_PENDING_TASK":
                    cancellation_checks += 1
                    reply = cancelled_task_reply(before.task_type)
                    if (
                        not cancels_pending_task(turn["user"])
                        or turn.get("expected_status") != "CANCELLED"
                        or turn.get("expected_action") != "CANCEL_PENDING_TASK"
                        or "已停止" not in reply
                        or "没有变化" not in reply
                    ):
                        cancellation_failures.append(f"{case['id']}:{index + 1}")
                else:
                    updated, rebuilt_goal = resume_pending_task(before, turn["user"])
                    resumed_turns += 1
                    continuity_checks += 1
                    if (
                        updated.task_id != before.task_id
                        or updated.task_type != before.task_type
                        or updated.context_revision != before.context_revision
                        or updated.answers[-1:] != (turn["user"][:500],)
                        or updated.turn_number != min(100, before.turn_number + 1)
                        or before.task not in rebuilt_goal
                        or not pending_question_matches_context(updated, context)
                    ):
                        continuity_failures.append(f"{case['id']}:{index + 1}")
                pending = None

            action = turn["expected_action"]
            if action in COMMIT_ACTIONS:
                approval_cases += 1
                previous = turns[index - 1] if index else {}
                if (
                    turn.get("confirmation_received") is not True
                    or previous.get("expected_status") != "WAITING_USER"
                ):
                    approval_failures.append(f"{case['id']}:{index + 1}")

            if turn["expected_status"] == "WAITING_USER":
                pending = PendingAgentQuestion(
                    task=" ".join(item["user"] for item in turns[: index + 1])[:1200],
                    question="fixture question",
                    context_revision=context["revision"],
                    task_type=case["task_type"],
                    task_id=f"{case['id']}:task",
                    answers=(),
                    turn_number=index + 1,
                )

            # Exercise the production lifecycle mapper for each expected terminal
            # state without asserting any model decision or provider behavior.
            resolved = resolve_task_status(
                stale=turn["expected_status"] == "STALE",
                cancelled=turn["expected_status"] == "CANCELLED",
                waiting_for_user=turn["expected_status"] == "WAITING_USER",
                proposed=turn["expected_status"] == "PROPOSED",
                has_partial_results=turn["expected_status"] == "PARTIAL",
                tool_failed=turn["expected_status"] == "PARTIAL",
            )
            if turn["expected_status"] in {
                "WAITING_USER", "PROPOSED", "COMPLETED", "PARTIAL", "STALE", "CANCELLED",
            }:
                expected_status = turn["expected_status"]
                if resolved != expected_status:
                    continuity_failures.append(f"{case['id']}:{index + 1}:status")

    passed = not (continuity_failures or approval_failures or stale_failures or cancellation_failures)
    canonical = json.dumps(dataset, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {
        "evaluation_id": f"{contract['evaluation_id']}_protocol",
        "status": "PRODUCTION_STATE_PROTOCOL_REPLAY",
        "dataset_id": contract["evaluation_id"],
        "provider_mode": "LOCAL_PRODUCTION_STATE_HELPERS",
        "case_count": contract["case_count"],
        "turn_count": contract["turn_count"],
        "resumed_turns": resumed_turns,
        "continuity_checks": continuity_checks,
        "continuity_failures": continuity_failures,
        "approval_gate_cases": approval_cases,
        "approval_gate_failures": approval_failures,
        "stale_context_checks": stale_checks,
        "stale_context_failures": stale_failures,
        "cancellation_checks": cancellation_checks,
        "cancellation_failures": cancellation_failures,
        "passed": passed,
        "case_file_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "source_provenance": evaluation_provenance(
            "advisor_multiturn_protocol_v5"
            if dataset.get("dataset_id") == "advisor_multiturn_v5"
            else "advisor_multiturn_protocol_v4"
            if dataset.get("dataset_id") == "advisor_multiturn_v4"
            else "advisor_multiturn_protocol"
        ),
        "interpretation": (
            "Production state-helper replay only: checks pending-task continuation, bounded answers, "
            "lifecycle mapping and fixture approval preconditions. No Agent/model/tool execution, "
            "answer-quality score, planner outcome, or live-provider result."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=DATASET)
    parser.add_argument("--output", type=Path, default=RESULT)
    args = parser.parse_args()
    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    report = evaluate_dataset(dataset)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "source_provenance"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
