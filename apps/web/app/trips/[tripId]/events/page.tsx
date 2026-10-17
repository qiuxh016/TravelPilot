"use client";

import Link from "next/link";
import { FormEvent, useEffect, useMemo, useState } from "react";
import { useParams } from "next/navigation";
import { createEvent, getTrip, listPlans, listWishlist, replan, type Plan, type ReplanResult, type Trip, type WishlistItem } from "../../../../lib/api";
import { RouteMap } from "../../../../components/route-map";

const eventTypes = [
  { value: "WEATHER_CHANGE", label: "天气变化", mark: "☂", note: "雨雪或高温，让户外安排换个顺序。" },
  { value: "USER_FATIGUE", label: "有点累了", mark: "〰", note: "放慢节奏，减少后续活动。" },
  { value: "POI_CANCELLED", label: "地点去不了", mark: "×", note: "移除一个地点，再整理当天安排。" },
  { value: "TIME_DELAY", label: "行程延误", mark: "◷", note: "告诉我们晚了多久，重新计算后续时间。" },
  { value: "USER_ADD", label: "临时想去这里", mark: "+", note: "把新地点加入当天计划。" },
  { value: "RESERVATION_CHANGE", label: "预约有变", mark: "↻", note: "根据预约变化调整计划。" },
];

export default function EventsPage() {
  const tripId = useParams<{ tripId: string }>().tripId;
  const [trip, setTrip] = useState<Trip | null>(null);
  const [plan, setPlan] = useState<Plan | null>(null);
  const [wishlist, setWishlist] = useState<WishlistItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [eventType, setEventType] = useState("WEATHER_CHANGE");
  const [effectiveAt, setEffectiveAt] = useState("");
  const [poiId, setPoiId] = useState("");
  const [delay, setDelay] = useState("30");
  const [result, setResult] = useState<ReplanResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    void Promise.all([getTrip(tripId), listPlans(tripId), listWishlist(tripId)]).then(([tripRow, plans, wishlistRows]) => {
      if (!active) return;
      setTrip(tripRow); setPlan(plans[0] ?? null); setWishlist(wishlistRows);
      if (plans[0]?.nodes[0]) setEffectiveAt(`${plans[0].nodes[0].day}T${plans[0].nodes[0].start_time.slice(0, 5)}`);
      else setEffectiveAt(`${tripRow.start_date}T09:00`);
    }).catch((cause) => { if (active) setError(cause instanceof Error ? cause.message : "这段旅行暂时无法加载"); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [tripId]);

  const selectedEvent = eventTypes.find((item) => item.value === eventType) ?? eventTypes[0];
  const points = useMemo(() => {
    const places = new Map(wishlist.map((item) => [item.poi_id, item]));
    return (plan?.nodes ?? []).flatMap((node) => {
      const place = places.get(node.poi_id);
      return place ? [{ id: node.id, name: node.name, detail: `${node.day.slice(5)} · ${node.start_time.slice(0, 5)}`, latitude: place.latitude, longitude: place.longitude }] : [];
    });
  }, [plan, wishlist]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError(""); setResult(null);
    try {
      const payload: Record<string, unknown> = {};
      if (["POI_CANCELLED", "USER_ADD", "RESERVATION_CHANGE"].includes(eventType) && poiId) payload.poi_id = poiId;
      if (eventType === "TIME_DELAY") payload.delay_min = Number(delay);
      const created = await createEvent(tripId, eventType, `${effectiveAt}:00+08:00`, payload);
      const next = await replan(tripId, created.id);
      setResult(next); setPlan(next.plan);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "重规划失败，请稍后再试"); }
    finally { setBusy(false); }
  }

  if (loading) return <main className="shell"><p className="page-loading">正在读取这段旅程…</p></main>;

  return <main className="shell events-page">
    <header className="events-heading">
      <div><Link className="context-backlink" href={`/trips/${tripId}`}>← {trip?.destination ?? "返回旅行"}</Link><h1>旅途中有变化，<br />计划也可以跟着变。</h1><p>告诉我们刚刚发生了什么。我们会保留旧版本，并从这个时间点之后重新安排。</p></div>
      <div className="events-heading-stamp" aria-hidden="true"><span>{plan ? "DAY" : "START"}</span><strong>{plan?.nodes[0]?.day.slice(-2) ?? trip?.start_date.slice(-2) ?? "01"}</strong><small>{plan ? "旅途进行中" : "旅行日期"}</small></div>
    </header>

    {error && <p className="events-alert" role="alert">{error}</p>}

    <div className="events-workspace">
      <section className="events-form-section">
        <div className="events-section-title"><span>01</span><div><h2>发生了什么？</h2><p>选一个最接近的情况，再补充时间或地点。</p></div></div>
        <div className="events-select-wrap"><label htmlFor="event-type">事件类型</label><select id="event-type" value={eventType} onChange={(event) => setEventType(event.target.value)}>{eventTypes.map((item) => <option value={item.value} key={item.value}>{item.label}</option>)}</select></div>
        <div className="event-selected-note"><span aria-hidden="true">{selectedEvent.mark}</span><p>{selectedEvent.note}</p></div>
        <form className="events-form" onSubmit={submit}>
          <label>从什么时候开始生效？<input aria-label="生效时间" required type="datetime-local" min={trip?.start_date ? `${trip.start_date}T00:00` : undefined} max={trip?.end_date ? `${trip.end_date}T23:59` : undefined} value={effectiveAt} onChange={(event) => setEffectiveAt(event.target.value)} /></label>
          {eventType === "POI_CANCELLED" || eventType === "USER_ADD" || eventType === "RESERVATION_CHANGE" ? <label>涉及地点<select aria-label="POI ID" required value={poiId} onChange={(event) => setPoiId(event.target.value)}><option value="">选择行程中的地点</option>{wishlist.map((item) => <option value={item.poi_id} key={item.id}>{item.name}</option>)}</select></label> : null}
          {eventType === "TIME_DELAY" ? <label>大约延误了多少分钟？<input aria-label="延误分钟" type="number" min="1" max="180" value={delay} onChange={(event) => setDelay(event.target.value)} /></label> : null}
          <button className="primary events-submit" disabled={busy || !plan}>{busy ? "正在调整安排…" : "看看怎么调整"}<span aria-hidden="true">→</span></button>
          {!plan && <small className="events-no-plan">先生成一版行程，再来处理旅途中的变化。</small>}
        </form>
      </section>

      <aside className="events-context-section">
        <div className="events-section-title"><span>02</span><div><h2>{result ? "新的安排" : "原计划在这里"}</h2><p>{result ? `v${result.plan.version} · ${result.plan.status}` : plan ? `当前版本 v${plan.version} · ${plan.nodes.length} 个安排` : "还没有已生成的安排"}</p></div></div>
        {result ? <div className="events-diff-list">{result.plan.warnings.map((warning, index) => <p className="events-warning" key={index}>注意：{warning}</p>)}{result.diff.length ? result.diff.map((change) => <article className={`events-diff-row op-${change.operation.toLowerCase()}`} key={`${change.stable_key}-${change.operation}`}><span>{change.operation === "KEEP" ? "保留" : change.operation === "REMOVE" ? "移除" : change.operation === "ADD" ? "新增" : "调整"}</span><div><strong>{String(change.after?.name ?? change.before?.name ?? change.stable_key)}</strong><small>{String(change.before?.start_time ?? "—")} → {String(change.after?.start_time ?? "—")} · 调整成本 {change.change_cost}</small><small>{change.reason_code}</small></div></article>) : <p className="events-no-change">这次变化没有需要移动的安排。原计划仍然保留。</p>}</div> : <>
          <RouteMap title={plan ? "计划地点" : "还没有路线"} points={points} disclaimer="标记来自当前计划中的地点；此处仅展示位置分布。" />
          {plan?.nodes.length ? <ol className="events-plan-rail">{plan.nodes.slice(0, 4).map((node, index) => <li key={node.id}><span className="events-plan-number">{String(index + 1).padStart(2, "0")}</span><span><strong>{node.name}</strong><small>{node.day.slice(5)} · {node.start_time.slice(0, 5)}</small></span></li>)}</ol> : <div className="events-empty-route"><span aria-hidden="true">↝</span><p>行程生成后，这里会显示当前路线与地点。</p><Link href={`/trips/${tripId}/plan`}>去看看行程计划 →</Link></div>}
        </>}
        {result && <Link className="events-view-plan" href={`/trips/${tripId}/plan`}>查看完整计划与版本记录 <span aria-hidden="true">↗</span></Link>}
      </aside>
    </div>
  </main>;
}
