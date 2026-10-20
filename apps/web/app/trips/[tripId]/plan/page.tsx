"use client";

import Link from "next/link";
import type { FormEvent } from "react";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { ApiError, createPlanRun, deletePlanNode, getTrip, listPlans, listWishlist, updatePlanNode, type Plan, type PlanNode, type Trip, type WishlistItem } from "../../../../lib/api";
import { RouteMap, type RouteMapPoint } from "../../../../components/route-map";
import { WeatherForecast } from "../../../../components/weather-forecast";

export default function PlanPage() {
  const tripId = useParams<{ tripId: string }>().tripId;
  const router = useRouter();
  const [trip, setTrip] = useState<Trip | null>(null);
  const [plans, setPlans] = useState<Plan[]>([]);
  const [wishlist, setWishlist] = useState<WishlistItem[]>([]);
  const [activeDay, setActiveDay] = useState("");
  const [selectedStop, setSelectedStop] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [editingNode, setEditingNode] = useState<PlanNode | null>(null);
  const [nodeForm, setNodeForm] = useState({ day: "", start_time: "", end_time: "", priority: 3, status: "PLANNED", note: "" });
  const editDialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    if (editingNode) editDialog.current?.showModal();
    else if (editDialog.current?.open) editDialog.current.close();
  }, [editingNode]);
  useEffect(() => {
    let cancelled = false;
    setPlans([]);
    setTrip(null);
    setWishlist([]);
    setActiveDay("");
    setSelectedStop("");
    setError("");
    setLoading(true);

    void Promise.all([getTrip(tripId), listPlans(tripId), listWishlist(tripId)])
      .then(([nextTrip, nextPlans, nextWishlist]) => {
        if (cancelled) return;
        setTrip(nextTrip);
        setPlans(nextPlans);
        setWishlist(nextWishlist);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setPlans([]);
        setWishlist([]);
        if (err instanceof ApiError && err.status === 404) {
          router.replace("/trips");
          return;
        }
        setError(err instanceof Error ? err.message : "加载行程失败");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => { cancelled = true; };
  }, [tripId, router]);

  async function generate() {
    setBusy(true); setError("");
    try {
      await createPlanRun(tripId, plans[0]?.version ?? 0);
      const [nextPlans, nextWishlist] = await Promise.all([listPlans(tripId), listWishlist(tripId)]);
      setPlans(nextPlans); setWishlist(nextWishlist);
    } catch (err) { setError(err instanceof Error ? err.message : "规划失败"); }
    finally { setBusy(false); }
  }
  function openEdit(node: PlanNode) {
    setEditingNode(node);
    setNodeForm({ day: node.day, start_time: node.start_time.slice(0, 5), end_time: node.end_time.slice(0, 5), priority: node.priority, status: node.status, note: node.note ?? "" });
  }
  async function saveNode(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!latest || !editingNode) return;
    setBusy(true); setError("");
    try {
      await updatePlanNode(tripId, latest.id, editingNode.id, {
        ...nodeForm, note: nodeForm.note || null, expected_base_version: latest.version,
      });
      setPlans(await listPlans(tripId));
      setSelectedStop(""); setEditingNode(null);
    } catch (err) { setError(err instanceof Error ? err.message : "调整安排失败"); }
    finally { setBusy(false); }
  }
  async function removeNode(node: PlanNode) {
    if (!latest || !window.confirm(`从当前计划中移除“${node.name}”？历史版本仍会保留。`)) return;
    setBusy(true); setError("");
    try {
      await deletePlanNode(tripId, latest.id, node.id, latest.version);
      setPlans(await listPlans(tripId)); setSelectedStop("");
    } catch (err) { setError(err instanceof Error ? err.message : "移除安排失败"); }
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
      <div className="actions">{trip?.access_role !== "VIEWER" && <button className="primary" disabled={busy} onClick={() => void generate()}>{busy ? "正在规划…" : latest ? "重新生成计划" : "生成我的第一版计划"}</button>}<Link className="secondary" href={`/advisor?tripId=${tripId}`}>和旅行顾问一起调整</Link></div>
    </section>
    {error ? <p role="alert">{error}</p> : null}
    {loading ? <p className="status" role="status">正在加载这段旅行的安排…</p> : null}
    {!loading && !error ? <WeatherForecast tripId={tripId} /> : null}
    {!loading && !error && latest && latest.nodes.length > 0 ? <>
      <div className="plan-version"><span><strong>计划 v{latest.version}</strong><small> · {latest.status}</small></span><Link href={`/trips/${tripId}/events`}>模拟变化并查看调整 →</Link></div>
      <section className="plan-day-switcher" aria-label="选择行程日期">{days.map((day, index) => <button key={day} type="button" className={day === visibleDay ? "is-selected" : ""} onClick={() => { setActiveDay(day); setSelectedStop(""); }}><small>第 {index + 1} 天</small><strong>{day}</strong><span>{latest.nodes.filter((node) => node.day === day).length} 个安排</span></button>)}</section>
      {wishlist.length === 0 && latest.nodes.length > 0 && <p className="plan-sync-notice">想去的地点已清空，但当前计划是已保存的独立版本。现在会保留历史计划；移除概览中的地点后，新版本会同步移除对应安排。</p>}
      <div className="plan-day-layout">
        <section className="plan-timeline"><div className="plan-section-title"><div><p className="eyebrow">DAY PLAN</p><h2>今日安排</h2></div><span>{dayNodes.length} 个地点</span></div>
          {dayNodes.length ? <div className="plan-stops">{dayNodes.map((node, index) => <article key={node.id} className={`plan-stop ${selectedStop === node.id ? "is-selected" : ""}`}>
            <button type="button" className="plan-stop-main" onClick={() => setSelectedStop(node.id)}><span className="plan-stop-time">{node.start_time.slice(0, 5)}<small>{node.end_time.slice(0, 5)}</small></span><span className="plan-stop-rail"><i>{index + 1}</i></span><span className="plan-stop-content"><strong>{node.name}</strong><small>{node.status} · 优先级 {node.priority}</small>{node.note && <p>{node.note}</p>}</span><span className="plan-stop-open">地图 ↗</span></button>
            {trip?.access_role !== "VIEWER" && <span className="plan-stop-actions"><button type="button" disabled={busy} onClick={() => openEdit(node)}>调整</button><button type="button" disabled={busy} onClick={() => void removeNode(node)}>移除</button></span>}
          </article>)}</div> : <p className="status">这一天还没有安排地点。</p>}
        </section>
        <div className="plan-map-column"><RouteMap title="今天的地点地图" points={routePoints} selectedPointId={selectedStop} disclaimer="点击左侧行程可选中地点；地图上的编号对应游览顺序。" onSelect={setSelectedStop} showDrivingRoute={routePoints.length > 1} />
          {routePoints.length < dayNodes.length && <p className="plan-map-note">有 {dayNodes.length - routePoints.length} 个地点暂缺坐标，无法显示在地图上。可在地点收藏中补充坐标。</p>}
        </div>
      </div>
      {latest.warnings.length > 0 && <section className="plan-warnings"><h2>出发前留意</h2>{latest.warnings.map((warning, index) => <p role="status" key={index}>✳ {warning}</p>)}</section>}
      <details className="plan-history"><summary>查看历史计划版本</summary>{plans.map((plan) => <p key={plan.id}>v{plan.version} · {plan.created_at}</p>)}</details>
    </> : !loading && !error ? <section className="plan-empty layer"><span aria-hidden="true">✦</span><h2>{latest ? "当前计划还没有安排地点" : "你的第一版行程还没开始"}</h2><p>{latest ? "地点移除后，当前计划会生成一个保留历史的新版本。添加新的想去地点后，可以重新生成安排。" : "先添加想去的地点，系统会按旅行日期和地点信息安排计划。"}</p><div className="actions">{trip?.access_role !== "VIEWER" && <><Link className="primary" href={`/places?tripId=${tripId}`}>去添加想去的地方</Link>{wishlist.length > 0 && <button className="secondary" disabled={busy} onClick={() => void generate()}>{busy ? "正在规划…" : "重新生成计划"}</button>}</>}<Link className="secondary" href={`/advisor?tripId=${tripId}`}>先咨询旅行顾问</Link></div></section> : null}
    <dialog className="plan-edit-dialog" ref={editDialog} onClose={() => setEditingNode(null)} onClick={(event) => { if (event.target === event.currentTarget) setEditingNode(null); }} aria-labelledby="plan-edit-title">
      {editingNode && <form className="plan-edit-panel" onSubmit={(event) => void saveNode(event)}><header><div><p className="eyebrow">EDIT STOP</p><h2 id="plan-edit-title">调整行程安排</h2><p>{editingNode.name}</p></div><button className="moment-dialog-close" type="button" aria-label="关闭" onClick={() => setEditingNode(null)}>×</button></header>
        <label>日期<input type="date" required value={nodeForm.day} onChange={(event) => setNodeForm({ ...nodeForm, day: event.target.value })} /></label>
        <div className="plan-edit-times"><label>开始时间<input type="time" required value={nodeForm.start_time} onChange={(event) => setNodeForm({ ...nodeForm, start_time: event.target.value })} /></label><label>结束时间<input type="time" required value={nodeForm.end_time} onChange={(event) => setNodeForm({ ...nodeForm, end_time: event.target.value })} /></label></div>
        <div className="plan-edit-times"><label>优先级<select value={nodeForm.priority} onChange={(event) => setNodeForm({ ...nodeForm, priority: Number(event.target.value) })}>{[1,2,3,4,5].map((value) => <option key={value} value={value}>{value}</option>)}</select></label><label>状态<select value={nodeForm.status} onChange={(event) => setNodeForm({ ...nodeForm, status: event.target.value })}><option value="PLANNED">计划中</option><option value="FIXED">固定安排</option><option value="RESERVED">已预约</option><option value="COMPLETED">已完成</option><option value="CANCELLED">已取消</option></select></label></div>
        <label>备注<textarea rows={3} value={nodeForm.note} onChange={(event) => setNodeForm({ ...nodeForm, note: event.target.value })} /></label>
        <footer><button type="button" className="secondary" onClick={() => setEditingNode(null)}>取消</button><button type="submit" className="primary" disabled={busy}>{busy ? "保存中…" : "保存调整"}</button></footer>
      </form>}
    </dialog>
  </main>;
}
