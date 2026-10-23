"""Check preference and route-cost tradeoffs against explicit synthetic labels."""

from datetime import date, time
from uuid import UUID, uuid5

from travelpilot_core.engines.impact_analysis.replacements import (
    ReplanCandidate,
    search_weather_replacements,
)
from travelpilot_core.engines.impact_analysis.replan import Node

NAMESPACE = UUID("bec4cf70-4a54-41bd-85a8-8d64e9210f51")


def run_evaluation() -> dict:
    removed_id = uuid5(NAMESPACE, "removed")
    following_id = uuid5(NAMESPACE, "following")
    low_interest_id = uuid5(NAMESPACE, "fast-low-interest")
    preferred_id = uuid5(NAMESPACE, "preferred-longer-route")
    removed = Node(
        removed_id,
        "removed outdoor visit",
        "ATTRACTION",
        date(2026, 10, 1),
        time(10),
        time(11),
        3,
        environment_profile={"exposure": "OUTDOOR", "sensitivity": {"RAIN": "HIGH"},
                             "source": "USER", "confidence": 0.8},
    )
    following = Node(
        following_id,
        "fixed evening visit",
        "ATTRACTION",
        date(2026, 10, 1),
        time(17),
        time(18),
        3,
    )
    candidates = [
        ReplanCandidate(
            low_interest_id,
            "fast low-interest option",
            "MUSEUM",
            30,
            104,
            45,
            2,
            0.1,
            {"open": "09:00", "close": "20:00"},
            {"exposure": "INDOOR", "sensitivity": {"RAIN": "LOW"},
             "source": "EDITORIAL", "confidence": 0.9},
        ),
        ReplanCandidate(
            preferred_id,
            "user-preferred option",
            "MUSEUM",
            30,
            104,
            45,
            5,
            0.9,
            {"open": "09:00", "close": "20:00"},
            {"exposure": "INDOOR", "sensitivity": {"RAIN": "LOW"},
             "source": "EDITORIAL", "confidence": 0.9},
        ),
    ]
    routes = {
        (low_interest_id, following_id): 1,
        (preferred_id, following_id): 20,
    }
    cases = (
        ("route_only", {"preference_penalty": 0.0}, low_interest_id),
        ("default", {}, preferred_id),
        ("preference_weight_2", {"preference_penalty": 2.0}, preferred_id),
    )
    results = []
    for case_id, weight_overrides, expected_id in cases:
        search = search_weather_replacements(
            [removed],
            [following],
            candidates,
            routes,
            condition="RAIN",
            objective_weights=weight_overrides,
        )
        chosen = search.assignments[0][1]
        results.append(
            {
                "case_id": case_id,
                "chosen_id": str(chosen.poi_id) if chosen else None,
                "chosen_name": chosen.name if chosen else None,
                "expected_id": str(expected_id),
                "expected_match": chosen is not None and chosen.poi_id == expected_id,
                "weights": search.objective_weights,
                "objective": search.objective,
            }
        )
    return {
        "evaluation_id": "preference_weight_sensitivity_v1",
        "provider_mode": "SYNTHETIC_DETERMINISTIC",
        "scenario_count": len(results),
        "all_expectations_match": all(item["expected_match"] for item in results),
        "label_source": "hand-authored synthetic preference ordering; not user study data",
        "scope": "sensitivity check for route-versus-explicit-preference tradeoff; does not empirically calibrate weights",
        "results": results,
    }
