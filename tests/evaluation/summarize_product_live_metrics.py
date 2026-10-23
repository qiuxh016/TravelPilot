"""Derive trace-only metrics from a completed live product-suite manifest.

This command reads saved JSON artifacts only. It does not start the API or call
an LLM, Maps, Weather, or any TravelPilot mutation endpoint.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from scripts.run_advisor_product_live_suite import (
    _aggregate_product_metrics,
    _scenario_summary,
)

ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / "tests" / "evaluation" / "results"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_trace_metrics(manifest_path: Path) -> dict[str, Any]:
    manifest_path = manifest_path.resolve()
    results_root = RESULTS.resolve()
    if manifest_path.parent != results_root or not manifest_path.is_file():
        raise ValueError("manifest must be an existing file directly under evaluation results")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    scenario_reports = manifest.get("scenario_reports")
    if not isinstance(scenario_reports, list) or not scenario_reports:
        raise ValueError("manifest must contain scenario_reports")

    per_scenario = []
    seen: set[str] = set()
    source_hashes: dict[str, str] = {}
    for raw_path in scenario_reports:
        if not isinstance(raw_path, str):
            raise TypeError("scenario report paths must be strings")
        path = Path(raw_path).resolve()
        if path.parent != results_root or not path.is_file():
            raise ValueError("scenario reports must be existing files directly under evaluation results")
        report = json.loads(path.read_text(encoding="utf-8"))
        scenario_id = report.get("scenario_id")
        if not isinstance(scenario_id, str) or not scenario_id or scenario_id in seen:
            raise ValueError("scenario reports must have unique non-empty scenario_id values")
        if not str(report.get("provider_mode", "")).startswith("LIVE_MODEL_REAL_ADVISOR_API_FAKE_MAPS"):
            raise ValueError(f"{scenario_id} is not a live-model Advisor API report")
        if report.get("temporary_account_deleted") is not True:
            raise ValueError(f"{scenario_id} temporary account was not confirmed deleted")
        if report.get("resources", {}).get("formal_write_prevented") is not True:
            raise ValueError(f"{scenario_id} does not prove formal writes were prevented")
        seen.add(scenario_id)
        per_scenario.append({"scenario_id": scenario_id, **_scenario_summary(report)})
        source_hashes[path.name] = _sha256(path)

    expected_count = manifest.get("scenario_count")
    if expected_count != len(per_scenario):
        raise ValueError("scenario report count does not match aggregate manifest")
    if manifest.get("all_scenarios_passed") is True and not all(
        row["passed"] for row in per_scenario
    ):
        raise ValueError("scenario reports contradict the aggregate pass status")

    return {
        "report_type": "advisor_product_live_trace_metrics_v1",
        "source_manifest": manifest_path.name,
        "source_manifest_sha256": _sha256(manifest_path),
        "scenario_report_sha256": source_hashes,
        "evaluation_id": manifest.get("evaluation_id"),
        "model": manifest.get("model"),
        "run_at_utc": manifest.get("run_at_utc"),
        "maps_weather_mode": manifest.get("maps_weather_mode"),
        "per_scenario": per_scenario,
        "aggregate_metrics": _aggregate_product_metrics(per_scenario),
        "interpretation": (
            "Descriptive runtime traces only. These counts do not measure answer quality, "
            "user utility, statistical significance, or real Maps/Weather accuracy. "
            "Latency is null when the source turns predate request-latency instrumentation."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path, help="completed live product aggregate JSON")
    parser.add_argument("output", type=Path, help="new derived metrics JSON; existing files are not overwritten")
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        parser.error(f"refusing to overwrite metrics report: {output}")
    report = build_trace_metrics(args.manifest)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "scenario_count": report["aggregate_metrics"]["scenario_count"],
        "turn_count": report["aggregate_metrics"]["turn_count"],
        "output": str(output),
    }))


if __name__ == "__main__":
    main()
