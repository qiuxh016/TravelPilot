import pytest
from travelpilot_api.advisor.task_taxonomy import (
    accepts_driving_route_confirmation,
    infer_task_type,
    is_preparation_request,
    is_route_query_request,
    requests_unsupported_route_mode,
    unsupported_route_mode_question,
)


def test_preparation_task_is_recognized_for_weather_grounded_packing_advice():
    assert is_preparation_request("旅行要带什么")
    assert is_preparation_request("衣服怎么准备")
    assert not is_preparation_request("推荐几个博物馆")


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"goal": "看看我现有地点时间怎么安排", "schedule_requested": True,
          "planning_category": None, "weather_requested": False, "discovery_requested": False}, "SCHEDULE"),
        ({"goal": "推荐午餐", "schedule_requested": False,
          "planning_category": "FOOD", "weather_requested": False, "discovery_requested": True}, "MEAL"),
        ({"goal": "午餐时间怎么安排", "schedule_requested": True,
          "planning_category": "FOOD", "weather_requested": False,
          "discovery_requested": True}, "MEAL"),
        ({"goal": "住宿时间怎么安排", "schedule_requested": True,
          "planning_category": "LODGING", "weather_requested": False,
          "discovery_requested": True}, "LODGING"),
        ({"goal": "推荐住宿", "schedule_requested": False,
          "planning_category": "LODGING", "weather_requested": False, "discovery_requested": True}, "LODGING"),
        ({"goal": "附近有什么好玩的", "schedule_requested": False,
          "planning_category": None, "weather_requested": False, "discovery_requested": True}, "DISCOVERY"),
        ({"goal": "搜索附近的川菜馆", "schedule_requested": False,
          "planning_category": None, "weather_requested": False, "discovery_requested": False,
          "search_requested": True}, "SEARCH"),
        ({"goal": "附近地铁怎么去", "schedule_requested": False,
          "planning_category": None, "weather_requested": False, "discovery_requested": False}, "TRANSPORT"),
        ({"goal": "明天可能下雨吗", "schedule_requested": False,
          "planning_category": None, "weather_requested": True, "discovery_requested": False}, "WEATHER"),
        ({"goal": "下雨天推荐室内景点", "schedule_requested": False,
          "planning_category": None, "weather_requested": True, "discovery_requested": True,
          "search_requested": True}, "DISCOVERY"),
        ({"goal": "下雨要带什么", "schedule_requested": False,
          "planning_category": None, "weather_requested": True, "discovery_requested": False}, "PREPARATION"),
        ({"goal": "下雨出门带什么", "schedule_requested": False,
          "planning_category": None, "weather_requested": False, "discovery_requested": False}, "PREPARATION"),
        ({"goal": "帮我写旅行记录", "schedule_requested": False,
          "planning_category": None, "weather_requested": False, "discovery_requested": False}, "MEMORY"),
        ({"goal": "聊聊旅行想法", "schedule_requested": False,
          "planning_category": None, "weather_requested": False, "discovery_requested": False}, "GENERAL"),
    ],
)
def test_infer_task_type_uses_explicit_task_outcome(kwargs, expected):
    assert infer_task_type(**kwargs) == expected


def test_transport_route_detection_distinguishes_supported_modes_from_transit():
    assert is_route_query_request("从人民公园到武侯祠怎么走")
    assert is_route_query_request("从人民公园步行到武侯祠")
    assert is_route_query_request("怎么坐地铁去春熙路")
    assert not requests_unsupported_route_mode("怎么坐地铁去春熙路")
    assert requests_unsupported_route_mode("成都到西安坐高铁")
    assert not requests_unsupported_route_mode("从人民公园步行到武侯祠")
    assert not requests_unsupported_route_mode("从人民公园开车到武侯祠")


def test_driving_route_confirmation_requires_affirmative_answer_to_matching_prompt():
    question = "当前只支持驾车路线。你要我先查询驾车路线吗？"

    assert accepts_driving_route_confirmation(question, "可以")
    assert accepts_driving_route_confirmation(question, "好的，查一下")
    assert not accepts_driving_route_confirmation(question, "不用了")
    assert not accepts_driving_route_confirmation("请说出起点和终点", "可以")


def test_unsupported_intercity_route_does_not_silently_propose_driving():
    question = unsupported_route_mode_question()

    assert "班次、时刻或票价" in question
    assert "起点、终点和出行方式" in question
    assert "不会自动改成驾车路线" in question
