from datetime import UTC, date, datetime, time
from uuid import uuid4

import pytest
from pydantic import ValidationError
from travelpilot_api.api.v1.events import _safe_weather_alternative
from travelpilot_api.schemas.environment import EnvironmentProfileInput, user_profile
from travelpilot_api.schemas.events import EventCreate
from travelpilot_core.engines.impact_analysis.replan import Node, replan
from travelpilot_infra.db.models.poi import PoiModel
from travelpilot_infra.db.models.wishlist import WishlistItemModel


def test_user_environment_profile_records_provenance_and_unknown_stays_empty():
    profile = EnvironmentProfileInput(
        exposure="OUTDOOR", sensitivity={"RAIN": "HIGH"}, evidence="用户手动确认",
    )

    assert user_profile(profile) == {
        "exposure": "OUTDOOR", "sensitivity": {"RAIN": "HIGH"},
        "evidence": "用户手动确认", "source": "USER", "confidence": 0.8,
    }
    assert user_profile(EnvironmentProfileInput()) is None


def test_environment_profile_rejects_unknown_weather_dimensions():
    with pytest.raises(ValidationError):
        EnvironmentProfileInput.model_validate({
            "exposure": "OUTDOOR", "sensitivity": {"HURRICANE": "HIGH"},
        })


def test_weather_event_rejects_unsupported_condition():
    with pytest.raises(ValidationError, match="supported weather condition"):
        EventCreate.model_validate({
            "event_type": "WEATHER_CHANGE",
            "effective_at": "2026-10-01T12:00:00+08:00",
            "payload": {"condition": "ALIENS"},
        })


def test_weather_replan_removes_only_high_sensitivity_outdoor_and_keeps_unknown():
    day = date(2026, 10, 1)
    outdoor = Node(uuid4(), "河边步道", "ATTRACTION", day, time(13), time(14), 3,
                   environment_profile={"exposure": "OUTDOOR", "sensitivity": {"RAIN": "HIGH"}})
    indoor = Node(uuid4(), "博物馆", "ATTRACTION", day, time(14), time(15), 3,
                  environment_profile={"exposure": "INDOOR", "sensitivity": {"RAIN": "LOW"}})
    unknown = Node(uuid4(), "未标注景点", "ATTRACTION", day, time(15), time(16), 3)

    revised, warnings = replan(
        [outdoor, indoor, unknown], "WEATHER_CHANGE",
        datetime(2026, 10, 1, 12, tzinfo=UTC), {"condition": "RAIN"},
    )

    assert outdoor not in revised
    assert indoor in revised and unknown in revised
    assert any("属性未知" in warning for warning in warnings)


@pytest.mark.parametrize(
    ("profile", "expected"),
    [
        ({"exposure": "INDOOR"}, True),
        ({"exposure": "OUTDOOR", "sensitivity": {"RAIN": "LOW"}}, True),
        ({"exposure": "OUTDOOR", "sensitivity": {"RAIN": "HIGH"}}, False),
        ({}, False),
    ],
)
def test_weather_substitute_requires_explicit_safe_profile(profile, expected):
    item = WishlistItemModel(environment_profile=profile)
    poi = PoiModel(category="MUSEUM", environment_profile=None)

    assert _safe_weather_alternative(item, poi, "RAIN") is expected
