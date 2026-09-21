"use client";

import Link from "next/link";
import { FormEvent, useState } from "react";
import { useParams } from "next/navigation";
import { createEvent, replan, type ReplanResult } from "../../../../lib/api";

const eventTypes = [
  ["WEATHER_CHANGE", "天气变化"], ["USER_FATIGUE", "用户疲劳"],
  ["POI_CANCELLED", "地点取消"], ["TIME_DELAY", "时间延误"],
  ["USER_ADD", "新增地点"], ["RESERVATION_CHANGE", "预约变化"],
];

export default function EventsPage() {
  const tripId = useParams<{ tripId: string }>().tripId;
  const [eventType, setEventType] = useState("WEATHER_CHANGE");
  const [effectiveAt, setEffectiveAt] = useState("");
  const [poiId, setPoiId] = useState("");
  const [delay, setDelay] = useState("30");
  const [result, setResult] = useState<ReplanResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError("");
    try {
      const payload: Record<string, unknown> = {};
      if (poiId) payload.poi_id = poiId;
      if (eventType === "TIME_DELAY") payload.delay_min = Number(delay);
      const created = await createEvent(tripId, eventType, `${effectiveAt}:00+08:00`, payload);
      setResult(await replan(tripId, created.id));
    } catch (err) { setError(err instanceof Error ? err.message : "重规划失败"); }
    finally { setBusy(false); }
  }
  return <main className="shell">
    <nav className="nav"><Link className="brand" href={`/trips/${tripId}/plan`}>TravelPilot</Link><span className="status">动态重规划</span></nav>
    <section className="hero"><p className="eyebrow">Replan</p><h1>现实发生变化</h1>
      <p className="lede">选择事件与时间，系统只调整当天事件之后的非固定节点，并保留旧计划版本。</p></section>
    <form className="trip-form" onSubmit={submit}>
      <label>事件类型<select value={eventType} onChange={(event) => setEventType(event.target.value)}>{eventTypes.map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label>
      <label>生效时间<input required type="datetime-local" value={effectiveAt} onChange={(event) => setEffectiveAt(event.target.value)} /></label>
      {eventType === "POI_CANCELLED" || eventType === "USER_ADD" || eventType === "RESERVATION_CHANGE" ?
        <label>POI ID<input required value={poiId} onChange={(event) => setPoiId(event.target.value)} /></label> : null}
      {eventType === "TIME_DELAY" ? <label>延误分钟<input type="number" min="1" max="180" value={delay} onChange={(event) => setDelay(event.target.value)} /></label> : null}
      <button className="primary" disabled={busy}>触发重规划</button>
    </form>
    {error ? <p role="alert">{error}</p> : null}
    {result ? <section><h2>Plan v{result.plan.version} · {result.plan.status}</h2>
      {result.plan.warnings.map((warning, index) => <p key={index} role="status">⚠ {warning}</p>)}
      <div className="wishlist-list">{result.diff.map((change) => <article className="layer" key={change.stable_key}>
        <span className="index">{change.operation} · 成本 {change.change_cost}</span>
        <h2>{String(change.after?.name ?? change.before?.name ?? change.stable_key)}</h2>
        <p>{String(change.before?.start_time ?? "—")} → {String(change.after?.start_time ?? "—")}</p>
        <p>{change.reason_code}</p>
      </article>)}</div>
    </section> : null}
  </main>;
}
