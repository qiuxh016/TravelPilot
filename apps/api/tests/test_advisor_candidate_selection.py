import pytest
from travelpilot_api.advisor.candidate_selection import (
    explicit_candidate_selection_ids,
    selected_ids_after_food_search,
)


def test_resolves_short_unique_candidate_name_from_free_text():
    candidates = [
        {"id": "food-1", "name": "鑫丰特色餐饮", "planning_category": "FOOD"},
        {"id": "food-2", "name": "客家小馆", "planning_category": "FOOD"},
    ]

    assert explicit_candidate_selection_ids("我选鑫丰", candidates) == ["food-1"]


def test_does_not_guess_when_candidate_alias_is_ambiguous():
    candidates = [
        {"id": "food-1", "name": "鑫丰特色餐饮（梅江店）", "planning_category": "FOOD"},
        {"id": "food-2", "name": "鑫丰客家菜馆（江南店）", "planning_category": "FOOD"},
    ]

    assert explicit_candidate_selection_ids("我选鑫丰", candidates) == []


def test_food_search_keeps_current_plan_meal_and_lodging_choices():
    candidates = [
        {"id": "tour-1", "in_current_plan": True, "planning_category": "ATTRACTION"},
        {"id": "food-1", "in_current_plan": False, "planning_category": "FOOD"},
        {"id": "hotel-1", "in_current_plan": False, "planning_category": "LODGING"},
        {"id": "old-food", "in_current_plan": False, "planning_category": "FOOD"},
    ]

    assert selected_ids_after_food_search(
        candidates, {"food-1", "hotel-1"}, {"hotel-1", "old-food"},
    ) == ["food-1", "hotel-1", "old-food", "tour-1"]


def test_food_search_preserves_prior_selected_food_lodging_and_sightseeing():
    candidates = [
        {"id": "tour-1", "in_current_plan": True, "planning_category": "ATTRACTION"},
        {"id": "food-1", "planning_category": "FOOD"},
        {"id": "hotel-1", "planning_category": "LODGING"},
    ]

    assert selected_ids_after_food_search(
        candidates, previously_selected_ids={"hotel-1", "food-1"},
    ) == ["food-1", "hotel-1", "tour-1"]


@pytest.mark.parametrize(
    ("previously_selected", "explicitly_selected", "expected"),
    [
        # Searching/choosing a lunch must not clear a selected hotel or saved stop.
        ({"hotel-1"}, {"lunch-1"}, ["hotel-1", "lunch-1", "tour-1"]),
        # A later dinner search must not clear the still-selected lunch.
        ({"hotel-1", "lunch-1", "park-1"}, {"dinner-1"}, ["dinner-1", "hotel-1", "lunch-1", "park-1", "tour-1"]),
        # An explicit deselection is reflected by the caller's current selection set.
        (set(), {"dinner-1"}, ["dinner-1", "tour-1"]),
        # IDs no longer present in the refreshed candidate snapshot are discarded.
        ({"hotel-removed", "old-food"}, {"lunch-1"}, ["lunch-1", "tour-1"]),
        # Repeated selected IDs and candidate rows cannot duplicate the selected state.
        (["hotel-1", "hotel-1"], ["lunch-1", "lunch-1"], ["hotel-1", "lunch-1", "tour-1"]),
    ],
)
def test_meal_candidate_refresh_keeps_only_current_plan_active_lodging_and_current_choice(
    previously_selected, explicitly_selected, expected,
):
    candidates = [
        {"id": "tour-1", "in_current_plan": True, "planning_category": "ATTRACTION"},
        {"id": "park-1", "planning_category": "ATTRACTION"},
        {"id": "lunch-1", "planning_category": "FOOD", "meal_type": "LUNCH"},
        {"id": "lunch-1", "planning_category": "FOOD", "meal_type": "LUNCH"},
        {"id": "dinner-1", "planning_category": "FOOD", "meal_type": "DINNER"},
        {"id": "hotel-1", "planning_category": "LODGING"},
    ]

    assert selected_ids_after_food_search(
        candidates,
        explicit_selection_ids=explicitly_selected,
        previously_selected_ids=previously_selected,
    ) == expected
