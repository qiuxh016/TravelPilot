"use client";

import { useState } from "react";

const scenarios = {
  sunny: { label: "晴朗", icon: "☀", temperature: "22°", advice: "适合安排户外活动" },
  cloudy: { label: "多云", icon: "☁", temperature: "19°", advice: "出发前再确认天气" },
  rain: { label: "降雨", icon: "☂", temperature: "16°", advice: "可考虑室内地点替代" },
  unavailable: { label: "暂不可用", icon: "—", temperature: "--", advice: "天气服务尚未接入" },
} as const;

type WeatherDemoProps = {
  days: string[];
};

export function WeatherDemo({ days }: WeatherDemoProps) {
  const [scenarioKey, setScenarioKey] = useState<keyof typeof scenarios>("sunny");
  const scenario = scenarios[scenarioKey];
  const dayLabels = (days.length > 0 ? days : ["行程日期待定"]).slice(0, 5);

  return <section className="weather-panel" aria-labelledby="weather-title">
    <div className="weather-panel-heading">
      <div><p className="eyebrow">WEATHER PREVIEW</p><h2 id="weather-title">天气与行程提示</h2></div>
      <span className="demo-badge">本地模拟</span>
    </div>
    <div className="weather-controls">
      <label htmlFor="weather-scenario">切换演示情景</label>
      <select id="weather-scenario" value={scenarioKey} onChange={(event) => setScenarioKey(event.target.value as keyof typeof scenarios)}>
        <option value="sunny">晴朗样例</option>
        <option value="cloudy">多云样例</option>
        <option value="rain">降雨样例</option>
        <option value="unavailable">模拟服务不可用</option>
      </select>
    </div>
    <div className="weather-days">
      {dayLabels.map((day, index) => <article className="weather-day" key={`${day}-${index}`}>
        <span className="weather-day-date">{day}</span>
        <span className="weather-icon" aria-hidden="true">{scenario.icon}</span>
        <strong>{scenario.temperature}</strong>
        <span>{scenario.label}</span>
      </article>)}
    </div>
    <p className="weather-advice">{scenario.advice}</p>
    <p className="weather-disclaimer">仅为界面演示样例，不是实时预报，不会触发行程重规划。</p>
  </section>;
}
