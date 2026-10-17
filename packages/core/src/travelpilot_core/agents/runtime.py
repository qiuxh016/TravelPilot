"""A small bounded runtime with an explicit tool allowlist."""

import asyncio
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from travelpilot_core.ports.tools import RetryableToolError, ToolFailure, ToolSpec


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
    safe = {
        "tool": tool,
        "status": str(data.get("status", "UNKNOWN"))[:32],
        "latency_ms": max(0, int(data.get("latency_ms", 0))),
        "error_code": str(data["error_code"])[:50] if data.get("error_code") else None,
        "input_data": safe_input,
        "output_data": safe_output,
    }
    for key in ("logical_step", "attempt", "tool_attempts_total"):
        value = data.get(key)
        if isinstance(value, int) and value >= 0:
            safe[key] = value
    if isinstance(data.get("retryable"), bool):
        safe["retryable"] = data["retryable"]
    return safe


class AgentRuntime:
    """Bounded tool runner: steps are intents, attempts are handler executions,
    and token_budget caps generated tokens while prompt tokens remain observable.
    """

    def __init__(self, tools: list[ToolSpec], trace: Callable[[dict], Awaitable[None]],
                 *, max_steps: int = 5, max_attempts: int | None = None, token_budget: int = 2000):
        self.tools = {tool.name: tool for tool in tools}
        self.trace = trace
        self.max_steps = max_steps
        max_retries = max((tool.retry_limit for tool in tools), default=0)
        self.max_attempts = max_attempts if max_attempts is not None else max_steps * (max_retries + 1)
        self.token_budget = token_budget
        self.steps = 0
        self.tool_attempts = 0
        self.retries = 0
        self.input_tokens_used = 0
        self.output_tokens_used = 0
        self.total_tokens_used = 0
        self._reserved_output_tokens = 0
        self.calls: list[dict] = []

    @property
    def execution_metrics(self) -> dict[str, int]:
        return {
            "logical_steps": self.steps,
            "tool_attempts": self.tool_attempts,
            "retries": self.retries,
            "input_tokens": self.input_tokens_used,
            "output_tokens": self.output_tokens_used,
            "total_tokens": self.total_tokens_used,
            "output_token_budget": self.token_budget,
        }

    def reserve_output_tokens(self, requested: int) -> int:
        """Reserve generated-token capacity; prompt tokens are tracked separately."""
        if requested < 1:
            raise ToolFailure("INVALID_TOKEN_RESERVATION")
        remaining = self.token_budget - self.output_tokens_used - self._reserved_output_tokens
        if remaining <= 0:
            raise ToolFailure("TOKEN_BUDGET_EXCEEDED")
        reserved = min(requested, remaining)
        self._reserved_output_tokens += reserved
        return reserved

    def release_output_tokens(self, reserved: int) -> None:
        if reserved < 0 or reserved > self._reserved_output_tokens:
            raise ToolFailure("INVALID_TOKEN_RESERVATION")
        self._reserved_output_tokens -= reserved

    def record_token_usage(self, *, input_tokens: int, output_tokens: int, reserved: int,
                           total_tokens: int | None = None) -> None:
        if (input_tokens < 0 or output_tokens < 0 or reserved < 0 or reserved > self._reserved_output_tokens
                or total_tokens is not None and total_tokens < 0):
            raise ToolFailure("INVALID_TOKEN_USAGE")
        self._reserved_output_tokens -= reserved
        self.input_tokens_used += input_tokens
        self.output_tokens_used += output_tokens
        self.total_tokens_used += total_tokens if total_tokens is not None else input_tokens + output_tokens
        if output_tokens > reserved:
            raise ToolFailure("TOKEN_BUDGET_EXCEEDED")

    async def _record(self, trace: dict[str, Any]) -> None:
        self.calls.append(trace)
        await self.trace(trace)

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
            await self._record({
                "tool": name, "status": "REJECTED", "latency_ms": 0,
                "input_data": arguments, "output_data": None,
                "error_code": "INVALID_TOOL_INPUT", "logical_step": self.steps,
                "attempt": 0, "tool_attempts_total": self.tool_attempts, "retryable": False,
            })
            raise ToolFailure("INVALID_TOOL_INPUT") from exc

        for attempt in range(1, tool.retry_limit + 2):
            if self.tool_attempts >= self.max_attempts:
                await self._record({
                    "tool": name, "status": "REJECTED", "latency_ms": 0,
                    "input_data": validated.model_dump(mode="json"), "output_data": None,
                    "error_code": "ATTEMPT_LIMIT", "logical_step": self.steps,
                    "attempt": attempt, "tool_attempts_total": self.tool_attempts, "retryable": False,
                })
                raise ToolFailure("ATTEMPT_LIMIT")
            self.tool_attempts += 1
            if attempt > 1:
                self.retries += 1
            started = time.perf_counter()
            try:
                output = await asyncio.wait_for(tool.handler(validated), timeout=tool.timeout_seconds)
            except Exception as exc:
                retryable_types = (RetryableToolError, *tool.retryable_exceptions)
                if isinstance(exc, TimeoutError):
                    code, retryable = "TOOL_TIMEOUT", True
                elif isinstance(exc, ToolFailure):
                    code, retryable = exc.code, False
                elif isinstance(exc, retryable_types):
                    code, retryable = "TOOL_RETRYABLE_FAILURE", True
                elif isinstance(exc, tool.non_retryable_exceptions):
                    code, retryable = "TOOL_PROVIDER_ERROR", False
                else:
                    code, retryable = "TOOL_INTERNAL_ERROR", False
                await self._record({
                    "tool": name, "status": "FAILED",
                    "latency_ms": round((time.perf_counter() - started) * 1000),
                    "input_data": validated.model_dump(mode="json"), "output_data": None,
                    "error_code": code, "logical_step": self.steps, "attempt": attempt,
                    "tool_attempts_total": self.tool_attempts, "retryable": retryable,
                })
                if not retryable or attempt > tool.retry_limit:
                    raise ToolFailure(code) from exc
                continue

            await self._record({
                "tool": name, "status": "SUCCEEDED",
                "latency_ms": round((time.perf_counter() - started) * 1000),
                "input_data": validated.model_dump(mode="json"), "output_data": output,
                "error_code": None, "logical_step": self.steps, "attempt": attempt,
                "tool_attempts_total": self.tool_attempts, "retryable": False,
            })
            return output
        raise ToolFailure("TOOL_FAILED")
