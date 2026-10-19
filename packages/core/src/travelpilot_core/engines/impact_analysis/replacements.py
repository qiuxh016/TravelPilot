"""Bounded, route-aware candidate selection for local plan repair."""

from dataclasses import dataclass
from datetime import time
from math import isfinite
from uuid import UUID

from travelpilot_core.engines.impact_analysis.replan import (
    Node,
    trusted_environment_profile,
)


@dataclass(frozen=True)
class ReplanCandidate:
    poi_id: UUID
    name: str
    category: str
    latitude: float
    longitude: float
    duration_min: int
    priority: int
    interest_score: float
    opening_hours: dict | None
    environment_profile: dict | None


@dataclass(frozen=True)
class _Option:
    node: Node
    route_cost: int
    time_shift: int
    priority: int
    interest_score: float
    preference_penalty: float


@dataclass(frozen=True)
class _SearchState:
    scheduled: tuple[Node, ...]
    remaining: tuple[ReplanCandidate, ...]
    assignments: tuple[tuple[Node, Node | None, str], ...]
    route_cost: int = 0
    time_shift: int = 0
    priority: int = 0
    interest_score: float = 0.0
    preference_penalty: float = 0.0
    unfilled_visit_penalty: float = 0.0


OBJECTIVE_WEIGHTS = {
    "plan_change_cost": 1.0,
    "route_time_delta_min": 0.1,
    "time_shift_min": 0.05,
    "preference_penalty": 1.0,
    "unfilled_visit_penalty": 1.0,
}


@dataclass(frozen=True)
class WeatherReplacementSearchResult:
    assignments: tuple[tuple[Node, Node | None, str], ...]
    objective: dict[str, int | float]
    objective_weights: dict[str, float]
    beam_width: int
    states_pruned: int
    peak_frontier_size: int
    diagnostics: tuple[dict[str, object], ...]


def _minutes(value: str) -> int | None:
    try:
        parsed = time.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return parsed.hour * 60 + parsed.minute


def _weather_safe(profile: dict | None, condition: str) -> bool:
    if not trusted_environment_profile(profile):
        return False
    exposure = str(profile.get("exposure", "UNKNOWN")).upper()
    sensitivities = profile.get("sensitivity", {})
    sensitivity = (
        sensitivities.get(condition.upper(), "UNKNOWN")
        if isinstance(sensitivities, dict)
        else "UNKNOWN"
    )
    return exposure == "INDOOR" or (
        exposure in {"OUTDOOR", "MIXED"} and str(sensitivity).upper() == "LOW"
    )


def _weather_replacement_options(
    removed: Node,
    scheduled_nodes: list[Node],
    candidates: list[ReplanCandidate],
    travel_minutes: dict[tuple[UUID, UUID], int],
    *,
    condition: str,
    max_nodes_per_day: int,
    minimum_transfer_min: int,
    closing_time: time,
) -> tuple[list[_Option], str, dict[str, int]]:
    day_nodes = sorted(
        (node for node in scheduled_nodes if node.day == removed.day),
        key=lambda node: (node.start_time, node.stable_key),
    )
    if len(day_nodes) + 1 > max_nodes_per_day:
        return [], "DAILY_CAPACITY", {"DAILY_CAPACITY": 1}

    position = next(
        (index for index, node in enumerate(day_nodes) if node.start_time >= removed.start_time),
        len(day_nodes),
    )
    previous = day_nodes[position - 1] if position else None
    following = day_nodes[position] if position < len(day_nodes) else None
    feasible: list[_Option] = []
    reasons: dict[str, int] = {}

    def reject(reason: str) -> None:
        reasons[reason] = reasons.get(reason, 0) + 1

    for candidate in candidates:
        if candidate.poi_id == removed.poi_id or any(
            node.poi_id == candidate.poi_id for node in scheduled_nodes
        ):
            continue
        if not _weather_safe(candidate.environment_profile, condition):
            reject("WEATHER_PROFILE")
            continue
        if candidate.duration_min <= 0:
            reject("INVALID_DURATION")
            continue
        hours = candidate.opening_hours or {}
        open_at = _minutes(hours.get("open"))
        close_at = _minutes(hours.get("close"))
        if open_at is None or close_at is None or close_at <= open_at:
            reject("OPENING_HOURS_UNKNOWN")
            continue

        earliest = removed.start_time.hour * 60 + removed.start_time.minute
        route_before = 0
        route_after = 0
        if previous:
            travel = travel_minutes.get((previous.poi_id, candidate.poi_id))
            if travel is None or travel < 0:
                reject("ROUTE_TIME_UNKNOWN")
                continue
            route_before = travel
            earliest = max(
                earliest,
                previous.end_time.hour * 60
                + previous.end_time.minute
                + travel
                + minimum_transfer_min,
            )
        start = max(earliest, open_at)
        end = start + candidate.duration_min
        latest_end = _minutes(closing_time.isoformat())
        if following:
            travel = travel_minutes.get((candidate.poi_id, following.poi_id))
            if travel is None or travel < 0:
                reject("ROUTE_TIME_UNKNOWN")
                continue
            route_after = travel
            latest_end = min(
                latest_end,
                following.start_time.hour * 60
                + following.start_time.minute
                - travel
                - minimum_transfer_min,
            )
        if end > close_at:
            reject("CLOSED_AT_SLOT")
            continue
        if end > latest_end:
            reject("TIME_OR_ROUTE_CONFLICT")
            continue

        original_start = removed.start_time.hour * 60 + removed.start_time.minute
        replacement = Node(
            candidate.poi_id,
            candidate.name,
            candidate.category,
            removed.day,
            time(start // 60, start % 60),
            time(end // 60, end % 60),
            candidate.priority,
            "PLANNED",
            f"WEATHER_REPLACEMENT:{condition.upper()}",
            candidate.environment_profile,
        )
        route_delta = route_before + route_after
        if previous and following:
            bypass = travel_minutes.get((previous.poi_id, following.poi_id))
            if bypass is not None and bypass >= 0:
                route_delta -= bypass
        feasible.append(
            _Option(
                replacement,
                route_delta,
                start - original_start,
                candidate.priority,
                candidate.interest_score,
                max(0, 5 - candidate.priority) * 0.5 + (1.0 - candidate.interest_score) * 2.0,
            )
        )

    feasible.sort(
        key=lambda option: (
            option.route_cost,
            option.time_shift,
            -option.priority,
            -option.interest_score,
            option.node.name,
            option.node.stable_key,
        )
    )
    return feasible, min(reasons) if reasons else "NO_CANDIDATES", reasons


def select_weather_replacement(
    removed: Node,
    scheduled_nodes: list[Node],
    candidates: list[ReplanCandidate],
    travel_minutes: dict[tuple[UUID, UUID], int],
    *,
    condition: str,
    max_nodes_per_day: int = 6,
    minimum_transfer_min: int = 20,
    closing_time: time = time(20),
) -> tuple[Node | None, str]:
    """Select the lowest-cost feasible alternative for one removed visit."""
    options, reason, _ = _weather_replacement_options(
        removed,
        scheduled_nodes,
        candidates,
        travel_minutes,
        condition=condition,
        max_nodes_per_day=max_nodes_per_day,
        minimum_transfer_min=minimum_transfer_min,
        closing_time=closing_time,
    )
    return (options[0].node, "OK") if options else (None, reason)


def search_weather_replacements(
    removed_nodes: list[Node],
    scheduled_nodes: list[Node],
    candidates: list[ReplanCandidate],
    travel_minutes: dict[tuple[UUID, UUID], int],
    *,
    condition: str,
    max_nodes_per_day: int = 6,
    minimum_transfer_min: int = 20,
    closing_time: time = time(20),
    beam_width: int = 128,
    objective_weights: dict[str, float] | None = None,
) -> WeatherReplacementSearchResult:
    """Jointly assign distinct candidates using a bounded beam search.

    The weighted objective combines PlanDiff cost, route-time increase, time
    shift, preference penalty, and a separate penalty for leaving a visit
    unfilled. An unfilled visit receives a penalty of ``5 + removed.priority``
    so a long detour can rationally be left unfilled without coupling that
    tradeoff to the user's preference weight.
    Hard feasibility checks run before scoring. Existing scheduled nodes are never
    moved. Candidate sets are expected to be bounded by the API layer (currently
    at most eight).
    """
    if beam_width < 1:
        raise ValueError("beam_width must be positive")
    weights = OBJECTIVE_WEIGHTS.copy()
    if objective_weights is not None:
        unknown_weights = set(objective_weights) - set(weights)
        if unknown_weights:
            raise ValueError("unknown objective weight")
        weights.update(objective_weights)
    if any(
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not isfinite(value)
        or value < 0
        for value in weights.values()
    ):
        raise ValueError("objective weights must be finite and non-negative")
    ordered_removed = sorted(
        removed_nodes, key=lambda node: (node.day, node.start_time, node.stable_key)
    )
    if not ordered_removed:
        return WeatherReplacementSearchResult(
            (),
            {
                "filled_visits": 0,
                "route_time_delta_min": 0,
                "time_shift_min": 0,
                "wishlist_priority_sum": 0,
                "interest_score_sum": 0.0,
                "unfilled_visit_penalty": 0,
            },
            weights,
            beam_width,
            0,
            1,
            (),
        )
    beam = [_SearchState(tuple(scheduled_nodes), tuple(candidates), ())]
    states_pruned = 0
    peak_frontier_size = 1
    diagnostics: list[dict[str, object]] = []

    def rank(state: _SearchState) -> tuple:
        selected = sum(replacement is not None for _, replacement, _ in state.assignments)
        change_cost = 3 * (len(state.assignments) + selected)
        route_component = weights["route_time_delta_min"] * state.route_cost
        shift_component = weights["time_shift_min"] * state.time_shift
        score = (
            weights["plan_change_cost"] * change_cost
            + route_component
            + shift_component
            + weights["preference_penalty"] * state.preference_penalty
            + weights["unfilled_visit_penalty"] * state.unfilled_visit_penalty
        )
        assignment_names = tuple(
            replacement.name if replacement else "~" for _, replacement, _ in state.assignments
        )
        return (
            score,
            change_cost,
            state.route_cost,
            state.time_shift,
            -state.priority,
            -state.interest_score,
            assignment_names,
        )

    for removed in ordered_removed:
        expanded: list[_SearchState] = []
        rejection_counts: dict[str, int] = {}
        feasible_option_checks = 0
        explored_state_count = len(beam)
        for state in beam:
            options, reason, rejections = _weather_replacement_options(
                removed,
                list(state.scheduled),
                list(state.remaining),
                travel_minutes,
                condition=condition,
                max_nodes_per_day=max_nodes_per_day,
                minimum_transfer_min=minimum_transfer_min,
                closing_time=closing_time,
            )
            feasible_option_checks += len(options)
            for code, count in rejections.items():
                rejection_counts[code] = rejection_counts.get(code, 0) + count
            for option in options:
                candidate_id = option.node.poi_id
                expanded.append(
                    _SearchState(
                        (*state.scheduled, option.node),
                        tuple(
                            candidate
                            for candidate in state.remaining
                            if candidate.poi_id != candidate_id
                        ),
                        (*state.assignments, (removed, option.node, "OK")),
                        state.route_cost + option.route_cost,
                        state.time_shift + option.time_shift,
                        state.priority + option.priority,
                        state.interest_score + option.interest_score,
                        state.preference_penalty + option.preference_penalty,
                        state.unfilled_visit_penalty,
                    )
                )
            expanded.append(
                _SearchState(
                    state.scheduled,
                    state.remaining,
                    (
                        *state.assignments,
                        (removed, None, reason if not options else "CANDIDATE_CONFLICT"),
                    ),
                    state.route_cost,
                    state.time_shift,
                    state.priority,
                    state.interest_score,
                    state.preference_penalty,
                    state.unfilled_visit_penalty + 5 + removed.priority,
                )
            )
        diagnostics.append(
            {
                "removed_poi_id": str(removed.poi_id),
                "explored_state_count": explored_state_count,
                "feasible_option_checks": feasible_option_checks,
                "candidate_rejection_state_counts": rejection_counts,
            }
        )
        peak_frontier_size = max(peak_frontier_size, len(expanded))
        states_pruned += max(0, len(expanded) - beam_width)
        beam = sorted(expanded, key=rank)[:beam_width]

    best = min(beam, key=rank)
    filled = sum(replacement is not None for _, replacement, _ in best.assignments)
    assignments = list(best.assignments)
    used_ids = {replacement.poi_id for _, replacement, _ in assignments if replacement}
    selected_nodes = [replacement for _, replacement, _ in assignments if replacement]
    for index, (removed, replacement, reason) in enumerate(assignments):
        if replacement is not None or reason not in {"CANDIDATE_CONFLICT", "NO_CANDIDATES"}:
            continue
        available = [candidate for candidate in candidates if candidate.poi_id not in used_ids]
        other_nodes = list(scheduled_nodes) + [
            node for node in selected_nodes if node.day == removed.day
        ]
        options, fallback, _ = _weather_replacement_options(
            removed,
            other_nodes,
            available,
            travel_minutes,
            condition=condition,
            max_nodes_per_day=max_nodes_per_day,
            minimum_transfer_min=minimum_transfer_min,
            closing_time=closing_time,
        )
        if (
            not options
            and fallback == "NO_CANDIDATES"
            and any(candidate.poi_id in used_ids for candidate in candidates)
        ):
            fallback = "CANDIDATE_CONFLICT"
        assignments[index] = (
            removed,
            None,
            "OBJECTIVE_TRADEOFF" if options else fallback,
        )
    route_component = weights["route_time_delta_min"] * best.route_cost
    shift_component = weights["time_shift_min"] * best.time_shift
    change_cost = 3 * (len(best.assignments) + filled)
    unfilled_component = weights["unfilled_visit_penalty"] * best.unfilled_visit_penalty
    weighted_score = (
        weights["plan_change_cost"] * change_cost
        + route_component
        + shift_component
        + weights["preference_penalty"] * best.preference_penalty
        + unfilled_component
    )
    final_diagnostics = tuple(
        {
            **diagnostic,
            "outcome": "REPLACED" if replacement is not None else "UNFILLED",
            "reason_code": reason,
            "replacement_poi_id": str(replacement.poi_id) if replacement else None,
        }
        for diagnostic, (_, replacement, reason) in zip(diagnostics, assignments)
    )
    return WeatherReplacementSearchResult(
        tuple(assignments),
        {
            "filled_visits": filled,
            "plan_change_cost": change_cost,
            "route_time_delta_min": best.route_cost,
            "weighted_route_component": route_component,
            "time_shift_min": best.time_shift,
            "weighted_time_shift_component": shift_component,
            "preference_penalty": best.preference_penalty,
            "unfilled_visit_penalty": best.unfilled_visit_penalty,
            "weighted_unfilled_visit_component": unfilled_component,
            "weighted_score": weighted_score,
            "wishlist_priority_sum": best.priority,
            "interest_score_sum": best.interest_score,
        },
        weights,
        beam_width,
        states_pruned,
        peak_frontier_size,
        final_diagnostics,
    )


def select_weather_replacements(
    removed_nodes: list[Node],
    scheduled_nodes: list[Node],
    candidates: list[ReplanCandidate],
    travel_minutes: dict[tuple[UUID, UUID], int],
    *,
    condition: str,
    max_nodes_per_day: int = 6,
    minimum_transfer_min: int = 20,
    closing_time: time = time(20),
    beam_width: int = 128,
) -> list[tuple[Node, Node | None, str]]:
    """Return assignments from the bounded joint weather replacement search."""
    return list(
        search_weather_replacements(
            removed_nodes,
            scheduled_nodes,
            candidates,
            travel_minutes,
            condition=condition,
            max_nodes_per_day=max_nodes_per_day,
            minimum_transfer_min=minimum_transfer_min,
            closing_time=closing_time,
            beam_width=beam_width,
        ).assignments
    )
