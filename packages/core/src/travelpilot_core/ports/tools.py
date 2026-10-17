"""Typed tool boundary used by agents, independent from transport."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel


@dataclass(frozen=True)
class ToolSpec:
    """Tool contract; only explicitly classified transient exceptions are retried."""

    name: str
    input_schema: type[BaseModel]
    handler: Callable[[BaseModel], Awaitable[dict[str, Any]]]
    timeout_seconds: float = 8
    retry_limit: int = 1
    retryable_exceptions: tuple[type[Exception], ...] = ()
    non_retryable_exceptions: tuple[type[Exception], ...] = ()


class ToolFailure(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class RetryableToolError(Exception):
    """An explicitly classified transient tool error that may be retried."""
