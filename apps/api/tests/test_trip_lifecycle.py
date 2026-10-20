from datetime import date

from travelpilot_api.trip_lifecycle import (
    due_planning_reminder,
    due_trip_reminder,
    lifecycle_status,
)


def test_lifecycle_uses_inclusive_trip_dates():
    start = date(2026, 10, 22)
    end = date(2026, 10, 24)

    assert lifecycle_status(start, end, date(2026, 10, 21)) == "UPCOMING"
    assert lifecycle_status(start, end, start) == "ONGOING"
    assert lifecycle_status(start, end, end) == "ONGOING"
    assert lifecycle_status(start, end, date(2026, 10, 25)) == "COMPLETED"


def test_planning_reminder_selects_nearest_missed_window_once():
    start = date(2026, 10, 22)

    assert due_planning_reminder(start, date(2026, 10, 15)) == ("PLAN_7D", date(2026, 10, 15))
    assert due_planning_reminder(start, date(2026, 10, 20)) == ("PLAN_2D", date(2026, 10, 20))
    assert due_planning_reminder(start, date(2026, 10, 21)) == ("PLAN_2D", date(2026, 10, 20))
    assert due_planning_reminder(start, date(2026, 10, 22)) is None
    assert due_planning_reminder(start, date(2026, 10, 14)) is None


def test_trip_reminders_are_suppressed_for_planned_or_cancelled_trips():
    start = date(2026, 10, 27)
    end = date(2026, 10, 28)

    assert due_trip_reminder(start, end, date(2026, 10, 20), has_plan=False) == ("PLAN_7D", date(2026, 10, 20))
    assert due_trip_reminder(start, end, date(2026, 10, 20), has_plan=True) is None
    assert due_trip_reminder(start, end, date(2026, 10, 20), has_plan=False, cancelled=True) is None
    assert due_trip_reminder(start, end, date(2026, 10, 29), has_plan=True) == ("AFTER_TRIP", date(2026, 10, 29))
