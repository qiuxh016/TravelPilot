"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { createPlanRun, listPlans, listWishlist, type Plan, type WishlistItem } from "../../../../lib/api";
import { RouteMap, type RouteMapPoint } from "../../../../components/route-map";
import { WeatherForecast } from "../../../../components/weather-forecast";

export default function PlanPage() {
  const tripId = useParams<{ tripId: string }>().tripId;
  const [plans, setPlans] = useState<Plan[]>([]);
  const [wishlist, setWishlist] = useState<WishlistItem[]>([]);
  const [activeDay, setActiveDay] = useState("");
  const [selectedStop, setSelectedStop] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    void Promise.all([listPlans(tripId), listWishlist(tripId)]).then(([nextPlans, nextWishlist]) => {
      setPlans(nextPlans); setWishlist(nextWishlist);
    }).catch((err) => setError(err.message));
  }, [tripId]);

  async function generate() {
    setBusy(true); setError("");
    try {
      await createPlanRun(tripId);
      const [nextPlans, nextWishlist] = await Promise.all([listPlans(tripId), listWishlist(tripId)]);
      setPlans(nextPlans); setWishlist(nextWishlist);
    } catch (err) { setError(err instanceof Error ? err.message : "规划失败"); }
    finally { setBusy(false); }
  }
  const latest = plans[0];
  const days = latest ? Array.from(new Set(latest.nodes.map((node) => node.day))) : [];
  const visibleDay = days.includes(activeDay) ? activeDay : days[0] ?? "";
  const dayNodes = latest?.nodes.filter((node) => node.day === visibleDay) ?? [];
  const routePoints = useMemo<RouteMapPoint[]>(() => dayNodes.flatMap((node) => {
    const place = wishlist.find((item) => item.poi_id === node.poi_id);
    return place ? [{ id: node.id, name: node.name, detail: `${node.start_time.slice(0, 5)}–${node.end_time.slice(0, 5)}`, latitude: place.latitude, longitude: place.longitude }] : [];
  }), [dayNodes, wishlist]);

  return <main className="shell plan-page">
    <nav className="nav"><Link className="brand" href={`/trips/${tripId}`}>TravelPilot</Link><span className="status">计划</span></nav>
    <section className="hero"><p className="eyebrow">YOUR ITINERARY</p><h1>把想去的地方，排成一天</h1><p className="lede">按天查看真实地点、顺序和天气。计划生成后，你可以继续和旅行顾问一起调整。</p>
      <div className="actions"><button className="primary" disabled={busy} onClick={() => void generate()}>{busy ? "正在规划…" : latest ? "重新生成计划" : "生成我的第一版计划"}</button><Link className="secondary" href={`/advisor?tripId=${tripId}`}>和旅行顾问一起调整</Link></div>
    </section>
    {error ? <p role="alert">{error}</p> : null}
    <WeatherForecast tripId={tripId} />
    {latest ? <>
      <div className="plan-version"><span><strong>计划 v{latest.version}</strong><small> · {latest.status}</small></span><Link href={`/trips/${tripId}/events`}>模拟变化并查看调整 →</Link></div>
      <section className="plan-day-switcher" aria-label="选择行程日期">{days.map((day, index) => <button key={day} type="button" className={day === visibleDay ? "is-selected" : ""} onClick={() => { setActiveDay(day); setSelectedStop(""); }}><small>第 {index + 1} 天</small><strong>{day}</strong><span>{latest.nodes.filter((node) => node.day === day).length} 个安排</span></button>)}</section>
      <div className="plan-day-layout">
        <section className="plan-timeline"><div className="plan-section-title"><div><p className="eyebrow">DAY PLAN</p><h2>今日安排</h2></div><span>{dayNodes.length} 个地点</span></div>
          {dayNodes.length ? <div className="plan-stops">{dayNodes.map((node, index) => <button type="button" key={node.id} className={`plan-stop ${selectedStop === node.id ? "is-selected" : ""}`} onClick={() => setSelectedStop(node.id)}>
            <span className="plan-stop-time">{node.start_time.slice(0, 5)}<small>{node.end_time.slice(0, 5)}</small></span><span className="plan-stop-rail"><i>{index + 1}</i></span><span className="plan-stop-content"><strong>{node.name}</strong><small>{node.status} · 优先级 {node.priority}</small>{node.note && <p>{node.note}</p>}</span><span className="plan-stop-open">地图 ↗</span>
          </button>)}</div> : <p className="status">这一天还没有安排地点。</p>}
        </section>
        <div className="plan-map-column"><RouteMap title="今天的地点地图" points={routePoints} selectedPointId={selectedStop} disclaimer="点击左侧行程可选中地点；地图上的编号对应游览顺序。" onSelect={setSelectedStop} />
          {routePoints.length < dayNodes.length && <p className="plan-map-note">有 {dayNodes.length - routePoints.length} 个地点暂缺坐标，无法显示在地图上。可在地点收藏中补充坐标。</p>}
        </div>
      </div>
      {latest.warnings.length > 0 && <section className="plan-warnings"><h2>出发前留意</h2>{latest.warnings.map((warning, index) => <p role="status" key={index}>✳ {warning}</p>)}</section>}
      <details className="plan-history"><summary>查看历史计划版本</summary>{plans.map((plan) => <p key={plan.id}>v{plan.version} · {plan.created_at}</p>)}</details>
    </> : <section className="plan-empty layer"><span aria-hidden="true">✦</span><h2>你的第一版行程还没开始</h2><p>先添加想去的地点，系统会按旅行日期和地点信息安排计划。</p><div className="actions"><Link className="primary" href={`/places?tripId=${tripId}`}>去添加想去的地方</Link><Link className="secondary" href={`/advisor?tripId=${tripId}`}>先咨询旅行顾问</Link></div></section>}
  </main>;
}
