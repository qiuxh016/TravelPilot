from dataclasses import dataclass, replace
from datetime import date, datetime, time
from uuid import UUID


@dataclass(frozen=True)
class Node:
    poi_id: UUID
    name: str
    category: str
    day: date
    start_time: time
    end_time: time
    priority: int
    status: str = "PLANNED"
    note: str | None = None

    @property
    def stable_key(self) -> str:
        return str(self.poi_id)


def _minutes(value: time) -> int:
    return value.hour * 60 + value.minute


def _clock(value: int) -> time:
    return time(value // 60, value % 60)


def affected(node: Node, effective_at: datetime) -> bool:
    """The first version only touches the event day, from the event time onward."""
    return node.day == effective_at.date() and node.start_time >= effective_at.time().replace(tzinfo=None)


def replan(nodes: list[Node], event_type: str, effective_at: datetime,
           payload: dict) -> tuple[list[Node], list[str]]:
    if event_type not in {"WEATHER_CHANGE", "USER_FATIGUE", "POI_CANCELLED",
                          "TIME_DELAY", "USER_ADD", "RESERVATION_CHANGE"}:
        raise ValueError("INVALID_EVENT_TYPE")
    result: list[Node] = []
    warnings: list[str] = []
    target = str(payload.get("poi_id", ""))
    eligible = [node for node in nodes if affected(node, effective_at) and node.status not in {"FIXED", "RESERVED"}]
    fatigue_remove = {node.stable_key for node in sorted(eligible, key=lambda item: (item.priority, item.start_time))[:max(1, len(eligible) // 2)]} if eligible else set()
    for node in nodes:
        if not affected(node, effective_at):
            result.append(node)
            continue
        if node.status in {"FIXED", "RESERVED"}:
            result.append(node)
            warnings.append(f"固定预约未移动：{node.name}")
            continue
        if event_type == "POI_CANCELLED" and node.stable_key == target:
            warnings.append(f"地点取消：{node.name}")
            continue
        if event_type == "WEATHER_CHANGE" and node.category == "ATTRACTION":
            warnings.append(f"天气影响室外地点：{node.name}")
            continue
        if event_type == "USER_FATIGUE" and node.stable_key in fatigue_remove:
            warnings.append(f"减少疲劳负担：{node.name}")
            continue
        if event_type == "TIME_DELAY":
            delay = int(payload.get("delay_min", 0))
            if delay < 1 or delay > 180:
                raise ValueError("INVALID_DELAY")
            start, end = _minutes(node.start_time) + delay, _minutes(node.end_time) + delay
            if end > 20 * 60:
                warnings.append(f"延误后当天无法容纳：{node.name}")
                continue
            result.append(replace(node, start_time=_clock(start), end_time=_clock(end)))
            continue
        if event_type == "RESERVATION_CHANGE" and node.stable_key == target:
            if not payload.get("start_time") or not payload.get("end_time"):
                warnings.append(f"预约变更待用户提供时间：{node.name}")
            else:
                start, end = time.fromisoformat(payload["start_time"]), time.fromisoformat(payload["end_time"])
                if start < time(9) or end > time(20) or end <= start:
                    raise ValueError("INVALID_RESERVATION_TIME")
                node = replace(node, start_time=start, end_time=end, status="FIXED")
        result.append(node)
    return result, warnings


def node_data(node: Node) -> dict:
    return {"stable_key": node.stable_key, "poi_id": str(node.poi_id), "name": node.name,
            "day": node.day.isoformat(), "start_time": node.start_time.isoformat(),
            "end_time": node.end_time.isoformat(), "priority": node.priority,
            "status": node.status, "note": node.note}


def plan_diff(before: list[Node], after: list[Node], reason_code: str) -> list[dict]:
    old = {node.stable_key: node for node in before}
    new = {node.stable_key: node for node in after}
    changes = []
    for key in sorted(old.keys() | new.keys()):
        a, b = old.get(key), new.get(key)
        if a is None:
            operation, cost = "ADD", 3
        elif b is None:
            operation, cost = "REMOVE", 3
        elif (a.day, a.start_time, a.end_time) != (b.day, b.start_time, b.end_time):
            operation, cost = "MOVE", 2
        elif a != b:
            operation, cost = "UPDATE", 1
        else:
            operation, cost = "KEEP", 0
        changes.append({"stable_key": key, "operation": operation,
                        "reason_code": reason_code if cost else "UNCHANGED",
                        "before": node_data(a) if a else None,
                        "after": node_data(b) if b else None, "change_cost": cost})
    return changes
