"""Stable fingerprints for the Advisor code loaded by a local API process."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


def _repository_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "apps/api/src/travelpilot_api/advisor").is_dir() and (
            parent / "packages/core/src/travelpilot_core/agents"
        ).is_dir():
            return parent
    raise RuntimeError("TravelPilot source tree is unavailable for Advisor build fingerprinting")


def advisor_runtime_source_manifest(root: Path | None = None) -> dict[str, str]:
    """Hash source modules that determine the authenticated Advisor execution path."""
    repository_root = root or _repository_root()
    api_root = Path("apps/api/src/travelpilot_api")
    paths = {
        api_root / "main.py",
        api_root / "build_info.py",
        *(api_root / "advisor").rglob("*.py"),
        *(api_root / "api/v1").glob("advisor*.py"),
        api_root / "schemas/advisor.py",
        api_root / "settings.py",
        *Path("packages/core/src/travelpilot_core/agents").rglob("*.py"),
        Path("packages/core/src/travelpilot_core/engines/optimizer/itinerary.py"),
        Path("packages/infrastructure/src/travelpilot_infra/providers/maps/fake.py"),
    }
    manifest: dict[str, str] = {}
    for relative_path in sorted(paths, key=lambda value: value.as_posix()):
        source = repository_root / relative_path
        if not source.is_file():
            raise RuntimeError(f"Advisor runtime source is missing: {relative_path.as_posix()}")
        manifest[relative_path.as_posix()] = hashlib.sha256(source.read_bytes()).hexdigest()
    return manifest


def fingerprint_manifest(manifest: dict[str, str]) -> str:
    canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
