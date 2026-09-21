"""Coarse deterministic geographic grouping for a small trip Wishlist."""

from collections.abc import Iterable


def cluster_places[T](places: Iterable[T]) -> list[list[T]]:
    groups: dict[tuple[int, int], list[T]] = {}
    for place in places:
        key = (int(place.latitude // 0.05), int(place.longitude // 0.05))
        groups.setdefault(key, []).append(place)
    return [groups[key] for key in sorted(groups)]
