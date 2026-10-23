"""Code and dataset provenance for reproducible Advisor evaluation artifacts."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

EVALUATION_SOURCES = {
    "advisor_full_task": (
        "tests/evaluation/advisor_full_task_eval.py",
        "tests/evaluation/advisor_fixed_workflow_baseline.py",
        "tests/evaluation/cases/advisor_existing_trip_agent_v3.json",
        "packages/core/src/travelpilot_core/agents/planner.py",
        "packages/core/src/travelpilot_core/agents/loop.py",
        "packages/core/src/travelpilot_core/agents/runtime.py",
        "packages/core/src/travelpilot_core/agents/verifiers.py",
        "apps/api/src/travelpilot_api/advisor/scheduling.py",
        "packages/core/src/travelpilot_core/engines/optimizer/itinerary.py",
    ),
    "advisor_live_action": (
        "tests/evaluation/advisor_live_action_eval.py",
        "tests/evaluation/advisor_fixed_workflow_baseline.py",
        "tests/evaluation/cases/advisor_existing_trip_agent_v3.json",
        "packages/core/src/travelpilot_core/agents/planner.py",
        "packages/core/src/travelpilot_core/agents/loop.py",
        "packages/core/src/travelpilot_core/agents/runtime.py",
        "packages/core/src/travelpilot_core/agents/verifiers.py",
        "apps/api/src/travelpilot_api/advisor/scheduling.py",
        "packages/core/src/travelpilot_core/engines/optimizer/itinerary.py",
    ),
    "advisor_open_consultation_live": (
        "tests/evaluation/advisor_open_consultation_live_eval.py",
        "tests/evaluation/cases/advisor_open_consultation_v2.json",
        "packages/core/src/travelpilot_core/agents/planner.py",
        "packages/core/src/travelpilot_core/agents/loop.py",
        "packages/core/src/travelpilot_core/agents/runtime.py",
        "apps/api/src/travelpilot_api/advisor/search_loop.py",
        "apps/api/src/travelpilot_api/advisor/tools.py",
        "apps/api/src/travelpilot_api/advisor/turn_service.py",
        "apps/api/src/travelpilot_api/advisor/state.py",
    ),
    "advisor_multiturn_contracts": (
        "tests/evaluation/advisor_multiturn_contracts.py",
        "tests/evaluation/cases/advisor_multiturn_v1.json",
        "tests/evaluation/test_advisor_multiturn_contracts.py",
        "apps/api/src/travelpilot_api/advisor/task_state.py",
        "apps/api/src/travelpilot_api/advisor/state.py",
        "packages/core/src/travelpilot_core/agents/loop.py",
    ),
    "advisor_multiturn_protocol": (
        "tests/evaluation/advisor_multiturn_protocol_eval.py",
        "tests/evaluation/advisor_multiturn_contracts.py",
        "tests/evaluation/expand_multiturn_dataset.py",
        "tests/evaluation/cases/advisor_multiturn_v2.json",
        "tests/evaluation/cases/advisor_multiturn_v3.json",
        "apps/api/tests/test_advisor_multiturn_protocol_eval.py",
        "apps/api/src/travelpilot_api/advisor/state.py",
        "apps/api/src/travelpilot_api/advisor/task_state.py",
    ),
    "advisor_multiturn_protocol_v4": (
        "tests/evaluation/advisor_multiturn_protocol_eval.py",
        "tests/evaluation/advisor_multiturn_contracts.py",
        "tests/evaluation/align_multiturn_capabilities.py",
        "tests/evaluation/cases/advisor_multiturn_v4.json",
        "apps/api/tests/test_advisor_multiturn_protocol_eval.py",
        "apps/api/src/travelpilot_api/advisor/state.py",
        "apps/api/src/travelpilot_api/advisor/task_state.py",
    ),
    "advisor_multiturn_protocol_v5": (
        "tests/evaluation/advisor_multiturn_protocol_eval.py",
        "tests/evaluation/advisor_multiturn_contracts.py",
        "tests/evaluation/advisor_action_ownership.py",
        "tests/evaluation/cases/advisor_multiturn_v4.json",
        "tests/evaluation/cases/advisor_multiturn_v5.json",
        "apps/api/tests/test_advisor_multiturn_protocol_eval.py",
        "apps/api/src/travelpilot_api/advisor/state.py",
        "apps/api/src/travelpilot_api/advisor/task_state.py",
        "apps/api/src/travelpilot_api/advisor/turn_service.py",
    ),
    "advisor_multiturn_contracts_v2": (
        "tests/evaluation/advisor_multiturn_contracts.py",
        "tests/evaluation/cases/advisor_multiturn_v2.json",
        "apps/api/src/travelpilot_api/advisor/task_state.py",
    ),
    "advisor_multiturn_contracts_v3": (
        "tests/evaluation/advisor_multiturn_contracts.py",
        "tests/evaluation/expand_multiturn_dataset.py",
        "tests/evaluation/test_expand_multiturn_dataset.py",
        "tests/evaluation/cases/advisor_multiturn_v2.json",
        "tests/evaluation/cases/advisor_multiturn_v3.json",
        "apps/api/src/travelpilot_api/advisor/task_state.py",
    ),
    "advisor_multiturn_contracts_v4": (
        "tests/evaluation/align_multiturn_capabilities.py",
        "tests/evaluation/advisor_multiturn_contracts.py",
        "tests/evaluation/cases/advisor_multiturn_v4.json",
        "apps/api/src/travelpilot_api/advisor/tools.py",
        "apps/api/src/travelpilot_api/advisor/route_evidence.py",
        "apps/api/src/travelpilot_api/advisor/route_query.py",
        "apps/api/src/travelpilot_api/advisor/task_state.py",
    ),
    "advisor_multiturn_contracts_v5": (
        "tests/evaluation/align_multiturn_action_owners.py",
        "tests/evaluation/advisor_action_ownership.py",
        "tests/evaluation/advisor_multiturn_contracts.py",
        "tests/evaluation/cases/advisor_multiturn_v4.json",
        "tests/evaluation/cases/advisor_multiturn_v5.json",
        "tests/evaluation/test_advisor_multiturn_contracts.py",
        "apps/api/src/travelpilot_api/api/v1/advisor.py",
        "apps/api/src/travelpilot_api/advisor/turn_service.py",
    ),
    "advisor_multiturn_contracts_holdout": (
        "tests/evaluation/advisor_multiturn_contracts.py",
        "tests/evaluation/cases/advisor_multiturn_holdout_v1.json",
        "tests/evaluation/cases/advisor_multiturn_v1.json",
        "tests/evaluation/cases/advisor_multiturn_v2.json",
        "tests/evaluation/cases/advisor_multiturn_v3.json",
        "tests/evaluation/cases/advisor_multiturn_v4.json",
        "tests/evaluation/cases/advisor_multiturn_v5.json",
        "tests/evaluation/cases/advisor_open_consultation_v2.json",
        "tests/evaluation/cases/advisor_existing_trip_agent_v3.json",
    ),
    "advisor_multiturn_task_scripted": (
        "tests/evaluation/advisor_multiturn_task_eval.py",
        "tests/evaluation/advisor_multiturn_contracts.py",
        "tests/evaluation/cases/advisor_multiturn_v5.json",
    ),
    "advisor_multiturn_runtime_local": (
        "tests/evaluation/advisor_multiturn_runtime_eval.py",
        "tests/evaluation/advisor_multiturn_task_eval.py",
        "tests/evaluation/advisor_multiturn_contracts.py",
        "tests/evaluation/cases/advisor_multiturn_v5.json",
        "packages/core/src/travelpilot_core/agents/loop.py",
        "packages/core/src/travelpilot_core/agents/runtime.py",
    ),
    "advisor_verifier_recovery_ablation": (
        "tests/evaluation/advisor_verifier_recovery_ablation.py",
        "tests/evaluation/test_advisor_verifier_recovery_ablation.py",
        "packages/core/src/travelpilot_core/agents/loop.py",
        "packages/core/src/travelpilot_core/agents/runtime.py",
        "packages/core/src/travelpilot_core/agents/verifiers.py",
    ),
    "advisor_preference_relevance": (
        "tests/evaluation/advisor_preference_relevance_eval.py",
        "tests/evaluation/cases/advisor_preference_relevance_v1.json",
        "apps/api/src/travelpilot_api/advisor/interpretation.py",
        "apps/api/src/travelpilot_api/advisor/destination_service.py",
    ),
    "advisor_preference_relevance_v1": (
        "tests/evaluation/advisor_preference_relevance_eval.py",
        "tests/evaluation/cases/advisor_preference_relevance_v1.json",
        "apps/api/src/travelpilot_api/advisor/interpretation.py",
        "apps/api/src/travelpilot_api/advisor/destination_service.py",
    ),
    "advisor_preference_relevance_v2": (
        "tests/evaluation/advisor_preference_relevance_eval.py",
        "tests/evaluation/cases/advisor_preference_relevance_v2.json",
        "apps/api/src/travelpilot_api/advisor/interpretation.py",
        "apps/api/src/travelpilot_api/advisor/destination_service.py",
    ),
    "advisor_multiturn_live": (
        "tests/evaluation/advisor_multiturn_live_eval.py",
        "tests/evaluation/cases/advisor_multiturn_v5.json",
        "packages/core/src/travelpilot_core/agents/planner.py",
        "packages/core/src/travelpilot_core/agents/loop.py",
    ),
    "advisor_unseen_multiturn_pilot": (
        "tests/evaluation/advisor_unseen_multiturn_eval.py",
        "tests/evaluation/test_advisor_unseen_multiturn_eval.py",
        "tests/evaluation/advisor_multiturn_live_eval.py",
        "tests/evaluation/advisor_multiturn_task_eval.py",
        "tests/evaluation/advisor_multiturn_contracts.py",
        "tests/evaluation/provenance.py",
        "tests/evaluation/cases/advisor_multiturn_holdout_v3.json",
        "packages/core/src/travelpilot_core/agents/planner.py",
        "packages/core/src/travelpilot_core/agents/loop.py",
        "packages/core/src/travelpilot_core/agents/runtime.py",
        "packages/core/src/travelpilot_core/agents/verifiers.py",
    ),
    "advisor_multiturn_holdout_live": (
        "tests/evaluation/advisor_multiturn_live_eval.py",
        "tests/evaluation/advisor_multiturn_contracts.py",
        "tests/evaluation/cases/advisor_multiturn_holdout_v1.json",
        "packages/core/src/travelpilot_core/agents/planner.py",
        "packages/core/src/travelpilot_core/agents/loop.py",
        "packages/core/src/travelpilot_core/agents/runtime.py",
    ),
    "advisor_product_live": (
        "tests/evaluation/advisor_product_live_eval.py",
        "apps/api/src/travelpilot_api/build_info.py",
        "apps/api/src/travelpilot_api/main.py",
        "apps/api/src/travelpilot_api/api/v1/advisor_messages.py",
        "apps/api/src/travelpilot_api/advisor/interpretation.py",
        "apps/api/src/travelpilot_api/advisor/request_planning.py",
        "apps/api/src/travelpilot_api/advisor/turn_plan.py",
        "apps/api/src/travelpilot_api/advisor/scheduling.py",
        "apps/api/src/travelpilot_api/advisor/turn_responses.py",
        "apps/api/src/travelpilot_api/advisor/turn_service.py",
        "packages/core/src/travelpilot_core/agents/advisor_reply.py",
        "apps/api/src/travelpilot_api/advisor/scheduling.py",
        "packages/core/src/travelpilot_core/agents/loop.py",
        "packages/infrastructure/src/travelpilot_infra/providers/maps/fake.py",
    ),
    "advisor_product_local_suite": (
        "scripts/verify_advisor_product_local_suite.py",
        "scripts/local_openai_chat_stub.py",
        "tests/evaluation/advisor_product_live_eval.py",
        "tests/evaluation/provenance.py",
        "tests/evaluation/local_advisor_weather_app.py",
        "tests/evaluation/local_advisor_destination_app.py",
        "tests/evaluation/advisor_route_weather_failure_app.py",
        "tests/evaluation/advisor_route_weather_weather_failure_app.py",
        "tests/evaluation/advisor_route_outage_app.py",
    ),
    "advisor_matched_schedule_product_api_local_preflight": (
        "scripts/verify_advisor_matched_schedule_product_api.py",
        "scripts/verify_advisor_product_local_suite.py",
        "scripts/local_openai_chat_stub.py",
        "tests/evaluation/advisor_product_live_eval.py",
        "tests/evaluation/provenance.py",
        "tests/evaluation/cases/advisor_matched_schedule_b1_v1.json",
        "apps/api/src/travelpilot_api/api/v1/advisor_messages.py",
        "apps/api/src/travelpilot_api/api/v1/plans.py",
        "apps/api/src/travelpilot_api/advisor/turn_service.py",
        "apps/api/src/travelpilot_api/advisor/scheduling.py",
        "packages/core/src/travelpilot_core/agents/loop.py",
        "packages/infrastructure/src/travelpilot_infra/providers/maps/fake.py",
    ),
}


def evaluation_provenance(kind: str) -> dict[str, object]:
    """Pin the checkout and the source files that determine an evaluation result."""
    try:
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain", "--untracked-files=normal"],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        )
    except (OSError, subprocess.CalledProcessError):
        revision = None
        dirty = None

    paths = EVALUATION_SOURCES[kind]
    return {
        "git_revision": revision,
        "working_tree_dirty": dirty,
        "source_sha256": {
            relative: hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
            for relative in paths
        },
    }
