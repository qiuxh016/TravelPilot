from decimal import Decimal

import pytest
from pydantic import ValidationError

from travelpilot_api.schemas.wishlist import WishlistCreate


def test_wishlist_accepts_valid_coordinates() -> None:
    item = WishlistCreate(
        name="成都大熊猫繁育研究基地",
        category="ATTRACTION",
        latitude=Decimal("30.73"),
        longitude=Decimal("104.14"),
        suggested_duration_min=180,
        priority=1,
    )

    assert item.name == "成都大熊猫繁育研究基地"
    assert item.priority == 1


def test_wishlist_rejects_invalid_latitude() -> None:
    with pytest.raises(ValidationError):
        WishlistCreate(
            name="错误地点",
            category="CUSTOM",
            latitude=Decimal("100"),
            longitude=Decimal("104"),
            suggested_duration_min=60,
            priority=3,
        )


def test_wishlist_rejects_invalid_priority() -> None:
    with pytest.raises(ValidationError):
        WishlistCreate(
            name="地点",
            category="CUSTOM",
            latitude=Decimal("30"),
            longitude=Decimal("104"),
            suggested_duration_min=60,
            priority=6,
        )