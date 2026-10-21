from types import SimpleNamespace

import pytest
from travelpilot_api.advisor.route_query import _verified_route, run_advisor_route_query
from travelpilot_api.advisor.task_taxonomy import is_itinerary_route_validation_request
from travelpilot_core.agents.loop import AgentAction
from travelpilot_core.ports.maps import Route

PLACES = [
    {"id": "place-a", "name": "宽窄巷子", "latitude": 30.6633, "longitude": 104.0647},
    {"id": "place-b", "name": "成都大熊猫繁育研究基地", "latitude": 30.7337, "longitude": 104.1477},
]


@pytest.mark.parametrize("goal", [
    "校验路线",
    "请按步行核验路线",
    "请核验当前行程中已保存地点之间的路线",
    "检查整段行程所有地点的交通时间",
])
def test_itinerary_route_validation_intent_is_distinct_from_point_to_point(goal):
    assert is_itinerary_route_validation_request(goal)


def test_point_to_point_route_query_does_not_become_itinerary_audit():
    assert not is_itinerary_route_validation_request("核验宽窄巷子到武侯祠的路线")


class Maps:
    def __init__(self, route=None):
        self.calls = []
        self.route = route

    async def get_route(self, origin, destination, mode="DRIVING", **_kwargs):
        self.calls.append((origin, destination, mode))
        return self.route or Route(origin, destination, 18200, 46, "fake", ((30.6633, 104.0647), (30.7337, 104.1477)), mode)


@pytest.mark.asyncio
async def test_route_lookup_uses_only_two_explicit_known_places_without_model():
    maps = Maps()

    result = await run_advisor_route_query(
        maps=maps, places=PLACES,
        user_goal="从宽窄巷子到成都大熊猫繁育研究基地怎么走",
        token_budget=47,
    )

    assert maps.calls == [((30.6633, 104.0647), (30.7337, 104.1477), "DRIVING")]
    assert result.loop.status == "SUCCEEDED"
    assert result.runtime.token_budget == 47
    assert result.route["mode"] == "DRIVING"
    assert result.route["status"] == "SIMULATED"
    assert result.route["duration_min"] == 46


@pytest.mark.asyncio
async def test_compound_itinerary_request_resolves_route_pair_from_explicit_connector():
    maps = Maps()
    places = [
        {**PLACES[0], "name": "人民公园"},
        {**PLACES[1], "name": "武侯祠", "latitude": 30.646, "longitude": 104.048},
        {"id": "place-c", "name": "锦里", "latitude": 30.642, "longitude": 104.055},
    ]

    result = await run_advisor_route_query(
        maps=maps,
        places=places,
        user_goal=(
            "请把我收藏的人民公园、武侯祠和锦里安排成两日游，"
            "查询人民公园到武侯祠的驾车路线，并结合天气提醒我带什么。"
        ),
    )

    assert result.loop.status == "SUCCEEDED"
    assert result.route["origin_name"] == "人民公园"
    assert result.route["destination_name"] == "武侯祠"
    assert maps.calls == [((30.6633, 104.0647), (30.646, 104.048), "DRIVING")]


@pytest.mark.asyncio
async def test_verified_route_snapshot_resumes_without_provider_call():
    class FakeMaps(Maps):
        pass

    maps = FakeMaps()
    cached = {
        "status": "SIMULATED", "origin_place_id": "place-a",
        "destination_place_id": "place-b",
        "points": [[30.6633, 104.0647], [30.7337, 104.1477]],
        "distance_m": 18200, "duration_min": 46, "mode": "DRIVING",
        "source": "Local simulated maps", "retrieved_at": "2026-10-18T00:00:00+00:00",
        "polyline": [[30.6633, 104.0647], [30.70, 104.10], [30.7337, 104.1477]],
        "segments": [{"mode": "DRIVING", "label": "模拟驾车路线"}],
    }
    result = await run_advisor_route_query(
        maps=maps, places=PLACES,
        user_goal="从宽窄巷子到成都大熊猫繁育研究基地怎么走",
        cached_route=cached,
    )
    assert maps.calls == []
    assert result.loop.status == "SUCCEEDED"
    assert result.route["origin_name"] == "宽窄巷子"
    assert result.route["destination_name"] == "成都大熊猫繁育研究基地"
    assert result.route["retrieved_at"] == cached["retrieved_at"]


@pytest.mark.asyncio
async def test_explicit_walking_request_uses_walking_route_and_labels_result():
    maps = Maps()

    result = await run_advisor_route_query(
        maps=maps, places=PLACES,
        user_goal="从宽窄巷子步行到成都大熊猫繁育研究基地怎么走",
    )

    assert maps.calls == [((30.6633, 104.0647), (30.7337, 104.1477), "WALKING")]
    assert result.route["mode"] == "WALKING"


@pytest.mark.asyncio
async def test_explicit_transit_request_uses_city_scoped_simulated_route():
    maps = Maps()

    result = await run_advisor_route_query(
        maps=maps, places=[{**PLACES[0], "city_code": "028"}, {**PLACES[1], "city_code": "028"}],
        user_goal="从宽窄巷子坐地铁到成都大熊猫繁育研究基地怎么走",
    )

    assert maps.calls == [((30.6633, 104.0647), (30.7337, 104.1477), "TRANSIT")]
    assert result.route["mode"] == "TRANSIT"


@pytest.mark.asyncio
async def test_transit_request_asks_to_refresh_places_when_city_codes_are_missing():
    maps = Maps()

    result = await run_advisor_route_query(
        maps=maps, places=PLACES,
        user_goal="从宽窄巷子坐地铁到成都大熊猫繁育研究基地怎么走",
    )

    assert maps.calls == []
    assert result.loop.status == "NEEDS_USER_INPUT"
    assert "城市编码" in result.loop.pending_question


@pytest.mark.asyncio
async def test_route_lookup_asks_when_endpoints_are_ambiguous():
    maps = Maps()

    result = await run_advisor_route_query(
        maps=maps, places=PLACES, user_goal="帮我查一下旅行中的驾车路线",
    )

    assert maps.calls == []
    assert result.loop.status == "NEEDS_USER_INPUT"
    assert "出发点和目的地" in result.loop.pending_question


@pytest.mark.asyncio
async def test_model_can_select_endpoints_but_cannot_inject_coordinates_or_unknown_places(monkeypatch):
    maps = Maps()

    async def decide(_observation, _schemas, **_kwargs):
        return AgentAction(
            kind="CALL_TOOL", tool="get_trip_route",
            arguments={"origin_place_id": "place-b", "destination_place_id": "place-a",
                       "origin": [0, 0], "mode": "DRIVING"},
        ), SimpleNamespace(prompt_tokens=5, completion_tokens=4, total_tokens=9)

    monkeypatch.setattr("travelpilot_api.advisor.route_query.decide_next_action", decide)
    result = await run_advisor_route_query(
        maps=maps, places=PLACES, user_goal="查一下去熊猫基地的路线",
        api_key="test", model="test", token_budget=128,
    )

    assert maps.calls == [((30.7337, 104.1477), (30.6633, 104.0647), "DRIVING")]
    assert result.route["origin_name"] == "成都大熊猫繁育研究基地"


@pytest.mark.asyncio
async def test_route_loop_never_sends_unknown_place_id_to_provider(monkeypatch):
    maps = Maps()

    async def decide(_observation, _schemas, **_kwargs):
        return AgentAction(
            kind="CALL_TOOL", tool="get_trip_route",
            arguments={"origin_place_id": "attacker", "destination_place_id": "place-a", "mode": "DRIVING"},
        ), SimpleNamespace(prompt_tokens=2, completion_tokens=2, total_tokens=4)

    monkeypatch.setattr("travelpilot_api.advisor.route_query.decide_next_action", decide)
    result = await run_advisor_route_query(
        maps=maps, places=PLACES, user_goal="帮我规划路线",
        api_key="test", model="test", token_budget=128,
    )

    assert maps.calls == []
    assert result.route is None
    assert result.loop.pending_question is not None


@pytest.mark.asyncio
async def test_model_cannot_change_user_requested_route_mode(monkeypatch):
    maps = Maps()

    async def decide(_observation, _schemas, **_kwargs):
        return AgentAction(
            kind="CALL_TOOL", tool="get_trip_route",
            arguments={"origin_place_id": "place-a", "destination_place_id": "place-b", "mode": "WALKING"},
        ), SimpleNamespace(prompt_tokens=2, completion_tokens=2, total_tokens=4)

    monkeypatch.setattr("travelpilot_api.advisor.route_query.decide_next_action", decide)
    result = await run_advisor_route_query(
        maps=maps, places=PLACES, user_goal="帮我查驾车路线",
        api_key="test", model="test", token_budget=128,
    )

    assert maps.calls == []
    assert result.route is None
    assert result.loop.pending_question is not None


@pytest.mark.asyncio
async def test_route_verifier_rejects_malformed_provider_result_without_retry():
    maps = Maps(route=Route((30.66, 104.06), (30.73, 104.14), -1, 99999, "fake"))

    result = await run_advisor_route_query(
        maps=maps, places=PLACES,
        user_goal="从宽窄巷子到成都大熊猫繁育研究基地怎么走",
    )

    assert len(maps.calls) == 1
    assert result.route is None
    assert result.loop.status == "SUCCEEDED"
    assert result.loop.transitions[-1]["status"] == "REJECTED"


@pytest.mark.asyncio
async def test_route_loop_resumes_checkpointed_endpoint_choice_after_interruption(monkeypatch):
    maps = Maps()
    saved = {}
    model_calls = 0
    interrupt_once = True

    async def decide(_observation, _schemas, **_kwargs):
        nonlocal model_calls
        model_calls += 1
        return AgentAction(
            kind="CALL_TOOL", tool="get_trip_route",
            arguments={"origin_place_id": "place-a", "destination_place_id": "place-b",
                       "mode": "DRIVING"},
        ), SimpleNamespace(prompt_tokens=2, completion_tokens=2, total_tokens=4)

    async def save(**decision):
        saved.update(decision)

    async def load(*, observation_ref, decision_number, approved_actions):
        if (
            observation_ref == saved.get("observation_ref")
            and decision_number == saved.get("decision_number")
            and approved_actions.get(saved.get("action_ref")) == saved.get("tool")
        ):
            return {"tool": saved["tool"], "action_ref": saved["action_ref"]}
        return None

    async def checkpoint(state):
        nonlocal interrupt_once
        if interrupt_once and state["state"] == "ACTING":
            interrupt_once = False
            raise RuntimeError("simulated process interruption")

    monkeypatch.setattr("travelpilot_api.advisor.route_query.decide_next_action", decide)
    with pytest.raises(RuntimeError, match="simulated process interruption"):
        await run_advisor_route_query(
            maps=maps, places=PLACES, user_goal="查一下这段旅行的路线",
            api_key="configured", model="configured",
            load_decision_checkpoint=load, save_decision_checkpoint=save,
            checkpoint=checkpoint,
        )

    result = await run_advisor_route_query(
        maps=maps, places=PLACES, user_goal="查一下这段旅行的路线",
        api_key="configured", model="configured",
        load_decision_checkpoint=load, save_decision_checkpoint=save,
        checkpoint=checkpoint,
    )

    assert result.loop.status == "SUCCEEDED"
    assert result.decision_source == "RECOVERED"
    assert model_calls == 1
    assert maps.calls == [((30.6633, 104.0647), (30.7337, 104.1477), "DRIVING")]


@pytest.mark.asyncio
async def test_route_verifier_rejects_out_of_range_polyline_coordinates():
    maps = Maps(route=Route(
        (30.66, 104.06), (30.73, 104.14), 18200, 46, "fake",
        ((30.66, 104.06), (130.73, 204.14)),
    ))

    result = await run_advisor_route_query(
        maps=maps, places=PLACES,
        user_goal="从宽窄巷子到成都大熊猫繁育研究基地怎么走",
    )

    assert len(maps.calls) == 1
    assert result.route is None
    assert result.loop.transitions[-1]["status"] == "REJECTED"


def test_route_verifier_rejects_inconsistent_provenance_and_endpoint_evidence():
    approved = {place["id"]: place for place in PLACES}
    route = {
        "status": "SIMULATED", "origin_place_id": "place-a",
        "destination_place_id": "place-b", "points": [[30.6633, 104.0647], [30.7337, 104.1477]],
        "distance_m": 18_200, "duration_min": 46, "mode": "DRIVING",
        "source": "Local simulated maps", "retrieved_at": "2026-10-18T00:00:00+00:00",
        "polyline": [[30.6633, 104.0647], [30.7337, 104.1477]],
    }
    assert _verified_route(route, approved)
    assert not _verified_route({**route, "source": "AMap Web Service"}, approved)
    assert not _verified_route({**route, "retrieved_at": "2026-10-18T00:00:00"}, approved)
    assert not _verified_route({**route, "points": [[31.0, 105.0], [30.7337, 104.1477]]}, approved)
    assert not _verified_route({**route, "polyline": []}, approved)
