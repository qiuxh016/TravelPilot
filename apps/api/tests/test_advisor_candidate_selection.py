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
    ) == ["food-1", "hotel-1", "tour-1"]


def test_food_search_preserves_prior_lodging_but_drops_old_temporary_food_pick():
    candidates = [
        {"id": "tour-1", "in_current_plan": True, "planning_category": "ATTRACTION"},
        {"id": "food-1", "planning_category": "FOOD"},
        {"id": "hotel-1", "planning_category": "LODGING"},
    ]

    assert selected_ids_after_food_search(
        candidates, previously_selected_ids={"hotel-1", "food-1"},
    ) == ["hotel-1", "tour-1"]
