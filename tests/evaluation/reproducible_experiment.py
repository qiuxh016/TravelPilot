"""Run the deterministic replanning and replacement evaluation suite."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import random
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tests.evaluation.agent_loop_eval import run_evaluation as run_agent_loop_eval
from tests.evaluation.beam_stress_eval import run_stress_evaluation
from tests.evaluation.joint_replacement_eval import run_evaluation as run_joint_replacement
from tests.evaluation.preference_calibration import (
    run_synthetic_demo as run_preference_calibration_demo,
)
from tests.evaluation.preference_weight_eval import run_evaluation as run_preference_weight_eval
from tests.evaluation.replacement_search_eval import run_evaluation as run_replacement_search
from tests.evaluation.replanning_baseline import run_baseline
from tests.evaluation.replanning_comparison import run_comparison
from tests.evaluation.skill_applicability_eval import run_evaluation as run_skill_applicability_eval
from tests.evaluation.user_add_eval import run_evaluation as run_user_add_eval

ROOT = Path(__file__).resolve().parents[2]
RANDOM_SEED = 20261017
INPUT_FILES = (
    "tests/evaluation/reproducible_experiment.py",
    "tests/evaluation/agent_loop_eval.py",
    "tests/evaluation/skill_applicability_eval.py",
    "tests/evaluation/user_add_eval.py",
    "tests/evaluation/live_skill_ablation.py",
    "tests/evaluation/replanning_baseline.py",
    "tests/evaluation/replacement_search_eval.py",
    "tests/evaluation/joint_replacement_eval.py",
    "tests/evaluation/replanning_comparison.py",
    "tests/evaluation/beam_stress_eval.py",
    "tests/evaluation/preference_weight_eval.py",
    "tests/evaluation/preference_calibration.py",
    "tests/evaluation/cases/replanning_baseline.json",
    "tests/evaluation/cases/replacement_scenarios.json",
    "tests/evaluation/cases/joint_replacement_scenarios.json",
    "tests/evaluation/cases/scenarios.json",
    "tests/evaluation/cases/user_add_scenarios.json",
    "packages/core/src/travelpilot_core/engines/impact_analysis/replan.py",
    "packages/core/src/travelpilot_core/engines/impact_analysis/replacements.py",
    "packages/core/src/travelpilot_core/engines/impact_analysis/insertion.py",
    "apps/api/src/travelpilot_api/api/v1/events.py",
    "packages/infrastructure/src/travelpilot_infra/db/models/workflow.py",
    "packages/infrastructure/src/travelpilot_infra/db/migrations/versions/6f8a1c3d5e20_trip_event_timezone_offset.py",
    "packages/core/src/travelpilot_core/agents/loop.py",
    "packages/core/src/travelpilot_core/agents/runtime.py",
    "packages/core/src/travelpilot_core/agents/planner.py",
    "packages/core/src/travelpilot_core/agents/extraction.py",
    "packages/core/src/travelpilot_core/ports/tools.py",
    "packages/infrastructure/src/travelpilot_infra/providers/tool_adapters.py",
    "apps/api/src/travelpilot_api/api/v1/agent.py",
    "packages/core/src/travelpilot_core/skills.py",
    "packages/core/src/travelpilot_core/skills/content-import/SKILL.md",
    "packages/core/src/travelpilot_core/skills/weather-replan/SKILL.md",
    "packages/core/src/travelpilot_core/agents/verifiers.py",
    "pyproject.toml",
    "uv.lock",
)


def _stable_payload(result: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in result.items() if key != "measured_at_utc"}


def _digest(result: dict[str, Any]) -> str:
    encoded = json.dumps(
        _stable_payload(result), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _run_suites() -> dict[str, Any]:
    return {
        "constrained_agent_loop": run_agent_loop_eval(),
        "skill_applicability": run_skill_applicability_eval(),
        "user_add_feasibility": run_user_add_eval(),
        "rule_based_replan": run_baseline(),
        "replacement_feasibility": run_replacement_search(),
        "joint_replacement_search": run_joint_replacement(),
        "weather_rule_ablation": run_comparison(),
        "beam_stress": run_stress_evaluation(),
        "preference_weight_sensitivity": run_preference_weight_eval(),
        "preference_calibration_demo": run_preference_calibration_demo(),
    }


def _run_fresh_suite_process() -> dict[str, Any]:
    environment = os.environ.copy()
    environment.update({
        "PYTHONHASHSEED": str(RANDOM_SEED),
        "LLM_API_KEY": "",
        "LLM_MODEL": "",
        "AMAP_API_KEY": "",
    })
    completed = subprocess.run(
        [sys.executable, "-m", "tests.evaluation.reproducible_experiment", "--suite-worker"],
        cwd=ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def run_experiment() -> dict[str, Any]:
    first_run = _run_fresh_suite_process()
    second_run = _run_fresh_suite_process()
    suites = first_run["suites"]
    repeated = second_run["suites"]
    reproducible = all(_digest(suites[key]) == _digest(repeated[key]) for key in suites)
    revision = (
        subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
        ).stdout.strip()
        or "UNKNOWN"
    )
    return {
        "experiment_id": "travelpilot_replanning_reproducibility_v18",
        "measured_at_utc": datetime.now(UTC).isoformat(),
        "environment": {
            "code_revision": revision,
            "worktree_dirty": bool(
                subprocess.run(
                    ["git", "status", "--porcelain"],
                    check=False,
                    capture_output=True,
                    text=True,
                ).stdout.strip()
            ),
            "provider_mode": "SYNTHETIC_DETERMINISTIC",
            "model": None,
            "generated_token_budget": 0,
            "random_seed": RANDOM_SEED,
            "python_hash_seed": RANDOM_SEED,
            "runs_in_separate_processes": True,
            "suite_process_ids": [first_run["process_id"], second_run["process_id"]],
            "provider_credentials_disabled": all((
                first_run["provider_credentials_disabled"],
                second_run["provider_credentials_disabled"],
            )),
            "fixture_ids_are_stable_uuid5": True,
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "input_sha256": {
                path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in INPUT_FILES
            },
        },
        "repeatability": {
            "runs_per_suite": 2,
            "stable_outputs_match": reproducible,
            "suite_sha256": {key: _digest(value) for key, value in suites.items()},
            "timestamps_excluded_from_digest": True,
        },
        "scope": "deterministic synthetic algorithm behavior; does not measure real provider or LLM quality",
        "suites": suites,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--suite-worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.suite_worker:
        random.seed(RANDOM_SEED)
        print(json.dumps({
            "process_id": os.getpid(),
            "provider_credentials_disabled": all(not os.environ.get(key) for key in (
                "LLM_API_KEY", "LLM_MODEL", "AMAP_API_KEY",
            )),
            "suites": _run_suites(),
        }, ensure_ascii=False, separators=(",", ":")))
        raise SystemExit(0)
    report = run_experiment()
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
