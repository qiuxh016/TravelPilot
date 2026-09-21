"""Typed tool boundary used by agents, independent from transport."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel


@dataclass(frozen=True)
class ToolSpec:
    name: str
    input_schema: type[BaseModel]
    handler: Callable[[BaseModel], Awaitable[dict[str, Any]]]
    timeout_seconds: float = 8
    retry_limit: int = 1


class ToolFailure(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code
