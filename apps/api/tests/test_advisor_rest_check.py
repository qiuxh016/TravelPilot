from datetime import date, time

from travelpilot_api.advisor.draft_service import rest_check_item


def test_rest_check_flags_only_long_days_without_a_clear_break():
    tightly_scheduled = [
        (time(9, 0), time(12, 0)),
        (time(12, 30), time(15, 30)),
        (time(16, 0), time(18, 30)),
    ]
    with_break = [
        (time(9, 0), time(12, 0)),
        (time(12, 45), time(15, 45)),
        (time(16, 0), time(18, 30)),
    ]
    short_day = [
        (time(9, 0), time(11, 0)),
        (time(11, 30), time(14, 30)),
        (time(15, 0), time(17, 0)),
    ]

    assert rest_check_item(date(2026, 11, 2), tightly_scheduled) == {
        "kind": "REST_CHECK", "day": "2026-11-02",
    }
    assert rest_check_item(date(2026, 11, 2), with_break) is None
    assert rest_check_item(date(2026, 11, 2), short_day) is None
    assert rest_check_item(date(2026, 11, 2), tightly_scheduled[:2]) is None
