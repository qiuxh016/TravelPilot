from datetime import date

import pytest
from pydantic import ValidationError

from travelpilot_api.schemas.trips import TripCreate


def test_trip_create_accepts_valid_input() -> None:
    trip = TripCreate(
        destination="成都",
        start_date=date(2026, 10, 1),
        end_date=date(2026, 10, 4),
        budget=6000,
        pace_level=2,
    )

    assert trip.destination == "成都"
    assert trip.pace_level == 2


def test_trip_rejects_invalid_pace_level() -> None:
    with pytest.raises(ValidationError):
        TripCreate(
            destination="成都",
            start_date=date(2026, 10, 1),
            end_date=date(2026, 10, 4),
            budget=6000,
            pace_level=6,
        )


def test_trip_rejects_invalid_dates() -> None:
    with pytest.raises(ValidationError):
        TripCreate(
            destination="成都",
            start_date=date(2026, 10, 5),
            end_date=date(2026, 10, 1),
            budget=6000,
            pace_level=2,
        )