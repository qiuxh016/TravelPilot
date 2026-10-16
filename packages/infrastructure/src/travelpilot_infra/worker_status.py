"""Shared Redis heartbeat settings for background worker health checks."""

WORKER_HEARTBEAT_PREFIX = "travelpilot:worker:heartbeat:"
WORKER_HEARTBEAT_TTL_SECONDS = 45
WORKER_HEARTBEAT_INTERVAL_SECONDS = 15
