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
