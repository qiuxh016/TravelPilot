"""Deterministic validation for agent-observed travel evidence."""

from math import isfinite
from typing import Any


def verify_trip_context(output: Any, expected_trip_id: str) -> bool:
    if not isinstance(output, dict) or output.get("trip_id") != expected_trip_id:
        return False
    wishlist = output.get("wishlist")
    points = output.get("points")
    memories = output.get("memories")
    if (
        not isinstance(wishlist, list)
        or any(not isinstance(name, str) for name in wishlist)
        or not isinstance(points, list)
        or len(points) != len(wishlist)
        or not isinstance(memories, list)
        or any(not isinstance(memory, str) for memory in memories)
        or not isinstance(output.get("preference"), str)
    ):
        return False
    for point in points:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            return False
        latitude, longitude = point
        if not _valid_number(latitude) or not _valid_number(longitude):
            return False
        if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
            return False
    return True


def verify_distance_matrix(output: Any, expected_size: int) -> bool:
    if expected_size < 2 or not isinstance(output, dict):
        return False
    matrix = output.get("durations_min")
    if not isinstance(matrix, list) or len(matrix) != expected_size:
        return False
    for index, row in enumerate(matrix):
        if not isinstance(row, list) or len(row) != expected_size:
            return False
        if any(not _valid_number(value) or value < 0 for value in row):
            return False
        # A route from a place to itself must take zero minutes. Accepting a
        # positive diagonal can hide malformed provider matrices and makes the
        # evidence contract weaker than the distance-matrix tool semantics.
        if row[index] != 0:
            return False
    return True


def _valid_number(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and isfinite(value)
    )
