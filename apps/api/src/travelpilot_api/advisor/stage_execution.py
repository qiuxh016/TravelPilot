"""Shared outcome accounting for bounded Advisor turn stages."""

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from travelpilot_api.advisor.turn_plan import AdvisorTurnPlan, ready_stage_names

_STAGE_OUTCOMES = frozenset({
    "SUCCEEDED", "NEEDS_USER_INPUT", "FAILED", "REJECTED", "FALLBACK", "STEP_LIMIT",
})
logger = logging.getLogger(__name__)


class AdvisorTurnExecutor:
    """Own stage outcomes and lifecycle checkpoints across one Advisor turn.

    Some stage inputs become available only after an earlier service boundary
    has merged verified evidence into the turn context. Repeated ``execute``
    calls therefore advance the same executor state instead of creating
    unrelated queues with reset transition numbering.
    """

    def __init__(
        self,
        plan: AdvisorTurnPlan,
        *,
        checkpoint: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        initial_transition: int = 0,
        recovery: dict[str, Any] | None = None,
    ) -> None:
        self.plan = plan
        self.checkpoint = checkpoint
        self.statuses: dict[str, str] = {}
        self.traces: dict[str, dict[str, Any]] = {}
        self.execution_order: list[str] = []
        self.selection_history: list[dict[str, Any]] = []
        recovered_decisions = recovery.get("selection_history", []) if recovery else []
        if isinstance(recovered_decisions, list):
            for record in recovered_decisions[:10]:
                if not isinstance(record, dict):
                    continue
                eligible = record.get("eligible_stages")
                order = record.get("order")
                source = record.get("decision_source")
                if (
                    not isinstance(eligible, list) or not isinstance(order, list)
                    or not eligible or len(eligible) > 3 or len(order) > 3
                    or not all(
                        isinstance(stage, str)
                        and stage in {"SEARCH_WEATHER", "SCHEDULE", "ROUTE"}
                        for stage in eligible + order
                    )
                    or len(set(eligible)) != len(eligible)
                    or len(set(order)) != len(order)
                    or set(eligible) != set(order)
                    or not isinstance(source, str)
                    or source not in {"MODEL", "RULE", "RUNTIME_GUARD", "RULE_RECOVERY"}
                ):
                    continue
                self.selection_history.append({
                    "eligible_stages": eligible,
                    "order": order,
                    "decision_source": "RECOVERED",
                    "original_decision_source": source,
                    "decision_summary": "上次尝试的阶段顺序仅供参考，本次会重新核验并决策",
                    "fallback_reason": None,
                })
        self.initial_transition = max(0, min(initial_transition, 1000))
        recovered_stages = recovery.get("completed_stages", []) if recovery else []
        if not isinstance(recovered_stages, list):
            recovered_stages = []
        completed_stages = [
            stage for stage in recovered_stages
            if isinstance(stage, str) and stage in {"SEARCH_WEATHER", "SCHEDULE", "ROUTE"}
        ][:3]
        interrupted_stage = recovery.get("interrupted_stage") if recovery else None
        self.recovery = {
            "resumed_same_task": True,
            "interrupted_stage": interrupted_stage
            if isinstance(interrupted_stage, str)
            and interrupted_stage in {"SEARCH_WEATHER", "SCHEDULE", "ROUTE"} else None,
            "completed_stages": completed_stages,
        } if recovery and recovery.get("has_previous_attempt") else None

    async def select_order(
        self,
        *,
        eligible_stages: list[str],
        selector: Callable[[list[str]], Awaitable[dict[str, Any]]],
    ) -> dict[str, Any]:
        """Ask the bounded policy to prioritize eligible stages and retain it.

        Selection may reorder the server-authorized set, but cannot remove an
        eligible stage or introduce an unplanned one.
        """
        planned = {stage.name for stage in self.plan.stages}
        eligible = list(dict.fromkeys(
            name for name in eligible_stages
            if name in planned and name not in self.statuses
        ))
        if not eligible:
            decision = {
                "order": [],
                "decision_source": "RULE",
                "decision_summary": "没有尚未执行的已授权阶段",
            }
        elif len(eligible) == 1:
            decision = {
                "order": eligible,
                "decision_source": "RULE",
                "decision_summary": "当前只有一个依赖已满足的阶段，继续执行",
            }
        else:
            try:
                decision = await selector(eligible)
            except Exception as error:  # noqa: BLE001 -- preserve authorized work
                decision = {
                    "order": eligible,
                    "decision_source": "RUNTIME_GUARD",
                    "decision_summary": "阶段选择暂不可用，按已授权顺序继续",
                    "fallback_reason": f"SELECTOR_ERROR:{type(error).__name__}",
                }
        proposed = decision.get("order", []) if isinstance(decision, dict) else []
        if not isinstance(proposed, list):
            proposed = []
        order = [name for name in proposed if name in eligible]
        order.extend(name for name in eligible if name not in order)
        normalized = {
            "order": order,
            "decision_source": str(decision.get("decision_source", "RULE_RECOVERY"))[:32]
            if isinstance(decision, dict) else "RULE_RECOVERY",
            "decision_summary": str(decision.get("decision_summary", ""))[:180]
            if isinstance(decision, dict) else "阶段选择无效，按授权顺序执行",
            "fallback_reason": str(decision.get("fallback_reason"))[:64]
            if isinstance(decision, dict) and decision.get("fallback_reason") else None,
        }
        history_item = {
            "eligible_stages": eligible,
            **normalized,
        }
        self.selection_history.append(history_item)
        if self.checkpoint and order:
            await self.checkpoint({
                "state": "STAGE_SELECTED",
                "transition": self.initial_transition + len(self.execution_order) + 1,
                "tool": order[0],
                "eligible_stages": eligible,
                "selected_order": order,
                "decision_source": normalized["decision_source"],
            })
        return normalized

    async def execute(
        self,
        *,
        requested_stages: set[str],
        preferred_order: list[str],
        runners: dict[str, Callable[[], Awaitable[dict[str, Any] | None]]],
        after_stage: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        """Advance this turn, retaining terminal outcomes from earlier phases."""
        pending = requested_stages - self.statuses.keys()
        while pending:
            ready = [
                name for name in ready_stage_names(
                    self.plan,
                    stage_statuses=self.statuses,
                    already_executed=set(self.statuses),
                )
                if name in pending and name in runners
            ]
            if not ready:
                break
            stage_name = next(
                (name for name in preferred_order if name in ready), ready[0],
            )
            transition = self.initial_transition + len(self.execution_order) + 1
            if self.checkpoint:
                await self.checkpoint({
                    "state": "STAGE_STARTED",
                    "transition": transition,
                    "tool": stage_name,
                })
            try:
                trace = await runners[stage_name]()
                if after_stage and isinstance(trace, dict):
                    await after_stage(stage_name, trace)
            except Exception as error:  # isolate one stage failure
                # A failed stage must not prevent unrelated authorized work from
                # completing. Keep provider/user details out of persisted traces.
                logger.exception("Advisor stage runner failed", extra={"stage": stage_name})
                trace = {
                    "status": "FAILED",
                    "decision_source": "RUNTIME_GUARD",
                    "decision_summary": "阶段执行意外失败；可独立执行的阶段仍会继续",
                    "error_code": "STAGE_EXECUTION_ERROR",
                    "failure_type": type(error).__name__[:64],
                }
            self.execution_order.append(stage_name)
            pending.remove(stage_name)
            if (
                isinstance(trace, dict)
                and isinstance(trace.get("status"), str)
                and trace["status"] in _STAGE_OUTCOMES
            ):
                self.traces[stage_name] = trace
            else:
                # A runner that returned no structured outcome must never be
                # treated as successful or silently disappear from the queue.
                # Mark it failed so dependent stages remain blocked.
                trace = {
                    "status": "FAILED",
                    "decision_source": "RUNTIME_GUARD",
                    "decision_summary": "阶段没有返回有效结果，依赖此结果的步骤已暂停",
                    "error_code": "INVALID_STAGE_OUTCOME",
                }
                self.traces[stage_name] = trace
            self.statuses[stage_name] = str(trace["status"])
            if self.checkpoint:
                await self.checkpoint({
                    "state": self.statuses[stage_name],
                    "transition": transition,
                    "tool": stage_name,
                })
        return {
            "traces": dict(self.traces),
            "statuses": dict(self.statuses),
            "execution_order": list(self.execution_order),
            "remaining": sorted(pending),
        }

    async def execute_adaptive(
        self,
        *,
        requested_stages: set[str],
        runners: dict[str, Callable[[], Awaitable[dict[str, Any] | None]]],
        selector: Callable[
            [list[str], dict[str, str], dict[str, dict[str, Any]]],
            Awaitable[dict[str, Any]],
        ],
        after_stage: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        """Select one ready action, observe its outcome, then choose again.

        The selector only receives stages authorized by the turn plan whose
        dependencies have succeeded. Every requested runnable stage remains
        mandatory. Re-evaluating after each outcome lets the next choice react
        to current tool evidence instead of following one stale batch order.
        """
        requested = set(requested_stages)
        while True:
            ready = [
                stage for stage in ready_stage_names(
                    self.plan,
                    stage_statuses=self.statuses,
                    already_executed=set(self.statuses),
                )
                if stage in requested and stage in runners
            ]
            if not ready:
                break
            decision = await self.select_order(
                eligible_stages=ready,
                selector=lambda eligible: selector(
                    eligible, dict(self.statuses), dict(self.traces),
                ),
            )
            next_stage = decision["order"][0]
            await self.execute(
                requested_stages={next_stage},
                preferred_order=[next_stage],
                runners={next_stage: runners[next_stage]},
                after_stage=after_stage,
            )
        return {
            "traces": dict(self.traces),
            "statuses": dict(self.statuses),
            "execution_order": list(self.execution_order),
            "remaining": sorted(requested - self.statuses.keys()),
        }


async def execute_ready_stage_queue(
    plan: AdvisorTurnPlan,
    *,
    requested_stages: set[str],
    initial_statuses: dict[str, str],
    preferred_order: list[str],
    runners: dict[str, Callable[[], Awaitable[dict[str, Any] | None]]],
    checkpoint: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
) -> dict[str, Any]:
    """Run every ready authorized stage in a bounded, dependency-aware queue.

    `preferred_order` only changes which ready stage runs first. It cannot add
    stages, skip another requested stage, or bypass a failed dependency.
    """
    executor = AdvisorTurnExecutor(plan, checkpoint=checkpoint)
    executor.statuses.update(initial_statuses)
    return await executor.execute(
        requested_stages=requested_stages,
        preferred_order=preferred_order,
        runners=runners,
    )


def summarize_stage_execution(
    plan: AdvisorTurnPlan,
    traces: dict[str, dict[str, Any] | None],
) -> dict[str, Any]:
    """Return a complete, dependency-aware status for every planned stage.

    A missing trace is classified as blocked only when a declared prerequisite
    did not succeed. A ready stage without a trace is surfaced as NOT_EXECUTED,
    which distinguishes an executor omission from a genuine dependency block.
    """
    statuses = {
        stage.name: str(traces[stage.name].get("status", "UNKNOWN"))[:32]
        for stage in plan.stages
        if isinstance(traces.get(stage.name), dict)
    }
    records: list[dict[str, str]] = []
    for stage in plan.stages:
        trace = traces.get(stage.name)
        if isinstance(trace, dict):
            status = statuses[stage.name]
            source = str(trace.get("decision_source", "UNKNOWN"))[:32]
            summary = str(trace.get("decision_summary", ""))[:240]
        else:
            failed_dependencies = [
                dependency for dependency in stage.depends_on
                if statuses.get(dependency) != "SUCCEEDED"
            ]
            if failed_dependencies:
                status = "BLOCKED_DEPENDENCY"
                source = "RUNTIME_GUARD"
                summary = "前置阶段未成功，本阶段未执行"
            else:
                status = "NOT_EXECUTED"
                source = "RUNTIME_GUARD"
                summary = "本阶段已就绪但未执行"
            statuses[stage.name] = status
        record = {
            "stage": stage.name,
            "status": status,
            "decision_source": source,
            "decision_summary": summary,
        }
        if isinstance(trace, dict):
            for key in ("error_code", "failure_type"):
                value = trace.get(key)
                if isinstance(value, str) and value and value.replace("_", "").isalnum():
                    record[key] = value[:64].upper()
        records.append(record)

    missing = [record["stage"] for record in records if record["status"] in {
        "BLOCKED_DEPENDENCY", "NOT_EXECUTED",
    }]
    terminal = [record["status"] for record in records]
    waiting = "NEEDS_USER_INPUT" in terminal
    succeeded = any(status == "SUCCEEDED" for status in terminal)
    overall = (
        "NEEDS_USER_INPUT" if waiting
        else "PARTIAL" if missing and succeeded
        else "FAILED" if missing
        else None
    )
    return {
        "stage_statuses": records,
        "unexecuted_stages": missing,
        "overall_status": overall,
    }
