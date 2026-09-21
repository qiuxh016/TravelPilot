"""Independent checks for generated visits."""

from collections import Counter
from datetime import time

from travelpilot_core.engines.optimizer.itinerary import Visit


def validate_visits(visits: list[Visit], *, max_pois_per_day: int = 6,
                    buffer_min: int = 60) -> list[str]:
    warnings = []
    counts = Counter(visit.day for visit in visits)
    for day, count in counts.items():
        if count > max_pois_per_day:
            warnings.append(f"{day} 超过每日 {max_pois_per_day} 个地点")
    for visit in visits:
        if visit.start_time < time(9) or visit.end_time > time(21 - buffer_min // 60, 0):
            warnings.append(f"{visit.candidate.name} 超出当天时间窗口")
        hours = visit.candidate.opening_hours or {}
        if hours.get("open") and visit.start_time < time.fromisoformat(hours["open"]):
            warnings.append(f"{visit.candidate.name} 尚未营业")
        if hours.get("close") and visit.end_time > time.fromisoformat(hours["close"]):
            warnings.append(f"{visit.candidate.name} 超出营业时间")
    return warnings
