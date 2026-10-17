"""Deterministic beam-width stress cases for capped replacement candidate pools."""

from datetime import date, time, timedelta
from uuid import UUID, uuid5

from travelpilot_core.engines.impact_analysis.replacements import (
    ReplanCandidate,
    search_weather_replacements,
)
from travelpilot_core.engines.impact_analysis.replan import Node

NAMESPACE = UUID("d02ee3e0-3292-431e-a614-0ee23d4fc2be")


def run_stress_evaluation() -> dict:
    records = []
    for removed_count in (6, 12, 18):
        for candidate_count in (8, 16, 32):

            def poi_id(key: str, count: int = removed_count) -> UUID:
                return uuid5(NAMESPACE, f"{count}:{key}")

            removed = []
            for index in range(removed_count):
                day_index, slot = divmod(index, 6)
                start_minute = 9 * 60 + slot * 100
                removed.append(
                    Node(
                        poi_id(f"removed-{index}"),
                        f"removed-{index}",
                        "ATTRACTION",
                        date(2026, 10, 1) + timedelta(days=day_index),
                        time(start_minute // 60, start_minute % 60),
                        time((start_minute + 45) // 60, (start_minute + 45) % 60),
                        3,
                    )
                )
            candidates = [
                ReplanCandidate(
                    poi_id(f"candidate-{index}"),
                    f"candidate-{index}",
                    "MUSEUM",
                    30.0,
                    104.0,
                    45,
                    3,
                    0.7,
                    {"open": "09:00", "close": "20:00"},
                    {"exposure": "INDOOR", "sensitivity": {"RAIN": "LOW"}},
                )
                for index in range(candidate_count)
            ]
            all_ids = [node.poi_id for node in removed] + [
                candidate.poi_id for candidate in candidates
            ]
            routes = {
                (origin, destination): 5
                for origin in all_ids
                for destination in all_ids
                if origin != destination
            }
            result = search_weather_replacements(
                removed,
                [],
                candidates,
                routes,
                condition="RAIN",
                beam_width=128,
            )
            records.append(
                {
                    "removed_nodes": removed_count,
                    "candidate_pool": len(candidates),
                    "beam_width": result.beam_width,
                    "filled_count": sum(
                        replacement is not None for _, replacement, _ in result.assignments
                    ),
                    "states_pruned": result.states_pruned,
                    "peak_frontier_size": result.peak_frontier_size,
                    "objective_score": result.objective.get("weighted_score"),
                }
            )
    return {
        "evaluation_id": "joint_replacement_beam_stress_v4",
        "provider_mode": "SYNTHETIC_DETERMINISTIC",
        "scenario_count": len(records),
        "candidate_pools": [8, 16, 32],
        "api_candidate_cap": 8,
        "scope": "core search-state growth at fixed beam width; pools above the API cap are stress-only and do not establish quality or global optimality",
        "results": records,
    }
