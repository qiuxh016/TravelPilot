from datetime import date
from uuid import uuid4

from travelpilot_core.engines.optimizer.itinerary import Candidate, build_itinerary


def candidate(name, duration=90, hours=None):
    return Candidate(uuid4(), name, 30.6, 104.0, duration, 3, 0.5, hours)


def test_visits_do_not_overlap_and_keep_daily_buffer():
    visits, warnings = build_itinerary(
        date(2026, 10, 1), date(2026, 10, 1),
        [candidate("A"), candidate("B")], [[0, 25], [25, 0]],
    )
    assert not warnings
    assert visits[1].start_time.hour * 60 + visits[1].start_time.minute >= 9 * 60 + 90 + 25
    assert visits[-1].end_time.hour < 20


def test_opening_hours_and_capacity_generate_warnings():
    visits, warnings = build_itinerary(
        date(2026, 10, 1), date(2026, 10, 1),
        [candidate("Closed", 120, {"open": "19:00", "close": "20:00"})],
    )
    assert not visits
    assert warnings


def test_reserved_lunch_window_moves_overlapping_visit_after_the_break():
    day = date(2026, 10, 1)
    visits, warnings = build_itinerary(
        day, day,
        [candidate("A", 120), candidate("B", 90)],
        [[0, 15], [15, 0]],
        reserved_break_windows={day: [(12 * 60, 13 * 60)]},
    )

    assert not warnings
    assert all(
        visit.end_time.hour * 60 + visit.end_time.minute <= 12 * 60
        or visit.start_time.hour * 60 + visit.start_time.minute >= 13 * 60
        for visit in visits
    )


def test_reserved_break_window_must_be_inside_trip_and_valid():
    day = date(2026, 10, 1)
    try:
        build_itinerary(
            day, day, [candidate("A")],
            reserved_break_windows={day: [(13 * 60, 12 * 60)]},
        )
    except ValueError as exc:
        assert "reserved break window" in str(exc)
    else:
        raise AssertionError("invalid break window should be rejected")
