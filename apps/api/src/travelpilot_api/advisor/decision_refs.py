"""Opaque stable references for observations and allowlisted tool actions."""

import hashlib
import json
from typing import Any


def stage_observation_ref(observations: dict[str, Any]) -> str:
    """Hash bounded inputs to a decision without persisting their contents."""
    encoded = json.dumps(observations, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def readonly_action_ref(tool: str, arguments: dict[str, Any]) -> str:
    """Hash an approved action; raw arguments stay in memory only."""
    encoded = json.dumps([tool, arguments], sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
