from datetime import date

from travelpilot_api.advisor.turn_responses import append_route_outcome
from travelpilot_api.advisor.turn_service import _append_weather_context, _transport_route_summary
from travelpilot_api.advisor.weather_advice import daily_weather_advice, planned_weather_impacts


def test_rain_forecast_recommends_rain_gear_and_indoor_backup_for_storms():
    advice = daily_weather_advice({
        "weather_code": 95,
        "precipitation_probability_max": 70,
        "temperature_min_c": 18,
        "temperature_max_c": 23,
    })

    assert {item["kind"] for item in advice} == {"RAIN", "INDOOR_ALTERNATIVE"}


def test_temperature_range_and_heat_generate_layering_and_hydration_tips():
    advice = daily_weather_advice({
        "weather_code": 0,
        "precipitation_probability_max": 0,
        "temperature_min_c": 16,
        "temperature_max_c": 31,
    })

    assert {item["kind"] for item in advice} == {"TEMPERATURE_SWING", "HEAT"}


def test_clear_mild_forecast_still_suggests_checking_near_term_forecast():
    advice = daily_weather_advice({
        "weather_code": 1,
        "precipitation_probability_max": 5,
        "temperature_min_c": 15,
        "temperature_max_c": 22,
    })

    assert advice == [{"kind": "GENERAL", "text": "当前预报没有明显天气提醒；出发前仍建议查看临近预报。"}]


def test_missing_forecast_fields_do_not_produce_a_false_clear_weather_tip():
    assert daily_weather_advice({}) == [{
        "kind": "UNAVAILABLE",
        "text": "这天的预报数据不完整，暂时无法生成穿衣或雨具建议。",
    }]


def test_weather_and_packing_context_is_kept_with_indoor_place_results():
    reply = _append_weather_context(
        "找到室内展馆。",
        weather_result={
            "city": "成都", "source": "Open-Meteo", "fetched_at": "2026-10-18T08:00:00+08:00",
            "days": [{
                "date": "2026-11-02", "description": "小雨", "temperature_min_c": 15,
                "temperature_max_c": 19, "precipitation_probability_max": 70,
                "advice": [{"kind": "RAIN", "text": "建议带伞"}],
            }],
        },
        weather_requested=True,
        preparation_requested=True,
        weather_failures=[],
    )

    assert "找到室内展馆" in reply
    assert "天气参考：2026-11-02 小雨" in reply
    assert "Open-Meteo" in reply and "2026-10-18T08:00:00+08:00" in reply
    assert "建议带伞" in reply
    assert "通用随身清单" in reply


def test_identical_weather_advice_across_days_is_shown_only_once():
    repeated = {"kind": "RAIN", "text": "建议带伞或轻便雨衣。"}
    reply = _append_weather_context(
        "找到午餐候选。",
        weather_result={
            "source": "Local simulated forecast",
            "fetched_at": "2026-10-19T08:00:00+08:00",
            "days": [
                {"date": "2026-10-20", "weather_code": 61, "advice": [repeated]},
                {"date": "2026-10-21", "weather_code": 61, "advice": [repeated]},
            ],
        },
        weather_requested=True,
        preparation_requested=True,
        weather_failures=[],
    )

    assert reply.count("建议带伞或轻便雨衣") == 1


def test_weather_evidence_merge_is_idempotent_after_model_reply_composition():
    forecast = {
        "source": "Open-Meteo",
        "fetched_at": "2026-10-19T08:00:00+08:00",
        "days": [{
            "date": "2026-10-20", "description": "小雨", "weather_code": 61,
            "temperature_min_c": 13, "temperature_max_c": 20,
            "precipitation_probability_max": 75,
            "advice": [{"kind": "RAIN", "text": "建议带伞或轻便雨衣。"}],
        }],
    }
    model_reply = "我找到午餐候选。天气有小雨，建议带伞或轻便雨衣。"
    once = _append_weather_context(
        model_reply,
        weather_result=forecast,
        weather_requested=True,
        preparation_requested=True,
        weather_failures=[],
    )
    twice = _append_weather_context(
        once,
        weather_result=forecast,
        weather_requested=True,
        preparation_requested=True,
        weather_failures=[],
    )

    assert twice == once
    assert once.count("建议带伞或轻便雨衣") == 1
    assert "Open-Meteo" in once and "2026-10-19T08:00:00+08:00" in once


def test_weather_outage_merge_is_idempotent_and_visible_with_model_reply():
    model_reply = "我找到午餐候选。"
    once = _append_weather_context(
        model_reply,
        weather_result={},
        weather_requested=True,
        preparation_requested=True,
        weather_failures=["WEATHER_PROVIDER_UNAVAILABLE"],
    )
    twice = _append_weather_context(
        once,
        weather_result={},
        weather_requested=True,
        preparation_requested=True,
        weather_failures=["WEATHER_PROVIDER_UNAVAILABLE"],
    )

    assert twice == once
    assert "天气服务暂时不可用" in once
    assert "稍后重试" in once
    assert "建议带伞或轻便雨衣" not in once


def test_out_of_range_weather_context_does_not_ask_for_dates_again():
    reply = _append_weather_context(
        "找到室内展馆。",
        weather_result={
            "status": "OUT_OF_RANGE", "source": "Open-Meteo",
            "message": "当前预报暂未覆盖这段行程日期。", "days": [],
        },
        weather_requested=True,
        preparation_requested=True,
        weather_failures=[],
    )

    assert "预报暂未覆盖" in reply
    assert "临近出发时再按天气调整" in reply
    assert "补充旅行日期后" not in reply


def test_transport_result_keeps_requested_weather_context_in_same_answer():
    route_reply = _transport_route_summary({
        "origin_name": "人民公园", "destination_name": "武侯祠", "mode": "WALKING",
        "distance_m": 1800, "duration_min": 25, "source": "AMap Web Service",
        "retrieved_at": "2026-10-18T08:00:00+08:00",
    })
    reply = _append_weather_context(
        route_reply,
        weather_result={
            "source": "Open-Meteo", "fetched_at": "2026-10-18T08:00:00+08:00",
            "days": [{
                "date": "2026-11-02", "description": "小雨", "temperature_min_c": 15,
                "temperature_max_c": 19, "precipitation_probability_max": 70,
                "advice": [{"kind": "RAIN", "text": "建议带伞"}],
            }],
        },
        weather_requested=True,
        preparation_requested=False,
        weather_failures=[],
    )

    assert "人民公园到武侯祠的步行路线" in reply
    assert "AMap Web Service" in reply
    assert "天气参考：2026-11-02 小雨" in reply
    assert "Open-Meteo" in reply and "建议带伞" in reply


def test_compound_weather_reply_keeps_route_failure_visible():
    reply = append_route_outcome(
        "成都预报有雨，建议带伞。",
        route=None,
        requested=True,
        failed=True,
    )

    assert "成都预报有雨，建议带伞" in reply
    assert "路线本轮未能核验" in reply
    assert "没有用直线距离替代驾车路线" in reply


def test_compound_route_success_is_added_once():
    route = {
        "origin_name": "人民公园", "destination_name": "武侯祠", "mode": "DRIVING",
        "distance_m": 1800, "duration_min": 10, "source": "Local simulated maps",
        "retrieved_at": "2026-10-18T08:00:00+08:00",
    }
    reply = append_route_outcome(
        "成都预报有雨，建议带伞。", route=route, requested=True, failed=False,
    )

    assert reply.count("人民公园到武侯祠的驾车路线") == 1
    assert "成都预报有雨，建议带伞" in reply


def test_planned_weather_impacts_only_explicit_high_sensitivity_outdoor_stops():
    class Schedule:
        day = date(2026, 10, 20)

    selected = [
        {"id": "wet", "name": "河边步道", "environment_profile": {
            "exposure": "OUTDOOR", "sensitivity": {"RAIN": "HIGH"},
        }},
        {"id": "unknown", "name": "未标注地点", "environment_profile": None},
        {"id": "indoor", "name": "博物馆", "environment_profile": {
            "exposure": "INDOOR", "sensitivity": {"RAIN": "LOW"},
        }},
    ]
    impacts = planned_weather_impacts(
        selected,
        {"wet": Schedule(), "unknown": Schedule(), "indoor": Schedule()},
        [{"date": "2026-10-20", "precipitation_probability_max": 70}],
    )

    assert len(impacts) == 1
    assert impacts[0]["candidate_id"] == "wet"
    assert impacts[0]["name"] == "河边步道"


def test_planned_weather_impacts_ignore_dry_days_and_missing_schedules():
    selected = [{"id": "wet", "name": "河边步道", "environment_profile": {
        "exposure": "OUTDOOR", "sensitivity": {"RAIN": "HIGH"},
    }}]

    assert planned_weather_impacts(selected, {}, [
        {"date": "2026-10-20", "precipitation_probability_max": 0},
    ]) == []
