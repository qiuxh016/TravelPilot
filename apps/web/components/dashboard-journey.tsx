"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { listPlans, listWishlist, type Plan, type Trip, type WishlistItem } from "../lib/api";
import { RouteMap, type RouteMapPoint } from "./route-map";

export function DashboardJourney({ trips }: { trips: Trip[] }) {
  const [tripId, setTripId] = useState(trips[0]?.id ?? "");
  const [wishlist, setWishlist] = useState<WishlistItem[]>([]);
  const [plans, setPlans] = useState<Plan[]>([]);
  const [activeDay, setActiveDay] = useState("");
  const [selectedStop, setSelectedStop] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const trip = trips.find((item) => item.id === tripId) ?? trips[0];

  useEffect(() => {
    if (!trip?.id) return;
    let active = true;
    setLoading(true);
    setError("");
    void Promise.all([listWishlist(trip.id), listPlans(trip.id)])
      .then(([nextWishlist, nextPlans]) => {
        if (!active) return;
        setWishlist(nextWishlist);
        setPlans(nextPlans);
        setActiveDay(nextPlans[0]?.nodes[0]?.day ?? "");
      })
      .catch((cause) => { if (active) setError(cause instanceof Error ? cause.message : "行程暂时无法读取"); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [trip?.id]);

  const plan = plans[0];
  const days = plan ? [...new Set(plan.nodes.map((node) => node.day))] : [];
  const day = days.includes(activeDay) ? activeDay : days[0] ?? "";
  const dayNodes = plan?.nodes.filter((node) => node.day === day) ?? [];
  const points = useMemo<RouteMapPoint[]>(() => {
    if (dayNodes.length) return dayNodes.flatMap((node) => {
      const place = wishlist.find((item) => item.poi_id === node.poi_id);
      return place ? [{ id: node.id, name: node.name, detail: `${node.start_time.slice(0, 5)}–${node.end_time.slice(0, 5)}`, latitude: place.latitude, longitude: place.longitude }] : [];
    });
    return wishlist.map((item) => ({ id: item.id, name: item.name, detail: item.address, latitude: item.latitude, longitude: item.longitude }));
  }, [dayNodes, wishlist]);

  return <section className="dashboard-journey" aria-label="最近的旅行">
    <header className="dashboard-journey-head">
      <div><span className="dashboard-kicker">最近的旅程</span><h2>{trip.destination}</h2><p>{trip.start_date} — {trip.end_date}</p></div>
      <div className="dashboard-journey-tools">
        {trips.length > 1 && <label className="sr-only" htmlFor="dashboard-trip-select">切换旅行</label>}
        {trips.length > 1 && <select id="dashboard-trip-select" value={tripId} onChange={(event) => { setTripId(event.target.value); setSelectedStop(""); }} aria-label="切换旅行">{trips.map((item) => <option key={item.id} value={item.id}>{item.destination} · {item.start_date}</option>)}</select>}
        <Link href={`/trips/${trip.id}`}>打开旅行 <span aria-hidden="true">↗</span></Link>
      </div>
    </header>
    <div className="dashboard-journey-body">
      <div className="dashboard-map-area">
        <RouteMap title={plan ? "这一天的路线" : "收藏地点地图"} points={points} selectedPointId={selectedStop} disclaimer="地图来自高德地图；连线表示游览顺序。" onSelect={setSelectedStop} />
      </div>
      <aside className="dashboard-agenda">
        <div className="dashboard-agenda-head">
          <div><span className="dashboard-kicker">{plan ? "行程预览" : "旅行收藏"}</span><h3>{plan ? "出发前的安排" : "想去的地方"}</h3></div>
          {plan && <Link href={`/trips/${trip.id}/plan`}>完整计划 →</Link>}
        </div>
        {loading ? <p className="dashboard-agenda-empty">正在整理这段旅程…</p> : error ? <p className="dashboard-agenda-empty" role="alert">{error}</p> : plan?.nodes.length ? <>
          <div className="dashboard-day-tabs" role="tablist" aria-label="选择计划日期">{days.map((item, index) => <button role="tab" aria-selected={day === item} className={day === item ? "is-selected" : ""} type="button" key={item} onClick={() => { setActiveDay(item); setSelectedStop(""); }}><small>DAY 0{index + 1}</small><strong>{item.slice(5)}</strong></button>)}</div>
          <div className="dashboard-agenda-list">{dayNodes.map((node, index) => <button type="button" key={node.id} className={`dashboard-agenda-row ${selectedStop === node.id ? "is-selected" : ""}`} onClick={() => setSelectedStop(node.id)}>
            <span className="dashboard-agenda-time">{node.start_time.slice(0, 5)}</span><span className="dashboard-agenda-marker">{index + 1}</span><span className="dashboard-agenda-copy"><strong>{node.name}</strong><small>{node.end_time.slice(0, 5)} · {node.status}</small></span><span className="dashboard-agenda-chevron">↗</span>
          </button>)}</div>
        </> : wishlist.length ? <div className="dashboard-agenda-list">{wishlist.slice(0, 5).map((item, index) => <button type="button" key={item.id} className={`dashboard-agenda-row ${selectedStop === item.id ? "is-selected" : ""}`} onClick={() => setSelectedStop(item.id)}><span className="dashboard-agenda-time">0{index + 1}</span><span className="dashboard-agenda-marker">{index + 1}</span><span className="dashboard-agenda-copy"><strong>{item.name}</strong><small>{item.address || "已收藏地点"}</small></span><span className="dashboard-agenda-chevron">↗</span></button>)}</div> : <div className="dashboard-agenda-empty"><p>这趟旅行还没有收藏地点。</p><Link className="text-link" href={`/places?tripId=${trip.id}`}>先去找几个想去的地方 →</Link></div>}
        {!plan && wishlist.length > 0 && <Link className="dashboard-plan-link" href={`/trips/${trip.id}/plan`}>把收藏排成每日行程 <span aria-hidden="true">→</span></Link>}
      </aside>
    </div>
  </section>;
}
