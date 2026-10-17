from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Exposure = Literal["INDOOR", "OUTDOOR", "MIXED", "UNKNOWN"]
Sensitivity = Literal["LOW", "MODERATE", "HIGH", "UNKNOWN"]
WeatherCondition = Literal["RAIN", "WIND", "HEAT", "COLD", "SNOW", "STORM"]
ProfileSource = Literal["USER", "PROVIDER", "EDITORIAL", "INFERRED", "UNKNOWN"]


class EnvironmentProfileInput(BaseModel):
    exposure: Exposure = "UNKNOWN"
    sensitivity: dict[WeatherCondition, Sensitivity] = Field(default_factory=dict)
    evidence: str | None = Field(default=None, max_length=500)


class EnvironmentProfileRead(BaseModel):
    model_config = ConfigDict(extra="ignore")

    exposure: Exposure = "UNKNOWN"
    sensitivity: dict[WeatherCondition, Sensitivity] = Field(default_factory=dict)
    source: ProfileSource = "UNKNOWN"
    confidence: float = Field(default=0, ge=0, le=1)
    evidence: str | None = None


def user_profile(profile: EnvironmentProfileInput | None) -> dict | None:
    if profile is None:
        return None
    data = profile.model_dump()
    if data["exposure"] == "UNKNOWN" and not data["sensitivity"]:
        return None
    return {**data, "source": "USER", "confidence": 0.8}
