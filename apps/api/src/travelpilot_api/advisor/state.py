"""Small, persisted Agent interaction state derived from conversation messages."""

import hashlib
import json
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
    context_fingerprint: str | None = None


def pending_context_fingerprint(context: Any) -> str | None:
    """Fingerprint only durable trip facts, not transient chat selections/revisions."""
    if not isinstance(context, dict):
        return None
    trip = context.get("base_trip") if isinstance(context.get("base_trip"), dict) else {}
    plan = context.get("current_plan") if isinstance(context.get("current_plan"), dict) else {}
    inputs = context.get("planning_inputs") if isinstance(context.get("planning_inputs"), dict) else {}
    planning_state = context.get("planning_state") if isinstance(context.get("planning_state"), dict) else {}
    slots = planning_state.get("slots") if isinstance(planning_state.get("slots"), dict) else {}
    durable_slots = {
        name: (slots.get(name) or {}).get("value")
        for name in ("destination", "start_date", "end_date", "budget", "pace_level", "preferences", "origin", "companions", "hard_constraints")
        if isinstance(slots.get(name), dict)
    }
    value = {
        "trip": trip,
        "plan": {"id": plan.get("id"), "version": plan.get("version")},
        "planning_slots": durable_slots,
        "planning_inputs": inputs,
        "personal_memory_fingerprint": context.get("personal_memory_fingerprint"),
    }
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


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
        context_fingerprint=(
            payload.get("context_fingerprint")
            if isinstance(payload.get("context_fingerprint"), str)
            and len(payload.get("context_fingerprint")) == 64 else None
        ),
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
        context_fingerprint=(
            pending.get("context_fingerprint")
            if isinstance(pending.get("context_fingerprint"), str)
            and len(pending.get("context_fingerprint")) == 64 else None
        ),
    )


def pending_question_matches_context(pending: PendingAgentQuestion, context: Any) -> bool:
    """Reject a resumed turn only when durable trip facts changed after the question."""
    if pending.context_fingerprint is not None:
        return pending.context_fingerprint == pending_context_fingerprint(context)
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


def affirms_pending_action(question: str, answer: str) -> bool:
    """Recognize a short affirmative only when the pending turn asks permission to act."""
    normalized = answer.strip().casefold()
    if not normalized or len(normalized) > 80 or any(token in normalized for token in (
        "不需要", "不用", "不要", "不必", "不行", "不确认", "取消", "算了", "等等",
        "再想想", "不确定", "不知道", "先看看", "考虑一下", "稍后再说",
    )):
        return False
    affirmative_prefixes = (
        "好", "可以", "行", "没问题", "确认", "帮我确认", "继续", "请继续",
        "按这个", "就按", "就这样", "照此", "同意",
        "yes", "yeah", "yep", "okay", "ok", "go ahead",
    )
    if not normalized.startswith(affirmative_prefixes):
        return False
    # The pending prompt may use ordinary conversational wording instead of
    # the canonical "要我/需要我" form, e.g. "要现在一起补吗？". Require
    # both an interrogative and a concrete next action so preference questions
    # such as "你更喜欢自然还是历史文化？" cannot be mistaken for permission.
    asks_a_question = any(token in question for token in (
        "吗", "？", "?", "要不要", "是否", "需不需要", "可不可以", "能不能",
    ))
    proposes_action = any(token in question for token in (
        "安排", "推荐", "查询", "搜索", "查找", "排出来", "生成", "继续",
        "加入", "添加", "补", "完善", "调整", "核对", "确认",
    ))
    return asks_a_question and proposes_action


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
