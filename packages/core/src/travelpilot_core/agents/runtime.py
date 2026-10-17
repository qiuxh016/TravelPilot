"""A small bounded runtime with an explicit tool allowlist."""

import asyncio
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from travelpilot_core.ports.tools import ToolFailure, ToolSpec


@dataclass
class AgentResult:
    status: str
    output: dict[str, Any] = field(default_factory=dict)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    next_action: str | None = None


def redact_tool_trace(data: dict[str, Any]) -> dict[str, Any]:
    """Keep operational metrics while excluding queries, place names, IDs, and coordinates."""
    tool = str(data.get("tool", "unknown"))[:80]
    raw_input = data.get("input_data") if isinstance(data.get("input_data"), dict) else {}
    raw_output = data.get("output_data") if isinstance(data.get("output_data"), dict) else {}
    safe_input: dict[str, Any] = {"provided_fields": sorted(str(key) for key in raw_input)[:20]}
    if isinstance(raw_input.get("points"), list):
        safe_input["point_count"] = len(raw_input["points"])
    if isinstance(raw_input.get("keyword"), str):
        safe_input["query_length"] = len(raw_input["keyword"])
    safe_output: dict[str, Any] = {}
    for key in ("status", "source"):
        value = raw_output.get(key)
        if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,40}", value):
            safe_output[key] = value
    for key in ("places", "durations_min", "results"):
        value = raw_output.get(key)
        if isinstance(value, list):
            safe_output[f"{key}_count"] = len(value)
            if key == "durations_min":
                safe_output["matrix_rows"] = len(value)
                safe_output["matrix_columns"] = max((len(row) for row in value if isinstance(row, list)), default=0)
    return {
        "tool": tool,
        "status": str(data.get("status", "UNKNOWN"))[:32],
        "latency_ms": max(0, int(data.get("latency_ms", 0))),
        "error_code": str(data["error_code"])[:50] if data.get("error_code") else None,
        "input_data": safe_input,
        "output_data": safe_output,
    }


class AgentRuntime:
    def __init__(self, tools: list[ToolSpec], trace: Callable[[dict], Awaitable[None]],
                 *, max_steps: int = 5, token_budget: int = 2000):
        self.tools = {tool.name: tool for tool in tools}
        self.trace = trace
        self.max_steps = max_steps
        self.token_budget = token_budget
        self.steps = 0
        self.calls: list[dict] = []

    async def call(self, name: str, arguments: dict) -> dict:
        if name not in self.tools:
            raise ToolFailure("UNKNOWN_TOOL")
        if self.steps >= self.max_steps:
            raise ToolFailure("STEP_LIMIT")
        self.steps += 1
        tool = self.tools[name]
        try:
            validated = tool.input_schema.model_validate(arguments)
        except ValidationError as exc:
            raise ToolFailure("INVALID_TOOL_INPUT") from exc
        for attempt in range(tool.retry_limit + 1):
            started = time.perf_counter()
            try:
                output = await asyncio.wait_for(tool.handler(validated), timeout=tool.timeout_seconds)
                trace = {"tool": name, "status": "SUCCEEDED", "latency_ms": round((time.perf_counter() - started) * 1000),
                         "input_data": validated.model_dump(mode="json"), "output_data": output,
                         "error_code": None}
                self.calls.append(trace)
                await self.trace(trace)
                return output
            except (TimeoutError, Exception) as exc:
                code = ("TOOL_TIMEOUT" if isinstance(exc, TimeoutError) else
                        exc.code if isinstance(exc, ToolFailure) else "TOOL_FAILED")
                trace = {"tool": name, "status": "FAILED", "latency_ms": round((time.perf_counter() - started) * 1000),
                         "input_data": validated.model_dump(mode="json"), "output_data": None,
                         "error_code": code}
                self.calls.append(trace)
                await self.trace(trace)
                if attempt >= tool.retry_limit or isinstance(exc, ToolFailure):
                    raise ToolFailure(code) from exc
        raise ToolFailure("TOOL_FAILED")
