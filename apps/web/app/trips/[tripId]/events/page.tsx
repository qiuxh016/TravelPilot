"use client";

import Link from "next/link";
import { FormEvent, useEffect, useMemo, useState } from "react";
import { useParams } from "next/navigation";
import { confirmReplan, createEvent, getTrip, listPlans, listWishlist, replan, type Plan, type ReplanResult, type Trip, type WishlistItem } from "../../../../lib/api";
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
  const [weatherCondition, setWeatherCondition] = useState("");
  const [effectiveAt, setEffectiveAt] = useState("");
  const [poiId, setPoiId] = useState("");
  const [delay, setDelay] = useState("");
  const [placeOpens, setPlaceOpens] = useState("");
  const [placeCloses, setPlaceCloses] = useState("");
  const [reservationStart, setReservationStart] = useState("");
  const [reservationEnd, setReservationEnd] = useState("");
  const [result, setResult] = useState<ReplanResult | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    void Promise.all([getTrip(tripId), listPlans(tripId), listWishlist(tripId)]).then(([tripRow, plans, wishlistRows]) => {
      if (!active) return;
      setTrip(tripRow); setPlan(plans[0] ?? null); setWishlist(wishlistRows);
      const requestedEventType = new URLSearchParams(window.location.search).get("event_type");
      if (eventTypes.some((item) => item.value === requestedEventType)) {
        setEventType(requestedEventType as string);
      }
      const params = new URLSearchParams(window.location.search);
      const condition = params.get("condition");
      if (["RAIN", "WIND", "HEAT", "COLD", "SNOW", "STORM"].includes(condition ?? "")) {
        setWeatherCondition(condition ?? "");
      }
      const delayMin = Number(params.get("delay_min"));
      if (Number.isInteger(delayMin) && delayMin >= 1 && delayMin <= 180) {
        setDelay(String(delayMin));
      }
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
    event.preventDefault(); setBusy(true); setError(""); setResult(null); setConfirmed(false);
    try {
      const payload: Record<string, unknown> = {};
      if (eventType === "WEATHER_CHANGE") payload.condition = weatherCondition;
      if (["POI_CANCELLED", "USER_ADD", "RESERVATION_CHANGE"].includes(eventType) && poiId) payload.poi_id = poiId;
      if (eventType === "USER_ADD") payload.opening_hours = { open: placeOpens, close: placeCloses };
      if (eventType === "TIME_DELAY") payload.delay_min = Number(delay);
      if (eventType === "RESERVATION_CHANGE") {
        payload.start_time = reservationStart;
        payload.end_time = reservationEnd;
      }
      const created = await createEvent(tripId, eventType, `${effectiveAt}:00+08:00`, payload);
      const next = await replan(tripId, created.id);
      setResult(next);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "重规划失败，请稍后再试"); }
    finally { setBusy(false); }
  }

  async function confirmPreview() {
    if (!result) return;
    setBusy(true); setError("");
    try {
      const saved = await confirmReplan(tripId, result);
      setPlan(saved.plan); setConfirmed(true);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "保存调整失败，请重新生成预览"); }
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
          {eventType === "WEATHER_CHANGE" ? <label>天气情况<select aria-label="天气情况" required value={weatherCondition} onChange={(event) => setWeatherCondition(event.target.value)}><option value="">请选择实际情况</option><option value="RAIN">下雨</option><option value="WIND">大风</option><option value="HEAT">高温</option><option value="COLD">降温</option><option value="SNOW">下雪</option><option value="STORM">暴风雨</option></select></label> : null}
          {eventType === "POI_CANCELLED" || eventType === "USER_ADD" || eventType === "RESERVATION_CHANGE" ? <label>涉及地点<select aria-label="POI ID" required value={poiId} onChange={(event) => setPoiId(event.target.value)}><option value="">选择行程中的地点</option>{wishlist.map((item) => <option value={item.poi_id} key={item.id}>{item.name}</option>)}</select></label> : null}
          {eventType === "USER_ADD" ? <div className="events-time-pair"><label>你确认的营业开始时间<input aria-label="地点营业开始时间" required type="time" value={placeOpens} onChange={(event) => setPlaceOpens(event.target.value)} /></label><label>你确认的营业结束时间<input aria-label="地点营业结束时间" required type="time" value={placeCloses} onChange={(event) => setPlaceCloses(event.target.value)} /></label><small>用于判断能否插入计划；未核实的营业时间不要猜填。</small></div> : null}
          {eventType === "TIME_DELAY" ? <label>大约延误了多少分钟？<input aria-label="延误分钟" required type="number" min="1" max="180" placeholder="请填写 1–180 分钟" value={delay} onChange={(event) => setDelay(event.target.value)} /></label> : null}
          {eventType === "RESERVATION_CHANGE" ? <div className="events-time-pair"><label>新的预约开始时间<input aria-label="预约开始时间" required type="time" value={reservationStart} onChange={(event) => setReservationStart(event.target.value)} /></label><label>新的预约结束时间<input aria-label="预约结束时间" required type="time" value={reservationEnd} onChange={(event) => setReservationEnd(event.target.value)} /></label></div> : null}
          <button className="primary events-submit" disabled={busy || !plan}>{busy ? "正在生成预览…" : "生成调整预览"}<span aria-hidden="true">→</span></button>
          {!plan && <small className="events-no-plan">先生成一版行程，再来处理旅途中的变化。</small>}
        </form>
      </section>

      <aside className="events-context-section">
        <div className="events-section-title"><span>02</span><div><h2>{result ? (confirmed ? "调整已保存" : "调整预览") : "原计划在这里"}</h2><p>{result ? `基于 v${result.based_on_plan_version} · 拟生成 v${result.proposed_version} · ${result.proposed_plan_status}` : plan ? `当前版本 v${plan.version} · ${plan.nodes.length} 个安排` : "还没有已生成的安排"}</p></div></div>
        {result ? <div className="events-diff-list">
          {!confirmed && <p className="events-warning">预览有效至 {new Date(result.expires_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}。确认前不会修改正式行程。</p>}
          {result.warnings.map((warning, index) => <p className="events-warning" key={index}>注意：{warning}</p>)}
          {result.diff.length ? result.diff.map((change) => <article className={`events-diff-row op-${change.operation.toLowerCase()}`} key={`${change.stable_key}-${change.operation}`}><span>{change.operation === "KEEP" ? "保留" : change.operation === "REMOVE" ? "移除" : change.operation === "ADD" ? "新增" : "调整"}</span><div><strong>{String(change.after?.name ?? change.before?.name ?? change.stable_key)}</strong><small>{String(change.before?.start_time ?? "—")} → {String(change.after?.start_time ?? "—")} · 调整成本 {change.change_cost}</small><small>{change.reason_code}</small></div></article>) : <p className="events-no-change">这次变化没有需要移动的安排。原计划仍然保留。</p>}
          {!confirmed && result.proposed_plan_status === "READY" && <button className="primary events-submit" type="button" disabled={busy} onClick={() => void confirmPreview()}>{busy ? "正在保存…" : "确认采用这版安排"}<span aria-hidden="true">✓</span></button>}
          {!confirmed && result.proposed_plan_status !== "READY" && <p className="events-warning">这份预览尚不能保存。请补全事件信息或处理冲突后重新生成。</p>}
        </div> : <>
          <RouteMap title={plan ? "计划地点" : "还没有路线"} points={points} disclaimer="标记来自当前计划中的地点；此处仅展示位置分布。" />
          {plan?.nodes.length ? <ol className="events-plan-rail">{plan.nodes.slice(0, 4).map((node, index) => <li key={node.id}><span className="events-plan-number">{String(index + 1).padStart(2, "0")}</span><span><strong>{node.name}</strong><small>{node.day.slice(5)} · {node.start_time.slice(0, 5)}</small></span></li>)}</ol> : <div className="events-empty-route"><span aria-hidden="true">↝</span><p>行程生成后，这里会显示当前路线与地点。</p><Link href={`/trips/${tripId}/plan`}>去看看行程计划 →</Link></div>}
        </>}
        {confirmed && <Link className="events-view-plan" href={`/trips/${tripId}/plan`}>查看完整计划与版本记录 <span aria-hidden="true">↗</span></Link>}
      </aside>
    </div>
  </main>;
}
