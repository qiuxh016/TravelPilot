"""Grounded user-facing summaries for Advisor weather, route and schedule evidence."""

from datetime import date


def weather_day_summary(day: dict) -> str:
    low, high = day.get("temperature_min_c"), day.get("temperature_max_c")
    if isinstance(low, (int, float)) and isinstance(high, (int, float)):
        temperature = f"{low:g}–{high:g}°C"
    elif isinstance(low, (int, float)):
        temperature = f"最低 {low:g}°C"
    elif isinstance(high, (int, float)):
        temperature = f"最高 {high:g}°C"
    else:
        temperature = "温度数据缺失"
    precipitation = day.get("precipitation_probability_max")
    rain = (
        f"降水概率 {precipitation}%"
        if isinstance(precipitation, (int, float)) and not isinstance(precipitation, bool)
        else "降水概率数据缺失"
    )
    return f"{day.get('date')} {day.get('description') or '天气情况未知'}，{temperature}，{rain}"


def append_weather_context(
    reply: str,
    *,
    weather_result: dict | None,
    weather_requested: bool,
    preparation_requested: bool,
    weather_failures: list[str],
) -> str:
    """Keep requested forecast/preparation context visible beside POI results."""
    days = weather_result.get("days", []) if isinstance(weather_result, dict) else []
    if weather_requested and days:
        daily = "；".join(weather_day_summary(day) for day in days)
        advice_items = list(dict.fromkeys(
            str(item["text"])
            for day in days
            for item in day.get("advice", [])
            if isinstance(item, dict) and item.get("text")
        ))
        if "天气参考：" not in reply:
            reply += f" 天气参考：{daily}。来源：{weather_result.get('source')}，更新时间：{weather_result.get('fetched_at')}。"
        else:
            missing_provenance = [
                str(value) for value in (weather_result.get("source"), weather_result.get("fetched_at"))
                if value and str(value) not in reply
            ]
            if missing_provenance:
                reply += " 数据来源/更新时间：" + "，".join(missing_provenance) + "。"
        missing_advice = [item for item in advice_items if item not in reply]
        if missing_advice:
            reply += "出行提醒：" + "；".join(missing_advice) + "。"
    elif weather_requested:
        source = weather_result.get("source") if isinstance(weather_result, dict) else "天气服务"
        detail = weather_result.get("message") if isinstance(weather_result, dict) else None
        if weather_failures:
            if "天气服务暂时不可用" not in reply:
                reply += " 天气服务暂时不可用，本轮没有生成天气结论。"
            if "稍后重试" not in reply:
                reply += "请稍后重试。"
        else:
            if "本轮没有可用的天气预报" not in reply:
                reply += f" 本轮没有可用的天气预报（{source}）。"
            if detail and detail not in reply:
                reply += str(detail)
            elif not detail and not any(
                label in reply for label in ("补充旅行日期", "临近出发")
            ):
                reply += "请补充旅行日期后再查。"
    if preparation_requested:
        if "通用随身清单" not in reply:
            reply += "通用随身清单：身份证件、手机与充电器、常用药、舒适鞋和个人洗漱用品。"
        if not days:
            status = weather_result.get("status") if isinstance(weather_result, dict) else None
            if status == "OUT_OF_RANGE":
                follow_up = "这段日期超出当前预报范围，临近出发时再按天气调整衣物、雨具和室内备选。"
            elif weather_failures:
                follow_up = "天气服务恢复后，我再按预报提醒衣物、雨具和室内备选。"
            else:
                follow_up = "补充旅行日期后，我再按预报提醒衣物、雨具和室内备选。"
            if follow_up not in reply:
                reply += follow_up
    return reply


def transport_route_summary(route: dict) -> str:
    mode_label = {
        "WALKING": "步行",
        "TRANSIT": "公交/地铁",
        "DRIVING": "驾车",
    }.get(route.get("mode"), "交通")
    return (
        f"已核验{route['origin_name']}到{route['destination_name']}的{mode_label}路线："
        f"约 {route['distance_m'] / 1000:.1f} 公里、{route['duration_min']} 分钟。"
        f"数据来源：{route['source']}，查询时间：{route['retrieved_at']}。"
        "这只是路线时间估算，不包含实时路况；请在出发前再次核对。"
    )


def append_route_outcome(
    reply: str, *, route: dict | None, requested: bool, failed: bool,
) -> str:
    """Keep a requested route outcome visible beside another task result."""
    if not requested:
        return reply
    if route:
        summary = transport_route_summary(route)
        return reply if summary in reply else f"{summary}\n\n{reply}"
    if failed and "路线本轮未能核验" not in reply:
        return (
            f"{reply.rstrip()}\n\n路线本轮未能核验；我没有用直线距离替代驾车路线，"
            "你可以稍后重试地图服务。"
        )
    return reply


def schedule_proposal_reply(
    *, candidate_count: int, route_evidence: str | None,
    transport_route_requested: bool, route: dict | None, route_question: str | None,
    suggestions: list[dict] | None = None, candidates: list[dict] | None = None,
    start_date: date | str | None = None,
    reserved_breaks: list[dict] | None = None,
) -> str:
    """Compose schedule outcomes without overstating route evidence."""
    route_basis = (
        "结合已核验的地图交通时间和地图建议游览时长"
        if route_evidence == "VERIFIED"
        else "结合当前可用的路线估算和地图建议游览时长"
        if route_evidence == "SIMULATED"
        else "结合本地距离估算和地图建议游览时长（路线未核验）"
    )
    reply = (
        f"我根据本轮纳入排程的 {candidate_count} 个地点，{route_basis}排出一版时间建议。"
        "它只是待确认草案；检查路线和空档后，点击“确认并保存行程”才会生成新计划。"
    )
    assignments = _schedule_assignment_summary(
        suggestions or [], candidates or [], start_date=start_date,
    )
    if assignments:
        reply = f"已按草案安排：{assignments}\n\n{reply}"
    break_summary = _reserved_break_summary(reserved_breaks or [], start_date=start_date)
    if break_summary:
        reply += (
            f"\n\n草案还按你的要求预留了：{break_summary}。"
            "这是建议时段，尚未安排餐厅；你可以告诉我想改到几点。"
        )
    if not transport_route_requested:
        return reply
    if route:
        return reply + "\n\n" + transport_route_summary(route)
    if route_question:
        return reply + "\n\n路线部分还需要你确认：" + route_question
    return reply + "\n\n本轮未能核验单独的路线查询结果；时间建议中的路线依据已按上文标注，请稍后重试地图路线查询。"


def _schedule_assignment_summary(
    suggestions: list[dict], candidates: list[dict], *, start_date: date | str | None,
) -> str:
    """Summarize only the proposed date/time/name facts already in the draft."""
    names_by_id = {
        str(candidate.get("id")): str(candidate["name"])
        for candidate in candidates
        if candidate.get("id") and isinstance(candidate.get("name"), str)
    }
    grouped: dict[str, list[str]] = {}
    for suggestion in suggestions:
        candidate_id = suggestion.get("candidate_id")
        day = suggestion.get("day")
        start_time = suggestion.get("start_time")
        if not isinstance(day, str) or not isinstance(start_time, str):
            continue
        name = names_by_id.get(str(candidate_id))
        if not name:
            continue
        try:
            proposed_day = date.fromisoformat(day)
            date_label = f"{proposed_day.month}月{proposed_day.day}日"
            if start_date:
                first_day = (
                    start_date if isinstance(start_date, date)
                    else date.fromisoformat(start_date)
                )
                day_number = (proposed_day - first_day).days + 1
                if 1 <= day_number <= 31:
                    date_label = f"第{day_number}天（{date_label}）"
        except ValueError:
            continue
        grouped.setdefault(date_label, []).append(f"{name} {start_time}")
    return "；".join(
        f"{day}：{'、'.join(items)}" for day, items in grouped.items()
    )


def _reserved_break_summary(
    reserved_breaks: list[dict], *, start_date: date | str | None,
) -> str:
    labels_by_type = {"LUNCH": "午餐", "DINNER": "晚餐", "BREAKFAST": "早餐"}
    grouped: dict[str, list[str]] = {}
    for item in reserved_breaks:
        day = item.get("day")
        start_time = item.get("start_time")
        end_time = item.get("end_time")
        label = labels_by_type.get(str(item.get("meal_type")))
        if not all(isinstance(value, str) for value in (day, start_time, end_time)) or not label:
            continue
        try:
            proposed_day = date.fromisoformat(day)
            date_label = f"{proposed_day.month}月{proposed_day.day}日"
            if start_date:
                first_day = (
                    start_date if isinstance(start_date, date)
                    else date.fromisoformat(start_date)
                )
                day_number = (proposed_day - first_day).days + 1
                if 1 <= day_number <= 31:
                    date_label = f"第{day_number}天（{date_label}）"
        except ValueError:
            continue
        grouped.setdefault(date_label, []).append(f"{label} {start_time}–{end_time}")
    return "；".join(f"{day} {', '.join(items)}" for day, items in grouped.items())
