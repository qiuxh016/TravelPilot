"""Conservative, explainable packing tips derived from daily forecast fields."""


_WET_WEATHER_CODES = {51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82, 85, 86, 95, 96, 99}
_SEVERE_WEATHER_CODES = {65, 67, 82, 86, 95, 96, 99}


def daily_weather_advice(day: dict) -> list[dict[str, str]]:
    """Return short tips only when the provider supplied usable forecast evidence."""
    code = day.get("weather_code")
    precipitation = day.get("precipitation_probability_max")
    low = day.get("temperature_min_c")
    high = day.get("temperature_max_c")
    advice: list[dict[str, str]] = []

    has_usable_data = (
        isinstance(code, int) and not isinstance(code, bool)
        or isinstance(precipitation, (int, float)) and not isinstance(precipitation, bool)
        or isinstance(low, (int, float)) and not isinstance(low, bool)
        or isinstance(high, (int, float)) and not isinstance(high, bool)
    )
    if not has_usable_data:
        return [{"kind": "UNAVAILABLE", "text": "这天的预报数据不完整，暂时无法生成穿衣或雨具建议。"}]

    if (isinstance(precipitation, (int, float)) and precipitation >= 40) or code in _WET_WEATHER_CODES:
        advice.append({"kind": "RAIN", "text": "降水可能性偏高，建议带伞或轻便雨衣。"})
    if code in _SEVERE_WEATHER_CODES:
        advice.append({"kind": "INDOOR_ALTERNATIVE", "text": "预报包含较强降水或雷雨，户外活动前留意临近预报，可准备室内备选。"})
    if isinstance(low, (int, float)) and low <= 8:
        advice.append({"kind": "COLD", "text": "早晚偏冷，建议分层穿衣并准备保暖外套。"})
    elif (
        isinstance(low, (int, float))
        and isinstance(high, (int, float))
        and high - low >= 10
    ):
        advice.append({"kind": "TEMPERATURE_SWING", "text": "昼夜温差较大，建议采用方便增减的分层穿搭。"})
    if isinstance(high, (int, float)) and high >= 30:
        advice.append({"kind": "HEAT", "text": "白天偏热，户外活动可准备防晒用品和饮水。"})
    if not advice:
        advice.append({"kind": "GENERAL", "text": "当前预报没有明显天气提醒；出发前仍建议查看临近预报。"})
    return advice


def planned_weather_impacts(
    selected: list[dict], schedules: dict, forecast_days: list[dict],
) -> list[dict[str, str]]:
    """Flag only explicit high rain-sensitive outdoor stops; never infer exposure."""
    rainy_dates = {
        str(day.get("date")) for day in forecast_days
        if (isinstance(day.get("precipitation_probability_max"), (int, float))
            and day["precipitation_probability_max"] >= 40)
        or day.get("weather_code") in _WET_WEATHER_CODES
    }
    impacts = []
    for item in selected:
        schedule = schedules.get(str(item.get("id")))
        profile = item.get("environment_profile") or {}
        sensitivity = profile.get("sensitivity") or {}
        day = getattr(schedule, "day", None)
        day_text = day.isoformat() if hasattr(day, "isoformat") else str(day or "")
        if (day_text in rainy_dates and profile.get("exposure") == "OUTDOOR"
                and sensitivity.get("RAIN") == "HIGH"):
            impacts.append({
                "candidate_id": str(item.get("id")),
                "name": str(item.get("name", "该户外地点")),
                "day": day_text,
                "reason": "该地点标记为户外且雨敏感度高，预报降水概率较高或有降水。",
            })
    return impacts
