"""Minimal MCP streamable HTTP tool adapter.

The caller provides an allowlist and the server URL; no arbitrary tool names are
forwarded from model output.
"""

import httpx
from travelpilot_core.ports.tools import ToolFailure


class MCPToolAdapter:
    def __init__(self, url: str, allowed_tools: set[str], token: str = "", timeout: float = 8):
        self.url = url
        self.allowed_tools = allowed_tools
        self.token = token
        self.timeout = timeout

    async def call(self, name: str, arguments: dict) -> dict:
        if name not in self.allowed_tools:
            raise ToolFailure("UNKNOWN_TOOL")
        headers = {"Accept": "application/json, text/event-stream"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(self.url, headers=headers, json={
                    "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                    "params": {"name": name, "arguments": arguments},
                })
                response.raise_for_status()
                data = response.json()
                if "error" in data or data.get("result", {}).get("isError"):
                    raise ToolFailure("MCP_TOOL_ERROR")
                return data["result"]
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            raise ToolFailure("MCP_UNAVAILABLE") from exc
