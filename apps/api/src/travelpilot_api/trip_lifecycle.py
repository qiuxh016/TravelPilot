from datetime import date, timedelta
from typing import Literal

TripLifecycleStatus = Literal["UPCOMING", "ONGOING", "COMPLETED"]


def lifecycle_status(start_date: date, end_date: date, today: date) -> TripLifecycleStatus:
    if today < start_date:
        return "UPCOMING"
    if today > end_date:
        return "COMPLETED"
    return "ONGOING"


def due_planning_reminder(start_date: date, today: date) -> tuple[str, date] | None:
    days_until_start = (start_date - today).days
    if 0 < days_until_start <= 2:
        return "PLAN_2D", start_date - timedelta(days=2)
    if 2 < days_until_start <= 7:
        return "PLAN_7D", start_date - timedelta(days=7)
    return None


def due_trip_reminder(
    start_date: date,
    end_date: date,
    today: date,
    has_plan: bool,
    cancelled: bool = False,
) -> tuple[str, date] | None:
    if cancelled:
        return None
    if end_date < today:
        return "AFTER_TRIP", end_date + timedelta(days=1)
    if not has_plan:
        return due_planning_reminder(start_date, today)
    return None
