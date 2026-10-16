"use client";

import { useEffect, useState } from "react";
import { ApiError, getTripWeather, type TripWeather } from "../lib/api";

type Props = { tripId: string };

export function WeatherForecast({ tripId }: Props) {
  const [weather, setWeather] = useState<TripWeather | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError("");
    setWeather(null);
    getTripWeather(tripId)
      .then((result) => { if (active) setWeather(result); })
      .catch((reason: unknown) => {
        if (!active) return;
        if (reason instanceof ApiError && reason.status === 404) {
          setError("当前 API 尚未加载天气接口，请重启 TravelPilot API 后刷新页面。");
        } else {
          setError(reason instanceof Error ? reason.message : "天气预报暂时不可用");
        }
      })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [tripId]);

  return <section className="weather-panel" aria-labelledby="weather-title">
    <div className="weather-panel-heading">
      <div><p className="eyebrow">WEATHER FORECAST</p><h2 id="weather-title">{weather?.city ?? "行程天气预报"}</h2></div>
      <span className="demo-badge">{weather?.source ?? "Open-Meteo"}</span>
    </div>
    {loading ? <p role="status">正在获取天气预报…</p> : null}
    {error ? <p role="alert">{error}</p> : null}
    {!loading && !error && weather?.days.length ? <>
      <div className="weather-days">
        {weather.days.map((day) => <article className="weather-day" key={day.date}>
          <span className="weather-day-date">{day.date}</span>
          <span className="weather-icon" aria-hidden="true">{day.weather_code >= 51 ? "☂" : day.weather_code >= 2 ? "☁" : "☀"}</span>
          <strong>{day.temperature_max_c == null || day.temperature_min_c == null ? "—" : `${Math.round(day.temperature_max_c)}° / ${Math.round(day.temperature_min_c)}°`}</strong>
          <span>{day.description}</span>
          <span>降水概率 {day.precipitation_probability_max == null ? "—" : `${day.precipitation_probability_max}%`}</span>
        </article>)}
      </div>
      {weather.message ? <p className="weather-advice">{weather.message}</p> : null}
      <p className="weather-disclaimer">预报获取于 {new Date(weather.fetched_at).toLocaleString("zh-CN")} · 数据来自 <a href="https://open-meteo.com/" target="_blank" rel="noreferrer">Open-Meteo</a>（<a href="https://creativecommons.org/licenses/by/4.0/" target="_blank" rel="noreferrer">CC BY 4.0</a>）· 预报可能变化，请出行前再次确认。</p>
    </> : null}
    {!loading && !error && !weather?.days.length ? <p className="weather-advice">{weather?.message ?? "当前暂无可用预报。"}</p> : null}
    <p className="weather-disclaimer">免费接口仅用于个人非商业试用；最多显示当前 16 天预报。</p>
  </section>;
}
