"""Fail-closed local forwarding proxy for explicitly bounded model evaluations."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response


@dataclass
class Budget:
    max_requests: int = 120
    max_requested_output_tokens: int = 12_288
    max_output_tokens_per_request: int = 512
    admitted_requests: int = 0
    rejected_requests: int = 0
    requested_output_tokens: int = 0
    lock: asyncio.Lock | None = None

    def __post_init__(self) -> None:
        self.lock = asyncio.Lock()


def create_app(
    *,
    upstream_base_url: str,
    budget: Budget | None = None,
    client: httpx.AsyncClient | None = None,
) -> FastAPI:
    upstream = urlsplit(upstream_base_url)
    if (
        upstream.scheme != "https"
        or not upstream.netloc
        or upstream.hostname != "api.deepseek.com"
        or upstream.port not in {None, 443}
        or upstream.username is not None
        or upstream.password is not None
        or upstream.path.rstrip("/") != "/v1"
        or upstream.query
        or upstream.fragment
    ):
        raise ValueError("upstream must be an HTTPS base URL without credentials or query")
    normalized_base = upstream_base_url.rstrip("/")
    limits = budget or Budget()
    if min(
        limits.max_requests,
        limits.max_requested_output_tokens,
        limits.max_output_tokens_per_request,
    ) < 1:
        raise ValueError("all proxy budget limits must be positive")
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.eval_budget = limits
    app.state.upstream_base_url = normalized_base
    app.state.upstream_client = client

    @app.get("/budget")
    @app.get("/v1/budget")
    async def get_budget() -> dict[str, int | str]:
        async with limits.lock:
            return {
                "upstream_origin": f"https://{upstream.netloc}",
                "max_requests": limits.max_requests,
                "max_requested_output_tokens": limits.max_requested_output_tokens,
                "max_output_tokens_per_request": limits.max_output_tokens_per_request,
                "admitted_requests": limits.admitted_requests,
                "rejected_requests": limits.rejected_requests,
                "requested_output_tokens": limits.requested_output_tokens,
                "remaining_requests": max(0, limits.max_requests - limits.admitted_requests),
                "remaining_requested_output_tokens": max(
                    0, limits.max_requested_output_tokens - limits.requested_output_tokens,
                ),
            }

    @app.post("/{api_path:path}")
    async def forward(api_path: str, request: Request) -> Response:
        if api_path != "v1/chat/completions":
            return JSONResponse({"error": {"code": "EVAL_PROXY_PATH_DENIED"}}, status_code=404)
        try:
            body: Any = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            return JSONResponse({"error": {"code": "EVAL_PROXY_INVALID_JSON"}}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"error": {"code": "EVAL_PROXY_INVALID_BODY"}}, status_code=400)
        requested = body.get("max_tokens", body.get("max_completion_tokens"))
        if isinstance(requested, bool) or not isinstance(requested, int) or requested < 1:
            return JSONResponse({"error": {"code": "EVAL_PROXY_TOKEN_CAP_REQUIRED"}}, status_code=400)
        bounded = min(requested, limits.max_output_tokens_per_request)
        body["max_tokens"] = bounded
        body.pop("max_completion_tokens", None)

        async with limits.lock:
            if (
                limits.admitted_requests >= limits.max_requests
                or limits.requested_output_tokens + bounded > limits.max_requested_output_tokens
            ):
                limits.rejected_requests += 1
                return JSONResponse(
                    {"error": {"code": "EVAL_PROXY_BUDGET_EXHAUSTED"}}, status_code=429,
                )
            # Reserve before forwarding, so concurrent requests cannot overspend.
            limits.admitted_requests += 1
            limits.requested_output_tokens += bounded

        headers = {
            "content-type": "application/json",
            "authorization": request.headers.get("authorization", ""),
        }
        upstream_client = app.state.upstream_client
        owns_client = upstream_client is None
        if owns_client:
            upstream_client = httpx.AsyncClient(timeout=45)
        try:
            result = await upstream_client.post(
                f"{normalized_base}/chat/completions", headers=headers, json=body,
            )
        except httpx.HTTPError:
            return JSONResponse(
                {"error": {"code": "EVAL_PROXY_UPSTREAM_UNAVAILABLE"}}, status_code=502,
            )
        finally:
            if owns_client:
                await upstream_client.aclose()
        return Response(
            content=result.content,
            status_code=result.status_code,
            headers={"content-type": result.headers.get("content-type", "application/json")},
        )

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8022)
    parser.add_argument("--upstream-base-url", default=os.environ.get("EVAL_MODEL_UPSTREAM_BASE_URL", ""))
    parser.add_argument("--max-requests", type=int, default=120)
    parser.add_argument("--max-requested-output-tokens", type=int, default=12_288)
    parser.add_argument("--max-output-tokens-per-request", type=int, default=512)
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "::1", "localhost"}:
        parser.error("evaluation proxy must bind to loopback only")
    if not args.upstream_base_url:
        parser.error("set --upstream-base-url to an HTTPS model API base URL")
    if min(args.max_requests, args.max_requested_output_tokens, args.max_output_tokens_per_request) < 1:
        parser.error("all budget limits must be positive")

    import uvicorn

    uvicorn.run(
        create_app(
            upstream_base_url=args.upstream_base_url,
            budget=Budget(
                max_requests=args.max_requests,
                max_requested_output_tokens=args.max_requested_output_tokens,
                max_output_tokens_per_request=args.max_output_tokens_per_request,
            ),
        ),
        host=args.host,
        port=args.port,
        access_log=False,
    )


if __name__ == "__main__":
    main()
