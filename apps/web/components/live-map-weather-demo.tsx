"use client";

import { useEffect, useState } from "react";
import {
  getStaticMapImage,
  getWeatherForecast,
  searchPlaces,
  type Place,
  type TripWeather,
} from "../lib/api";

function dateOffset(days: number) {
  const date = new Date();
  date.setDate(date.getDate() + days);
  const localDate = new Date(date.getTime() - date.getTimezoneOffset() * 60_000);
  return localDate.toISOString().slice(0, 10);
}

function weatherIcon(code: number) {
  if ([95, 96, 99].includes(code)) return "⛈";
  if ([51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82].includes(code)) return "☂";
  if ([71, 73, 75, 77, 85, 86].includes(code)) return "❄";
  if ([2, 3, 45, 48].includes(code)) return "☁";
  return "☀";
}

export function LiveMapWeatherDemo() {
  const [city, setCity] = useState("成都");
  const [activeCity, setActiveCity] = useState("成都");
  const [weather, setWeather] = useState<TripWeather | null>(null);
  const [places, setPlaces] = useState<Place[]>([]);
  const [mapImage, setMapImage] = useState("");
  const [weatherError, setWeatherError] = useState("");
  const [mapError, setMapError] = useState("");
  const [weatherLoading, setWeatherLoading] = useState(true);
  const [mapLoading, setMapLoading] = useState(true);

  async function loadCity(nextCity: string) {
    const normalizedCity = nextCity.trim();
    if (!normalizedCity) return;
    setActiveCity(normalizedCity);
    setWeatherLoading(true);
    setMapLoading(true);
    setWeatherError("");
    setMapError("");
    setWeather(null);
    setPlaces([]);
    setMapImage("");

    const startDate = dateOffset(0);
    const endDate = dateOffset(2);
    const [weatherResult, placesResult] = await Promise.allSettled([
      getWeatherForecast(normalizedCity, startDate, endDate),
      searchPlaces("景点", normalizedCity),
    ]);

    if (weatherResult.status === "fulfilled") setWeather(weatherResult.value);
    else setWeatherError(weatherResult.reason instanceof Error ? weatherResult.reason.message : "天气预报暂时不可用");
    setWeatherLoading(false);

    if (placesResult.status === "rejected") {
      setMapError(placesResult.reason instanceof Error ? placesResult.reason.message : "地点搜索暂时不可用");
      setMapLoading(false);
      return;
    }

    let foundPlaces = placesResult.value;
    if (!foundPlaces.length) foundPlaces = await searchPlaces("公园", normalizedCity).catch(() => []);
    const visiblePlaces = foundPlaces.slice(0, 8);
    setPlaces(visiblePlaces);
    if (!visiblePlaces.length) {
      setMapError("高德没有找到该城市的景点，请尝试输入城市全称。");
      setMapLoading(false);
      return;
    }

    try {
      setMapImage(await getStaticMapImage(visiblePlaces));
    } catch (reason) {
      setMapError(reason instanceof Error ? reason.message : "地图暂时不可用");
    } finally {
      setMapLoading(false);
    }
  }

  useEffect(() => {
    void loadCity("成都");
    // Load one live sample when this page is opened.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => () => {
    if (mapImage) URL.revokeObjectURL(mapImage);
  }, [mapImage]);

  return <div className="live-map-weather-demo">
    <section className="map-panel" aria-labelledby="live-map-title">
      <div className="map-panel-heading">
        <div><p className="eyebrow">LIVE MAP</p><h2 id="live-map-title">真实地点与地图</h2></div>
        <span className="demo-badge">{places.length ? "高德地图 · 真实地点" : "高德地图"}</span>
      </div>
      <form className="live-data-controls" onSubmit={(event) => { event.preventDefault(); void loadCity(city); }}>
        <label htmlFor="live-city">目的地</label>
        <input id="live-city" value={city} onChange={(event) => setCity(event.target.value)} placeholder="例如：成都、上海" />
        <button className="primary" type="submit" disabled={weatherLoading || mapLoading}>查询真实数据</button>
      </form>
      <div className="live-map-frame">
        {mapImage ? <img src={mapImage} alt={`${activeCity}真实地图及高德搜索地点`} /> : null}
        {mapLoading ? <p role="status">正在搜索真实地点并加载地图…</p> : null}
        {mapError ? <p className="live-data-error" role="alert">{mapError}</p> : null}
      </div>
      {places.length ? <ol className="map-point-list">
        {places.map((place, index) => <li key={place.provider_id}>
          <span className="map-point-number">{index + 1}</span>
          <span><strong>{place.name}</strong><small>{place.address || activeCity}</small></span>
        </li>)}
      </ol> : null}
      <p className="map-disclaimer">地点和底图来自高德；标点按搜索结果位置绘制，连线只表示顺序，不是导航路线。</p>
    </section>

    <section className="weather-panel" aria-labelledby="live-weather-title">
      <div className="weather-panel-heading">
        <div><p className="eyebrow">LIVE WEATHER FORECAST</p><h2 id="live-weather-title">{weather?.city ?? `${activeCity}天气预报`}</h2></div>
        <span className="demo-badge">{weather?.source ?? "Open-Meteo"}</span>
      </div>
      {weatherLoading ? <p role="status">正在获取实时天气预报…</p> : null}
      {weatherError ? <p className="live-data-error" role="alert">{weatherError}</p> : null}
      {!weatherLoading && !weatherError && weather?.days.length ? <>
        <div className="weather-days">
          {weather.days.map((day) => <article className="weather-day" key={day.date}>
            <span className="weather-day-date">{day.date}</span>
            <span className="weather-icon" aria-hidden="true">{weatherIcon(day.weather_code)}</span>
            <strong>{day.temperature_max_c == null || day.temperature_min_c == null ? "—" : `${Math.round(day.temperature_max_c)}° / ${Math.round(day.temperature_min_c)}°`}</strong>
            <span>{day.description}</span>
            <span>降水概率 {day.precipitation_probability_max == null ? "—" : `${day.precipitation_probability_max}%`}</span>
          </article>)}
        </div>
        <p className="weather-disclaimer">更新于 {new Date(weather.fetched_at).toLocaleString("zh-CN")} · 数据来自 <a href="https://open-meteo.com/" target="_blank" rel="noreferrer">Open-Meteo</a>（CC BY 4.0）。这是天气预报，可能变化。</p>
      </> : null}
      {!weatherLoading && !weatherError && !weather?.days.length ? <p className="weather-advice">{weather?.message ?? "当前暂无可用预报。"}</p> : null}
      <p className="weather-disclaimer">Open-Meteo 免费接口仅用于个人非商业试用。</p>
    </section>
  </div>;
}
