"""Bounded per-session model output budget and explicit continuation consent."""

from typing import Any, Literal

USAGE_KEY = "advisor_llm_usage"
EXTENSION_KEY = "advisor_llm_budget_extension"
PENDING_KEY = "advisor_llm_budget_pending"
BudgetAction = Literal["NONE", "PROMPT", "CONTINUE", "CANCEL"]


def _count(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def session_usage(context: dict) -> dict[str, int]:
    stored = context.get(USAGE_KEY)
    stored = stored if isinstance(stored, dict) else {}
    input_tokens = _count(stored.get("input_tokens"))
    output_tokens = _count(stored.get("output_tokens"))
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
    }


def remaining_session_output_tokens(context: dict, configured_limit: int) -> int:
    limit = session_output_limit(context, configured_limit)
    return max(0, limit - session_usage(context)["output_tokens"])


def session_output_limit(context: dict, configured_limit: int) -> int:
    extension = min(_count(context.get(EXTENSION_KEY)), 1_000_000)
    return _count(configured_limit) + extension


def record_session_usage(context: dict, input_tokens: int, output_tokens: int) -> dict[str, int]:
    current = session_usage(context)
    input_total = current["input_tokens"] + _count(input_tokens)
    output_total = current["output_tokens"] + _count(output_tokens)
    updated = {
        "input_tokens": input_total,
        "output_tokens": output_total,
        "total_tokens": input_total + output_total,
    }
    context[USAGE_KEY] = updated
    return updated


def handle_budget_continuation(
    context: dict,
    message: str,
    *,
    per_turn_grant: int,
) -> tuple[BudgetAction, str | None, int]:
    """Process a queued message, granting one bounded extension only on consent."""
    pending = context.get(PENDING_KEY)
    if not isinstance(pending, dict):
        return "NONE", None, 0
    normalized = message.strip().casefold()
    if normalized in {"继续", "继续本次对话", "继续使用模型额度", "继续这次任务"}:
        queued = pending.get("input")
        goal = queued.strip()[:1200] if isinstance(queued, str) and queued.strip() else None
        if goal is None:
            context.pop(PENDING_KEY, None)
            return "CANCEL", None, 0
        grant = min(_count(per_turn_grant), 8000)
        context[EXTENSION_KEY] = min(_count(context.get(EXTENSION_KEY)) + grant, 1_000_000)
        context.pop(PENDING_KEY, None)
        return "CONTINUE", goal, grant
    if normalized in {"取消", "取消继续", "不用了", "先不用", "算了", "停止"}:
        context.pop(PENDING_KEY, None)
        context.pop("pending_agent_question", None)
        return "CANCEL", None, 0
    context[PENDING_KEY] = {"input": message.strip()[:1200]}
    return "PROMPT", None, 0


def queue_budget_continuation(context: dict, message: str) -> None:
    context[PENDING_KEY] = {"input": message.strip()[:1200]}
