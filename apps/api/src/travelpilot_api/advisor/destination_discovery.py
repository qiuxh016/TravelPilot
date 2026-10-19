"""Bounded, observation-driven destination discovery using live map evidence."""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from travelpilot_core.agents.loop import (
    AgentAction,
    AgentTaskContext,
    LoopResult,
    run_constrained_loop,
)
from travelpilot_core.agents.planner import PlannerDecisionFailure, decide_next_action
from travelpilot_core.agents.runtime import AgentRuntime
from travelpilot_core.ports.tools import ToolFailure, ToolSpec
from travelpilot_infra.providers.maps.errors import MapsProviderError

from travelpilot_api.advisor.interpretation import _DESTINATION_CITIES
from travelpilot_api.advisor.orchestration_state import readonly_action_ref, stage_observation_ref
from travelpilot_api.advisor.tools import map_popularity_score
from travelpilot_api.settings import settings


class DestinationSearchInput(BaseModel):
    """The Agent chooses only from request-scoped city and interest indexes."""

    model_config = ConfigDict(extra="forbid")

    city: str = Field(min_length=1, max_length=80)
    interest_index: int = Field(ge=0, le=1)


class DestinationDiscoveryResult:
    def __init__(self, *, searches: list[dict[str, Any]], loop: LoopResult,
                 runtime: AgentRuntime, decision_source: str, decision_summary: str):
        self.searches = searches
        self.loop = loop
        self.runtime = runtime
        self.decision_source = decision_source
        self.decision_summary = decision_summary


def _verified_result(value: dict[str, Any], expected_city: str, expected_interest_index: int) -> bool:
    interest_index = value.get("interest_index")
    if (
        value.get("city") != expected_city
        or isinstance(interest_index, bool)
        or not isinstance(interest_index, int)
        or interest_index != expected_interest_index
    ):
        return False
    retrieved_at = value.get("retrieved_at")
    if not isinstance(retrieved_at, str):
        return False
    try:
        retrieved = datetime.fromisoformat(retrieved_at)
    except ValueError:
        return False
    if retrieved.tzinfo is None:
        return False
    age = (datetime.now(UTC) - retrieved.astimezone(UTC)).total_seconds()
    if not 0 <= age <= 600:
        return False
    places = value.get("places")
    if value.get("status") not in {"VERIFIED", "NO_RESULTS"} or not isinstance(places, list) or len(places) > 100:
        return False
    if (value["status"] == "VERIFIED" and not places) or (
        value["status"] == "NO_RESULTS" and places
    ):
        return False
    return all(
        isinstance(place, dict)
        and place.get("provider") == "amap"
        and isinstance(place.get("provider_id"), str) and bool(place["provider_id"])
        and len(place["provider_id"]) <= 120
        and isinstance(place.get("name"), str) and bool(place["name"]) and len(place["name"]) <= 160
        and isinstance(place.get("latitude"), (int, float)) and not isinstance(place["latitude"], bool)
        and isinstance(place.get("longitude"), (int, float)) and not isinstance(place["longitude"], bool)
        and -90 <= place["latitude"] <= 90
        and -180 <= place["longitude"] <= 180
        for place in places
    )


async def run_destination_discovery(
    *, maps, queries: list[tuple[str, str]], user_goal: str,
    api_key: str = "", model: str = "", base_url: str = "", token_budget: int = 0,
    cached_searches: dict[tuple[str, int], dict[str, Any]] | None = None,
    approved_cities: list[str] | None = None,
    search_observation_callback: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    checkpoint: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    load_decision_checkpoint: Callable[..., Awaitable[dict[str, str] | None]] | None = None,
    save_decision_checkpoint: Callable[..., Awaitable[None]] | None = None,
) -> DestinationDiscoveryResult:
    """Search a maximum of 12 request-approved city/interest pairs.

    The model cannot create cities or search phrases. The server maps its bounded
    indices to the approved shortlist and keeps final ranking outside the model.
    """
    approved_queries = queries[:2]
    cities = list(dict.fromkeys(city.strip() for city in (approved_cities or _DESTINATION_CITIES) if city.strip()))
    cities = cities[:max(1, 12 // max(1, len(approved_queries)))]
    pairs = [(city, index) for city in cities for index in range(len(approved_queries))]
    attempted: set[tuple[str, int]] = set()
    current: list[tuple[str, int] | None] = [None]
    searches: list[dict[str, Any]] = []
    decision_source = "RULE"
    decision_summary = "按有限城市与兴趣范围搜索目的地证据"
    selected_decision: dict[str, Any] = {}

    async def search(arguments: DestinationSearchInput) -> dict[str, Any]:
        if arguments.city not in cities or arguments.interest_index >= len(approved_queries):
            raise ToolFailure("DESTINATION_SEARCH_OUT_OF_SCOPE")
        label, keyword = approved_queries[arguments.interest_index]
        cached = (cached_searches or {}).get((arguments.city, arguments.interest_index))
        if isinstance(cached, dict):
            return {
                **cached,
                "city": arguments.city,
                "interest_index": arguments.interest_index,
                "interest": label,
            }
        try:
            places = await maps.search_place(keyword, arguments.city)
        except MapsProviderError as exc:
            raise ToolFailure("MAPS_PROVIDER_UNAVAILABLE") from exc
        verified = [place for place in places if place.provider == "amap"]
        return {
            "city": arguments.city,
            "interest_index": arguments.interest_index,
            "interest": label,
            "status": "VERIFIED" if verified else "NO_RESULTS",
            "retrieved_at": datetime.now(UTC).isoformat(),
            "places": [{
                "provider": place.provider,
                "provider_id": str(place.provider_id)[:120],
                "name": str(place.name)[:160],
                "category": str(place.category or "")[:80],
                "address": str(place.address or "")[:240],
                "latitude": place.latitude,
                "longitude": place.longitude,
                "popularity_score": map_popularity_score(getattr(place, "raw_metadata", None)),
            } for place in verified[:100]],
        }

    async def no_trace(_: dict) -> None:
        return None

    runtime = AgentRuntime(
        [ToolSpec("search_destination_places", DestinationSearchInput, search,
                  timeout_seconds=settings.maps_timeout_seconds, retry_limit=0)],
        no_trace,
        max_steps=12,
        max_attempts=12,
        token_budget=max(0, token_budget),
    )
    tool_schema = {"search_destination_places": {
        "input_schema": DestinationSearchInput.model_json_schema(),
    }}

    def decision_context(observation, remaining):
        prompt_observation = {
            "task": "discover_destination_from_live_map_evidence",
            "user_goal": user_goal[:500],
            "approved_cities": cities,
            "approved_interests": [label for label, _ in approved_queries],
            "remaining_search_pairs": [
                {"city": city, "interest_index": index,
                 "interest": approved_queries[index][0]}
                for city, index in remaining
            ],
            "observed_searches": [{
                "city": item["city"], "interest": item["interest"],
                "status": item["status"], "result_count": len(item["places"]),
            } for item in searches],
        }
        return {**prompt_observation, "loop_observation": observation}, prompt_observation

    async def policy(observation: dict[str, Any], allowed_tools: list[str]) -> AgentAction:
        nonlocal decision_source, decision_summary
        remaining = [pair for pair in pairs if pair not in attempted]
        if not remaining:
            return AgentAction(kind="FINISH", summary="已完成本轮有界目的地搜索")
        scoped_observation, prompt_observation = decision_context(observation, remaining)
        if not api_key.strip() or not model.strip() or not token_budget:
            selected = remaining[0]
            decision_source = "RULE"
            decision_summary = "模型不可用或额度不足，按固定顺序检索有限候选"
        else:
            reserved = 0
            try:
                reserved = runtime.reserve_output_tokens(min(128, token_budget))
                action, usage = await decide_next_action(
                    prompt_observation,
                    {name: schema for name, schema in tool_schema.items() if name in allowed_tools},
                    api_key=api_key, model=model, base_url=base_url,
                    token_budget=reserved,
                )
            except PlannerDecisionFailure as exc:
                runtime.record_token_usage(
                    input_tokens=exc.usage.prompt_tokens, output_tokens=exc.usage.completion_tokens,
                    total_tokens=exc.usage.total_tokens, reserved=reserved,
                )
                action = None
                decision_source = "RULE_RECOVERY"
                decision_summary = "模型动作未通过校验，回退到剩余的有限搜索范围"
            except ToolFailure:
                if reserved:
                    runtime.release_output_tokens(reserved)
                action = None
                decision_source = "RULE_RECOVERY"
                decision_summary = "模型决策暂不可用，回退到剩余的有限搜索范围"
            else:
                runtime.record_token_usage(
                    input_tokens=usage.prompt_tokens, output_tokens=usage.completion_tokens,
                    total_tokens=usage.total_tokens, reserved=reserved,
                )
                decision_source = "MODEL"
                decision_summary = action.summary[:180] or "根据已观察的地图证据选择下一步"
            if action is not None and action.kind in {"ASK_USER", "FINISH"}:
                return action
            arguments = action.arguments if action and action.kind == "CALL_TOOL" else {}
            selected = next((pair for pair in remaining
                             if pair[0] == arguments.get("city")
                             and pair[1] == arguments.get("interest_index")), None)
            if selected is None:
                selected = remaining[0]
                decision_source = "RULE_RECOVERY"
                decision_summary = "Agent 选择超出本轮搜索范围，已限制为剩余候选"
        current[0] = selected
        selected_action = AgentAction(
            kind="CALL_TOOL", tool="search_destination_places",
            arguments={"city": selected[0], "interest_index": selected[1]},
            summary=decision_summary,
        )
        selected_ref = readonly_action_ref(selected_action.tool, selected_action.arguments)
        selected_decision.clear()
        selected_decision.update({
            "observation_ref": stage_observation_ref(scoped_observation),
            "action_ref": selected_ref,
            "approved_actions": {
                readonly_action_ref("search_destination_places", {
                    "city": city, "interest_index": index,
                }): "search_destination_places"
                for city, index in remaining
            },
        })
        attempted.add(selected)
        return selected_action

    async def recover_decision(observation, allowed_tools, decision_number):
        if load_decision_checkpoint is None or "search_destination_places" not in allowed_tools:
            return None
        remaining = [pair for pair in pairs if pair not in attempted]
        if not remaining:
            return None
        scoped_observation, _ = decision_context(observation, remaining)
        actions = {
            readonly_action_ref("search_destination_places", {
                "city": city, "interest_index": index,
            }): (city, index)
            for city, index in remaining
        }
        recovered = await load_decision_checkpoint(
            observation_ref=stage_observation_ref(scoped_observation),
            decision_number=decision_number,
            approved_actions={key: "search_destination_places" for key in actions},
        )
        pair = actions.get(recovered.get("action_ref", "")) if recovered else None
        if pair is None or recovered.get("tool") != "search_destination_places":
            return None
        current[0] = pair
        attempted.add(pair)
        nonlocal decision_source, decision_summary
        decision_source = "RECOVERED"
        decision_summary = "恢复中断前已批准的目的地检索"
        return AgentAction(
            kind="CALL_TOOL", tool="search_destination_places",
            arguments={"city": pair[0], "interest_index": pair[1]},
            summary=decision_summary,
        )

    async def persist_decision(_observation, action, decision_number):
        if (
            save_decision_checkpoint is None
            or action.tool != "search_destination_places"
            or selected_decision.get("action_ref")
            != readonly_action_ref(action.tool, action.arguments)
        ):
            return
        await save_decision_checkpoint(
            observation_ref=selected_decision["observation_ref"],
            decision_number=decision_number,
            approved_actions=selected_decision["approved_actions"],
            tool=action.tool,
            action_ref=selected_decision["action_ref"],
        )

    async def verifier(name: str, output: dict[str, Any]) -> bool:
        selected = current[0]
        if name != "search_destination_places" or selected is None:
            return False
        valid = _verified_result(output, *selected)
        if valid:
            searches.append(output)
            if search_observation_callback is not None:
                await search_observation_callback(output)
        return valid

    async def save_checkpoint(state: dict[str, Any]) -> None:
        if checkpoint is not None:
            await checkpoint(state)

    task_context = AgentTaskContext(
        task_type="DISCOVERY",
        goal=user_goal[:1200] or "为尚未指定目的地的旅行寻找候选城市",
        known_facts=["map_provider=amap", f"approved_city_count={len(cities)}"],
        missing_facts=[],
        success_criteria=["use only approved cities and interests", "base candidates on verified AMap places"],
        allowed_tools=["search_destination_places"],
        approval_boundary="READ_ONLY",
    )
    loop = await run_constrained_loop(
        runtime=runtime,
        policy=policy,
        verifier=verifier,
        checkpoint=save_checkpoint,
        task_context=task_context,
        initial_observation={"verified_search_count": 0},
        max_decisions=len(pairs) + 1,
        allowed_tool_names={"search_destination_places"},
        continue_on_tool_failure=True,
        continue_on_verifier_failure=True,
        recover_decision=recover_decision,
        persist_decision=persist_decision if save_decision_checkpoint is not None else None,
    )
    return DestinationDiscoveryResult(
        searches=searches,
        loop=loop,
        runtime=runtime,
        decision_source=decision_source,
        decision_summary=decision_summary,
    )
