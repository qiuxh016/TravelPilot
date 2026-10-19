"""Model-selected ordering for compatible read-only/draft Advisor stages."""

from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from travelpilot_core.agents.planner import PlannerDecisionFailure, decide_next_action
from travelpilot_core.agents.runtime import AgentRuntime

_STAGE_SELECTION_TOKEN_CAP = 64
from travelpilot_core.ports.tools import ToolFailure

from travelpilot_api.advisor.orchestration_state import (
    resolve_stage_order,
    stage_observation_ref,
    stage_order_checkpoint_writer,
)


class EmptyStageInput(BaseModel):
    """Marker schema: the model chooses a stage but cannot supply its inputs."""


_STAGE_TO_TOOL = {
    "SEARCH_WEATHER": "run_search_weather_stage",
    "SCHEDULE": "run_schedule_stage",
    "ROUTE": "run_route_stage",
}


async def resolve_turn_stage_order(
    db: AsyncSession,
    *,
    owner_id: UUID,
    session_id: UUID,
    task_id: str,
    context_revision: int,
    stages: list[str],
    observation: dict[str, Any],
    retryable: bool,
    runtime: AgentRuntime,
    api_key: str,
    model: str,
    base_url: str,
    token_budget: int,
    selector: Callable[..., Awaitable[dict[str, Any]]] | None = None,
    resolver: Callable[..., Awaitable[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Select and checkpoint one bounded turn-stage ordering consistently."""
    observation_ref = stage_observation_ref(observation)

    async def choose() -> dict[str, Any]:
        select = selector or choose_stage_order
        return await select(
            stages=stages,
            observation=observation,
            runtime=runtime,
            api_key=api_key,
            model=model,
            base_url=base_url,
            token_budget=token_budget,
        )

    resolve = resolver or resolve_stage_order
    return await resolve(
        db,
        owner_id=owner_id,
        session_id=session_id,
        task_id=task_id,
        context_revision=context_revision,
        allowed_stages=stages,
        observation_ref=observation_ref,
        retryable=retryable,
        choose=choose,
        persist=stage_order_checkpoint_writer(
            owner_id=owner_id,
            session_id=session_id,
            task_id=task_id,
            context_revision=context_revision,
            allowed_stages=stages,
            observation_ref=observation_ref,
        ),
    )


async def choose_stage_order(
    *,
    stages: list[str],
    observation: dict[str, Any],
    runtime: AgentRuntime,
    api_key: str,
    model: str,
    base_url: str,
    token_budget: int,
) -> dict[str, Any]:
    """Let the model choose the first compatible stage; keep every stage required.

    The selector may reorder only independently authorized stages, which are
    authorized by the deterministic turn plan. It cannot omit a requested stage,
    alter tool arguments, or perform a persistent write. If the model is missing,
    malformed, out of budget, or unavailable, use the stable declared stage order.
    """
    eligible = [stage for stage in stages if stage in _STAGE_TO_TOOL]
    default_order = eligible.copy()
    if len(eligible) < 2:
        return {
            "order": default_order,
            "decision_source": "RULE",
            "decision_summary": "本轮只有一个兼容执行阶段，按授权范围继续",
            "fallback_reason": None,
        }
    if not api_key or not model:
        return {
            "order": default_order,
            "decision_source": "RULE",
            "decision_summary": "模型不可用，按固定安全顺序执行全部授权阶段",
            "fallback_reason": "MODEL_NOT_CONFIGURED",
        }

    tool_to_stage = {_STAGE_TO_TOOL[stage]: stage for stage in eligible}
    schemas = {
        tool: {"input_schema": EmptyStageInput.model_json_schema()}
        for tool in tool_to_stage
    }
    reserved = 0
    try:
        # Stage routing is a short control decision. Reserving the whole turn
        # allowance here can starve the actual route/schedule tool loop, so
        # leave most of a small turn budget for execution and grounded output.
        reserved = runtime.reserve_output_tokens(
            min(_STAGE_SELECTION_TOKEN_CAP, max(1, token_budget))
        )
        action, usage = await decide_next_action(
            {
                "task": "choose_next_compatible_advisor_stage",
                "user_goal": str(observation.get("user_goal", ""))[:500],
                "eligible_stages": eligible,
                "trip_facts": observation.get("trip_facts", {}),
                "completed_stage_observations": observation.get("completed_stage_observations", {}),
                "stage_contracts": observation.get("stage_contracts", {}),
            },
            schemas,
            api_key=api_key,
            model=model,
            base_url=base_url,
            token_budget=reserved,
        )
    except PlannerDecisionFailure as exc:
        runtime.record_token_usage(
            input_tokens=exc.usage.prompt_tokens,
            output_tokens=exc.usage.completion_tokens,
            total_tokens=exc.usage.total_tokens,
            reserved=reserved,
        )
        return {
            "order": default_order,
            "decision_source": "RULE_RECOVERY",
            "decision_summary": "模型阶段选择未通过校验，按固定安全顺序执行全部授权阶段",
            "fallback_reason": exc.code,
        }
    except ToolFailure as exc:
        if reserved:
            runtime.release_output_tokens(reserved)
        return {
            "order": default_order,
            "decision_source": "RULE_RECOVERY",
            "decision_summary": "模型阶段选择暂不可用，按固定安全顺序执行全部授权阶段",
            "fallback_reason": exc.code,
        }
    except Exception:  # noqa: BLE001 -- keep turn budget and stages recoverable
        if reserved:
            runtime.release_output_tokens(reserved)
        return {
            "order": default_order,
            "decision_source": "RULE_RECOVERY",
            "decision_summary": "模型阶段选择发生内部错误，按固定安全顺序执行全部授权阶段",
            "fallback_reason": "STAGE_SELECTOR_INTERNAL_ERROR",
        }
    try:
        runtime.record_token_usage(
            input_tokens=usage.prompt_tokens,
            output_tokens=usage.completion_tokens,
            total_tokens=usage.total_tokens,
            reserved=reserved,
        )
    except ToolFailure as exc:
        # Actual usage is retained by the runtime even when it exceeded its
        # reservation; do not let that invalid decision choose an action.
        return {
            "order": default_order,
            "decision_source": "RULE_RECOVERY",
            "decision_summary": "阶段选择超出本轮额度，按固定安全顺序执行全部授权阶段",
            "fallback_reason": exc.code,
        }
    if action.kind != "CALL_TOOL" or action.tool not in tool_to_stage:
        return {
            "order": default_order,
            "decision_source": "RULE_RECOVERY",
            "decision_summary": "模型未选择可执行阶段，按固定安全顺序执行全部授权阶段",
            "fallback_reason": "INVALID_STAGE_CHOICE",
        }

    first = tool_to_stage[action.tool]
    order = [first, *(stage for stage in eligible if stage != first)]
    return {
        "order": order,
        "decision_source": "MODEL",
        "decision_summary": action.summary[:180] or f"根据当前旅行观察先执行{_stage_label(first)}",
        "fallback_reason": None,
    }


def _stage_label(stage: str) -> str:
    return {
        "SEARCH_WEATHER": "天气与地点搜索",
        "SCHEDULE": "行程排程",
        "ROUTE": "路线查询",
    }.get(stage, stage)
