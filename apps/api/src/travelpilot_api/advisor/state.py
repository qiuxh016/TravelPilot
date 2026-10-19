"""Small, persisted Agent interaction state derived from conversation messages."""

from dataclasses import dataclass, replace
from typing import Any

from travelpilot_api.advisor.task_taxonomy import ADVISOR_TASK_TYPES


@dataclass(frozen=True)
class PendingAgentQuestion:
    task: str
    question: str
    context_revision: int | None = None
    task_type: str = "SCHEDULE"
    task_id: str | None = None
    answers: tuple[str, ...] = ()
    turn_number: int = 1
    task_kind: str | None = None


def _bounded_answers(value: Any) -> tuple[str, ...] | None:
    if value is None:
        return ()
    if not isinstance(value, list) or len(value) > 6:
        return None
    if any(not isinstance(item, str) or not item.strip() for item in value):
        return None
    return tuple(item.strip()[:500] for item in value)


def pending_agent_question(payload: Any) -> PendingAgentQuestion | None:
    if not isinstance(payload, dict):
        return None
    trace = payload.get("agent_trace")
    if not isinstance(trace, dict) or trace.get("status") != "NEEDS_USER_INPUT":
        return None
    task_type = payload.get("pending_task_type")
    if task_type not in ADVISOR_TASK_TYPES:
        if payload.get("schedule_recommendation_requested"):
            task_type = "SCHEDULE"
        else:
            return None
    task = payload.get("pending_task")
    question = payload.get("pending_question")
    if not isinstance(task, str) or not task.strip() or not isinstance(question, str) or not question.strip():
        return None
    revision = payload.get("context_revision")
    if isinstance(revision, bool) or (revision is not None and not isinstance(revision, int)):
        return None
    answers = _bounded_answers(payload.get("pending_answers"))
    turn_number = payload.get("pending_turn_number", len(answers or ()) + 1)
    task_id = payload.get("pending_task_id")
    task_kind = payload.get("pending_task_kind")
    if answers is None or isinstance(turn_number, bool) or not isinstance(turn_number, int) or not 1 <= turn_number <= 100:
        return None
    if task_id is not None and (not isinstance(task_id, str) or len(task_id) > 64):
        return None
    if task_kind is not None and task_kind not in {"DESTINATION_RECOMMENDATION"}:
        return None
    return PendingAgentQuestion(
        task=task.strip()[:1200],
        question=question.strip()[:500],
        context_revision=revision,
        task_type=task_type,
        task_id=task_id,
        answers=answers,
        turn_number=turn_number,
        task_kind=task_kind,
    )


def pending_agent_question_from_context(context: Any) -> PendingAgentQuestion | None:
    if not isinstance(context, dict):
        return None
    pending = context.get("pending_agent_question")
    if not isinstance(pending, dict):
        return None
    task = pending.get("task")
    question = pending.get("question")
    revision = pending.get("context_revision")
    if (
        not isinstance(task, str) or not task.strip()
        or not isinstance(question, str) or not question.strip()
        or isinstance(revision, bool) or not isinstance(revision, int)
    ):
        return None
    task_type = pending.get("task_type", "SCHEDULE")
    if task_type not in ADVISOR_TASK_TYPES:
        return None
    answers = _bounded_answers(pending.get("answers"))
    turn_number = pending.get("turn_number", len(answers or ()) + 1)
    task_id = pending.get("task_id")
    task_kind = pending.get("task_kind")
    if answers is None or isinstance(turn_number, bool) or not isinstance(turn_number, int) or not 1 <= turn_number <= 100:
        return None
    if task_id is not None and (not isinstance(task_id, str) or len(task_id) > 64):
        return None
    if task_kind is not None and task_kind not in {"DESTINATION_RECOMMENDATION"}:
        return None
    return PendingAgentQuestion(
        task=task.strip()[:1200], question=question.strip()[:500],
        context_revision=revision, task_type=task_type, task_id=task_id,
        answers=answers, turn_number=turn_number,
        task_kind=task_kind,
    )


def pending_question_matches_context(pending: PendingAgentQuestion, context: Any) -> bool:
    """Reject a resumed turn when the trip/session changed after the question."""
    if pending.context_revision is None or not isinstance(context, dict):
        return pending.context_revision is None
    revision = context.get("revision")
    return isinstance(revision, int) and not isinstance(revision, bool) and revision == pending.context_revision


def resume_pending_task(pending: PendingAgentQuestion, answer: str) -> tuple[PendingAgentQuestion, str]:
    """Append one bounded answer and rebuild the original task for the next turn."""
    clean_answer = answer.strip()[:500]
    if not clean_answer:
        return pending, pending.task
    updated = replace(
        pending,
        answers=(*pending.answers, clean_answer)[-6:],
        turn_number=min(100, pending.turn_number + 1),
    )
    answers = "\n".join(f"- {item}" for item in updated.answers)
    return updated, (
        f"原始任务：{updated.task}\n"
        f"先前追问：{updated.question}\n"
        f"用户补充信息：\n{answers}"
    )


def cancels_pending_task(answer: str) -> bool:
    normalized = answer.strip().casefold()
    return normalized in {
        "取消", "取消吧", "算了", "不用了", "先不用", "停止", "不继续了",
        "cancel", "stop", "never mind",
    }


def cancelled_task_reply(task_type: str | None) -> str:
    """Acknowledge cancellation in terms of the actual pending travel task."""
    task_labels = {
        "SEARCH": "地点搜索", "DISCOVERY": "目的地探索", "SCHEDULE": "行程调整",
        "MEAL": "餐饮推荐", "LODGING": "住宿搜索", "TRANSPORT": "交通查询",
        "WEATHER": "天气查询", "PREPARATION": "出行准备建议", "MEMORY": "旅行记录处理",
        "GENERAL": "这项旅行咨询",
    }
    label = task_labels.get(task_type or "", "这项旅行任务")
    return f"好的，{label}已停止；当前旅行数据没有变化。"
