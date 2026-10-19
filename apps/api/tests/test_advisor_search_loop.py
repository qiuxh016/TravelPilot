import json
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import BaseModel
from travelpilot_api.advisor.search_loop import _verified_search_result, run_advisor_search_loop
from travelpilot_api.advisor.tools import advisor_tool_runtime
from travelpilot_core.agents.loop import AgentAction
from travelpilot_core.agents.runtime import AgentRuntime
from travelpilot_core.ports.tools import ToolSpec


def test_frozen_open_consultation_cases_cover_core_advisor_modes():
    case_file = Path(__file__).resolve().parents[3] / "tests/evaluation/cases/advisor_open_consultation_v1.json"
    dataset = json.loads(case_file.read_text(encoding="utf-8"))
    assert dataset["frozen_before_scoring"] is True
    assert len(dataset["cases"]) == 10
    assert len({case["id"] for case in dataset["cases"]}) == 10
    assert {case["expected_next_action"] for case in dataset["cases"]} >= {
        "ASK_USER", "SEARCH_PLACES", "SEARCH_FOOD", "SEARCH_LODGING",
        "GET_WEATHER", "READ_TRIP_AND_ROUTE",
    }


class SearchInput(BaseModel):
    keyword: str
    city: str | None = None


class EmptyInput(BaseModel):
    pass


async def _trace(_event):
    return None


async def _record_checkpoint(target, state):
    target.append(state)


def _runtime(searched):
    async def search(arguments):
        searched.append((arguments.keyword, arguments.city))
        return {
            "status": "VERIFIED",
            "places": [{
                "provider": "amap", "provider_id": arguments.keyword,
                "name": f"{arguments.keyword} result", "latitude": 30.6,
                "longitude": 104.0,
            }],
        }

    return AgentRuntime([ToolSpec("search_places", SearchInput, search)], _trace)


def test_search_result_verifier_rejects_invalid_provider_or_coordinate_evidence():
    place = {
        "provider": "amap", "provider_id": "museum-1", "name": "博物馆",
        "latitude": 30.6, "longitude": 104.0,
    }
    assert _verified_search_result({"status": "VERIFIED", "places": [place]})
    assert _verified_search_result({
        "status": "SIMULATED", "places": [{**place, "provider": "fake"}],
    })
    assert _verified_search_result({"status": "NO_RESULTS", "places": []})

    invalid_outputs = [
        {"status": "VERIFIED", "places": [{**place, "latitude": True}]},
        {"status": "VERIFIED", "places": [{**place, "longitude": False}]},
        {"status": "VERIFIED", "places": [{**place, "provider": "fake"}]},
        {"status": "SIMULATED", "places": [place]},
        {"status": "VERIFIED", "places": [{**place, "latitude": float("inf")}]},
        {"status": "VERIFIED", "places": [{**place, "provider_id": ""}]},
        {"status": "NO_RESULTS", "places": [place]},
        {"status": "VERIFIED", "places": []},
    ]
    assert all(not _verified_search_result(output) for output in invalid_outputs)


@pytest.mark.asyncio
async def test_search_loop_uses_observations_to_select_next_approved_query(monkeypatch):
    searched = []
    checkpoints = []
    decisions = iter([
        AgentAction(kind="CALL_TOOL", tool="search_places", arguments={"keyword": "景点"}, summary="先查景点"),
        AgentAction(kind="CALL_TOOL", tool="search_places", arguments={"keyword": "美食"}, summary="再查午餐"),
        AgentAction(kind="FINISH", summary="候选已齐"),
    ])

    async def decide(observation, _schemas, **_kwargs):
        assert observation["agent_task"]["task_type"] == "DISCOVERY"
        assert observation["agent_task"]["allowed_tools"] == ["search_places"]
        if len(searched) == 1:
            assert observation["search_places"]["places"][0]["name"] == "景点 result"
        return next(decisions), SimpleNamespace(prompt_tokens=4, completion_tokens=3, total_tokens=7)

    monkeypatch.setattr("travelpilot_api.advisor.search_loop.decide_next_action", decide)
    runtime = _runtime(searched)
    result = await run_advisor_search_loop(
        runtime=runtime,
        requests=[{"keyword": "景点"}, {"keyword": "美食"}],
        city="成都", user_goal="轻松规划并找午餐", api_key="test", model="test",
        checkpoint=lambda state: _record_checkpoint(checkpoints, state),
    )

    assert searched == [("景点", "成都"), ("美食", "成都")]
    assert result.loop.status == "SUCCEEDED"
    assert result.decision_source == "MODEL"
    assert [item["state"] for item in checkpoints] == [
        "OBSERVING", "ACTING", "VERIFIED", "ACTING", "VERIFIED", "SUCCEEDED",
    ]


@pytest.mark.asyncio
async def test_search_loop_cannot_finish_before_all_explicit_domains_are_queried(monkeypatch):
    searched = []
    observed_queries = []

    async def decide(observation, _schemas, **_kwargs):
        observed_queries.append(list(observation["approved_queries"]))
        return AgentAction(kind="FINISH", summary="已经有一些候选"), SimpleNamespace(
            prompt_tokens=2, completion_tokens=1, total_tokens=3,
        )

    monkeypatch.setattr("travelpilot_api.advisor.search_loop.decide_next_action", decide)
    result = await run_advisor_search_loop(
        runtime=_runtime(searched),
        requests=[
            {"keyword": "景点", "required": True},
            {"keyword": "餐饮服务", "required": True},
        ],
        city="成都", user_goal="找景点并推荐午餐", task_type="DISCOVERY",
        api_key="test", model="test",
    )

    assert searched == [("景点", "成都"), ("餐饮服务", "成都")]
    assert observed_queries == [["景点", "餐饮服务"], ["餐饮服务"]]
    assert result.loop.status == "SUCCEEDED"
    assert len(result.results) == 2
    assert result.decision_source == "RULE_RECOVERY"


@pytest.mark.asyncio
async def test_recovered_map_search_is_reused_as_verified_loop_observation():
    searched = []
    recovered = {
        "status": "VERIFIED", "retrieved_at": datetime.now().astimezone().isoformat(),
        "places": [{"provider": "amap", "provider_id": "museum-1",
                    "name": "博物馆", "latitude": 30.6, "longitude": 104.0}],
    }
    result = await run_advisor_search_loop(
        runtime=_runtime(searched), requests=[{"keyword": "博物馆"}], city="成都",
        user_goal="查博物馆", cached_searches={"博物馆": recovered},
    )
    assert searched == []
    assert result.results == [({"keyword": "博物馆"}, recovered)]


@pytest.mark.asyncio
async def test_search_loop_resumes_same_approved_action_after_interruption():
    calls = []
    saved = {}
    interrupt_once = True

    async def search(arguments):
        calls.append(arguments.keyword)
        return {
            "status": "VERIFIED",
            "places": [{"provider": "amap", "provider_id": "museum-1",
                        "name": "museum", "latitude": 30.6, "longitude": 104.0}],
        }

    runtime = AgentRuntime([ToolSpec("search_places", SearchInput, search)], _trace)

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

    with pytest.raises(RuntimeError, match="simulated process interruption"):
        await run_advisor_search_loop(
            runtime=runtime, requests=[{"keyword": "博物馆"}], city="成都",
            user_goal="查询博物馆", load_decision_checkpoint=load,
            save_decision_checkpoint=save, checkpoint=checkpoint,
        )

    assert saved["tool"] == "search_places"
    assert saved["action_ref"] in saved["approved_actions"]
    result = await run_advisor_search_loop(
        runtime=runtime, requests=[{"keyword": "博物馆"}], city="成都",
        user_goal="查询博物馆", load_decision_checkpoint=load,
        save_decision_checkpoint=save, checkpoint=checkpoint,
    )
    assert result.loop.status == "SUCCEEDED"
    assert calls == ["博物馆"]
    assert result.results[0][1]["places"][0]["name"] == "museum"
    assert result.loop.status == "SUCCEEDED"
    assert result.decision_source == "RECOVERED"


@pytest.mark.asyncio
async def test_meal_search_passes_meal_contract_and_exposes_only_read_tools(monkeypatch):
    observed = {}
    searched = []

    async def decide(observation, _schemas, **_kwargs):
        observed.update(observation["agent_task"])
        return AgentAction(
            kind="CALL_TOOL", tool="search_places", arguments={"keyword": "restaurant"},
        ), SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2)

    monkeypatch.setattr("travelpilot_api.advisor.search_loop.decide_next_action", decide)
    await run_advisor_search_loop(
        runtime=_runtime(searched), requests=[{"keyword": "餐饮服务"}], city="成都",
        user_goal="帮我找午餐", task_type="MEAL", api_key="test", model="test",
    )

    assert observed["task_type"] == "MEAL"
    assert observed["allowed_tools"] == ["search_places"]
    assert searched == [("餐饮服务", "成都")]


@pytest.mark.asyncio
async def test_search_loop_completes_explicit_meal_lookup_before_optional_question(monkeypatch):
    searched = []
    actions = iter([AgentAction(kind="ASK_USER", question="你更偏爱哪种口味？")])

    async def decide(_observation, _schemas, **_kwargs):
        return next(actions), SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2)

    monkeypatch.setattr("travelpilot_api.advisor.search_loop.decide_next_action", decide)
    result = await run_advisor_search_loop(
        runtime=_runtime(searched), requests=[{"keyword": "餐饮服务"}], city="梅州",
        user_goal="推荐午餐", task_type="MEAL", api_key="test", model="test",
    )

    assert searched == [("餐饮服务", "梅州")]
    assert len(result.results) == 1
    assert result.loop.status == "SUCCEEDED"
    assert result.pending_question is None


@pytest.mark.asyncio
async def test_search_loop_completes_explicit_lodging_lookup_before_optional_question(monkeypatch):
    searched = []

    async def decide(_observation, _schemas, **_kwargs):
        return AgentAction(kind="ASK_USER", question="你更在意价格还是交通？"), SimpleNamespace(
            prompt_tokens=1, completion_tokens=1, total_tokens=2,
        )

    monkeypatch.setattr("travelpilot_api.advisor.search_loop.decide_next_action", decide)
    result = await run_advisor_search_loop(
        runtime=_runtime(searched), requests=[{"keyword": "住宿服务"}], city="梅州",
        user_goal="推荐住宿", task_type="LODGING", api_key="test", model="test",
    )

    assert searched == [("住宿服务", "梅州")]
    assert len(result.results) == 1
    assert result.loop.status == "SUCCEEDED"


@pytest.mark.asyncio
async def test_rainy_day_discovery_can_use_labelled_fixture_before_indoor_search(monkeypatch):
    searched = []
    call_order = []

    async def search(arguments):
        call_order.append("search_places")
        searched.append(arguments.keyword)
        return {
            "status": "VERIFIED",
            "places": [{
                "provider": "amap", "provider_id": "indoor-1", "name": "室内展馆",
                "latitude": 30.6, "longitude": 104.0,
            }],
        }

    async def weather(_arguments):
        call_order.append("get_trip_weather")
        return {
            "status": "AVAILABLE", "city": "成都", "source": "Local simulated forecast",
            "fetched_at": "2026-10-18T08:00:00+08:00",
            "days": [{"date": "2026-10-20", "weather_code": 61, "description": "小雨",
                      "temperature_max_c": 20, "temperature_min_c": 13,
                      "precipitation_probability_max": 75}],
        }

    async def decide(observation, _schemas, **_kwargs):
        assert observation["agent_task"]["task_type"] == "DISCOVERY"
        assert observation["agent_task"]["allowed_tools"] == ["get_trip_weather", "search_places"]
        if "get_trip_weather" not in observation:
            assert observation["approved_weather_lookup"] is True
            assert observation["observed_searches"] == []
            action = AgentAction(kind="CALL_TOOL", tool="get_trip_weather", arguments={})
        elif "search_places" not in observation:
            weather_observation = observation["get_trip_weather"]
            assert weather_observation["source"] == "Local simulated forecast"
            assert weather_observation["days"][0]["weather_code"] == 61
            assert observation["observed_searches"] == []
            action = AgentAction(
                kind="CALL_TOOL", tool="search_places", arguments={"keyword": "室内景点"},
            )
        else:
            assert observation["search_places"]["places"][0]["name"] == "室内展馆"
            action = AgentAction(kind="FINISH", summary="已核对雨天候选和预报")
        return action, SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2)

    monkeypatch.setattr("travelpilot_api.advisor.search_loop.decide_next_action", decide)
    runtime = AgentRuntime([
        ToolSpec("search_places", SearchInput, search),
        ToolSpec("get_trip_weather", EmptyInput, weather),
    ], _trace)
    result = await run_advisor_search_loop(
        runtime=runtime,
        requests=[{"keyword": "室内景点"}], city="成都",
        user_goal="下雨天帮我找室内替代地点，也看看天气",
        task_type="DISCOVERY", weather_requested=True,
        weather_source="Local simulated forecast", api_key="test", model="test",
    )

    assert call_order == ["get_trip_weather", "search_places"]
    assert searched == ["室内景点"]
    assert result.weather["source"] == "Local simulated forecast"
    assert result.weather["days"][0]["weather_code"] == 61
    assert result.loop.status == "SUCCEEDED"


@pytest.mark.asyncio
async def test_optional_clarification_does_not_skip_explicit_weather_lookup(monkeypatch):
    calls = []

    async def search(_arguments):
        calls.append("search_places")
        return {
            "status": "VERIFIED",
            "places": [{
                "provider": "amap", "provider_id": "indoor-1", "name": "室内展馆",
                "latitude": 30.6, "longitude": 104.0,
            }],
        }

    async def weather(_arguments):
        calls.append("get_trip_weather")
        return {
            "status": "AVAILABLE", "city": "成都", "source": "Local simulated forecast",
            "fetched_at": "2026-10-18T08:00:00+08:00",
            "days": [{"date": "2026-10-20", "weather_code": 61, "description": "小雨",
                      "temperature_max_c": 20, "temperature_min_c": 13,
                      "precipitation_probability_max": 75}],
        }

    async def decide(observation, _schemas, **_kwargs):
        if not observation.get("get_trip_weather"):
            assert observation["approved_weather_lookup"] is True
            action = AgentAction(kind="ASK_USER", question="你更想去自然景点还是历史街区？")
            return action, SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2)
        if not observation.get("search_places"):
            action = AgentAction(
                kind="CALL_TOOL", tool="search_places", arguments={"keyword": "室内景点"},
            )
            return action, SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2)
        action = AgentAction(kind="FINISH", summary="天气和候选都已核对")
        return action, SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2)

    monkeypatch.setattr("travelpilot_api.advisor.search_loop.decide_next_action", decide)
    runtime = AgentRuntime([
        ToolSpec("search_places", SearchInput, search),
        ToolSpec("get_trip_weather", EmptyInput, weather),
    ], _trace)
    result = await run_advisor_search_loop(
        runtime=runtime, requests=[{"keyword": "室内景点", "required": True}],
        city="成都", user_goal="下雨天帮我找室内替代地点，也看看天气",
        task_type="DISCOVERY", weather_requested=True,
        weather_source="Local simulated forecast", api_key="test", model="test",
    )

    assert calls == ["get_trip_weather", "search_places"]
    assert result.weather["days"][0]["description"] == "小雨"
    assert result.loop.status == "SUCCEEDED"


@pytest.mark.asyncio
async def test_weather_loop_rejects_available_forecast_without_days():
    async def weather(_arguments):
        return {
            "status": "AVAILABLE", "city": "成都", "source": "Open-Meteo",
            "fetched_at": "2026-10-18T08:00:00+08:00", "days": [],
        }

    runtime = AgentRuntime([ToolSpec("get_trip_weather", EmptyInput, weather)], _trace)
    result = await run_advisor_search_loop(
        runtime=runtime, requests=[], city="成都", user_goal="查一下旅行天气",
        weather_requested=True,
    )

    assert result.weather is None
    assert result.loop.status == "SUCCEEDED"
    assert any(step["status"] == "REJECTED" for step in result.loop.transitions)


@pytest.mark.asyncio
async def test_search_loop_clamps_model_query_and_city_to_server_plan(monkeypatch):
    searched = []

    async def decide(_observation, _schemas, **_kwargs):
        return AgentAction(
            kind="CALL_TOOL", tool="search_places",
            arguments={"keyword": "外部注入关键词", "city": "其他城市"},
        ), SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2)

    monkeypatch.setattr("travelpilot_api.advisor.search_loop.decide_next_action", decide)
    result = await run_advisor_search_loop(
        runtime=_runtime(searched),
        requests=[{"keyword": "宽窄巷子"}], city="成都", user_goal="搜索宽窄巷子",
        api_key="test", model="test",
    )

    assert searched == [("宽窄巷子", "成都")]
    assert result.runtime.calls[0]["status"] == "SUCCEEDED"
    assert result.decision_source == "RULE_RECOVERY"


@pytest.mark.asyncio
async def test_search_loop_can_pause_for_user_input(monkeypatch):
    async def decide(_observation, _schemas, **_kwargs):
        return AgentAction(kind="ASK_USER", question="你更想找自然景点还是历史街区？"), SimpleNamespace(
            prompt_tokens=2, completion_tokens=5, total_tokens=7,
        )

    monkeypatch.setattr("travelpilot_api.advisor.search_loop.decide_next_action", decide)
    result = await run_advisor_search_loop(
        runtime=_runtime([]), requests=[{"keyword": "景点"}], city="成都",
        user_goal="给我推荐地点", api_key="test", model="test",
    )

    assert result.loop.status == "NEEDS_USER_INPUT"
    assert result.pending_question == "你更想找自然景点还是历史街区？"
    assert result.runtime.steps == 0


@pytest.mark.asyncio
async def test_agent_can_choose_trip_weather_and_provider_scope_is_server_owned(monkeypatch):
    provider_calls = []

    class Maps:
        async def search_place(self, *_args):
            return []

    class Weather:
        async def forecast(self, city, start_date, end_date):
            provider_calls.append((city, start_date, end_date))
            return {
                "status": "OUT_OF_RANGE", "city": city, "source": "Open-Meteo",
                "fetched_at": datetime.fromisoformat("2026-10-18T00:00:00+00:00"), "days": [],
            }

    decisions = iter([
        AgentAction(
            kind="CALL_TOOL", tool="get_trip_weather",
            arguments={"city": "untrusted", "start_date": "2000-01-01"},
            summary="查当前旅行天气",
        ),
        AgentAction(kind="FINISH", summary="天气已核验"),
    ])

    async def decide(_observation, _schemas, **_kwargs):
        return next(decisions), SimpleNamespace(prompt_tokens=3, completion_tokens=2, total_tokens=5)

    monkeypatch.setattr("travelpilot_api.advisor.search_loop.decide_next_action", decide)
    runtime = advisor_tool_runtime(
        Maps(), weather=Weather(), city="梅州", start_date=date(2026, 10, 20),
        end_date=date(2026, 10, 21),
    )
    result = await run_advisor_search_loop(
        runtime=runtime, requests=[], city="梅州", user_goal="天气如何", weather_requested=True,
        api_key="test", model="test",
    )

    assert provider_calls == [("梅州", date(2026, 10, 20), date(2026, 10, 21))]
    assert result.weather["source"] == "Open-Meteo"
    assert result.loop.status == "SUCCEEDED"


@pytest.mark.asyncio
async def test_recovered_weather_is_an_observation_and_skips_provider_call():
    provider_calls = []

    class Maps:
        async def search_place(self, *_args):
            return []

    class Weather:
        async def forecast(self, *_args):
            provider_calls.append("called")
            raise AssertionError("recovered forecast must prevent a duplicate request")

    recovered = {
        "status": "AVAILABLE", "source": "Open-Meteo",
        "fetched_at": "2026-10-18T00:00:00+00:00", "city": "成都",
        "days": [{"date": "2026-10-20", "description": "小雨", "weather_code": 61,
                  "temperature_max_c": 20, "temperature_min_c": 13,
                  "precipitation_probability_max": 75}],
    }
    runtime = advisor_tool_runtime(
        Maps(), weather=Weather(), city="成都", start_date=date(2026, 10, 20),
        end_date=date(2026, 10, 20),
    )
    result = await run_advisor_search_loop(
        runtime=runtime, requests=[], city="成都", user_goal="查天气",
        weather_requested=True, cached_weather=recovered,
    )
    assert provider_calls == []
    assert result.weather == recovered
    assert result.loop.status == "SUCCEEDED"
    assert result.decision_source == "RECOVERED"
    assert "旅行天气预报" in result.decision_summary


@pytest.mark.asyncio
async def test_recovered_weather_for_another_city_is_discarded():
    provider_calls = []

    class Maps:
        async def search_place(self, *_args):
            return []

    class Weather:
        async def forecast(self, city, *_args):
            provider_calls.append(city)
            return {
                "status": "AVAILABLE", "source": "Open-Meteo", "city": city,
                "fetched_at": datetime.fromisoformat("2026-10-18T00:00:00+00:00"),
                "days": [{"date": "2026-10-20", "description": "晴", "weather_code": 0,
                          "temperature_max_c": 21, "temperature_min_c": 12,
                          "precipitation_probability_max": 5}],
            }

    stale_city_snapshot = {
        "status": "AVAILABLE", "source": "Open-Meteo", "city": "成都",
        "fetched_at": "2026-10-18T00:00:00+00:00",
        "days": [{"date": "2026-10-20", "description": "小雨", "weather_code": 61,
                  "temperature_max_c": 20, "temperature_min_c": 13,
                  "precipitation_probability_max": 75}],
    }
    runtime = advisor_tool_runtime(
        Maps(), weather=Weather(), city="西安", start_date=date(2026, 10, 20),
        end_date=date(2026, 10, 20),
    )
    result = await run_advisor_search_loop(
        runtime=runtime, requests=[], city="西安", user_goal="查天气",
        weather_requested=True, cached_weather=stale_city_snapshot,
    )

    assert provider_calls == ["西安"]
    assert result.weather["city"] == "西安"
    assert result.decision_source == "RULE"
