import pytest
from travelpilot_api.advisor.task_contract import build_agent_task_context, classify_search_task


@pytest.mark.parametrize(
    ("category", "preparation", "weather", "discovery", "expected"),
    [
        ("FOOD", True, True, True, "MEAL"),
        ("LODGING", False, True, True, "LODGING"),
        (None, True, True, True, "DISCOVERY"),
        (None, False, True, True, "DISCOVERY"),
        (None, False, False, True, "DISCOVERY"),
        (None, False, False, False, "SEARCH"),
    ],
)
def test_search_dispatch_assigns_the_most_specific_task_policy(category, preparation, weather, discovery, expected):
    assert classify_search_task(
        planning_category=category,
        preparation_requested=preparation,
        weather_requested=weather,
        discovery_requested=discovery,
    ) == expected


@pytest.mark.parametrize(
    ("task_type", "available", "expected", "boundary"),
    [
        ("MEAL", ["search_places", "get_trip_weather"], ["search_places"], "READ_ONLY"),
        ("DISCOVERY", ["search_places", "get_trip_weather"],
         ["get_trip_weather", "search_places"], "READ_ONLY"),
        ("LODGING", ["search_places", "book_hotel"], ["search_places"], "READ_ONLY"),
        ("TRANSPORT", ["get_trip_route", "get_distance_matrix"], ["get_trip_route"], "READ_ONLY"),
        ("MEMORY", ["search_places", "save_memory"], [], "READ_ONLY"),
        ("SCHEDULE", ["get_distance_matrix", "build_schedule_draft", "save_plan"],
         ["build_schedule_draft", "get_distance_matrix"], "DRAFT_ONLY"),
    ],
)
def test_task_contract_intersects_available_tools_with_canonical_scope(
    task_type, available, expected, boundary,
):
    task = build_agent_task_context(
        task_type=task_type,
        goal="安排旅行",
        known_facts=["destination"],
        missing_facts=["dates"],
        available_tools=available,
    )

    assert task.allowed_tools == expected
    assert task.approval_boundary == boundary
    assert task.success_criteria
    if task_type == "MEMORY":
        assert "ask before persistent memory changes" in task.success_criteria


def test_meal_task_can_use_forecast_only_when_the_user_also_requests_weather():
    task = build_agent_task_context(
        task_type="MEAL",
        goal="帮我找午餐，并看看当天会不会下雨",
        known_facts=["destination", "dates"],
        missing_facts=[],
        available_tools=["search_places", "get_trip_weather"],
        weather_requested=True,
    )

    assert task.task_type == "MEAL"
    assert task.allowed_tools == ["get_trip_weather", "search_places"]
    assert any("weather evidence" in item for item in task.success_criteria)


def test_task_contract_bounds_and_deduplicates_shared_context_fields():
    task = build_agent_task_context(
        task_type="DISCOVERY",
        goal="X" * 1300,
        known_facts=["destination", "destination"],
        missing_facts=["dates", "dates"],
        available_tools=["search_places"],
    )

    assert len(task.goal) == 1200
    assert task.known_facts == ["destination"]
    assert task.missing_facts == ["dates"]
    assert task.allowed_tools == ["search_places"]
