"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";
import {
  confirmReplan, createEvent, getTrip, listPlans, listWishlist, replan,
  type Plan, type PlanDiff, type ReplanResult, type Trip, type WishlistItem,
} from "../lib/api";

const EVENT_TYPES = [
  ["WEATHER_CHANGE", "天气变化"], ["USER_FATIGUE", "需要放慢节奏"],
  ["POI_CANCELLED", "地点去不了"], ["TIME_DELAY", "行程延误"],
  ["USER_ADD", "临时想去这里"], ["RESERVATION_CHANGE", "预约有变"],
] as const;
const WEATHER = [["RAIN", "下雨"], ["WIND", "大风"], ["HEAT", "高温"], ["COLD", "降温"], ["SNOW", "下雪"], ["STORM", "暴风雨"]] as const;

type Props = {
  tripId: string;
  initialEventType: string;
  prefill?: { condition?: string; delay_min?: number };
  onClose: () => void;
  onApplied: () => void;
};

function diffLabel(diff: PlanDiff) {
  return diff.operation === "KEEP" ? "保留" : diff.operation === "REMOVE" ? "移除" : diff.operation === "ADD" ? "新增" : "调整";
}

export function AdvisorWhatIf({ tripId, initialEventType, prefill, onClose, onApplied }: Props) {
  const safeInitialType = EVENT_TYPES.some(([value]) => value === initialEventType) ? initialEventType : "WEATHER_CHANGE";
  const [trip, setTrip] = useState<Trip | null>(null);
  const [plan, setPlan] = useState<Plan | null>(null);
  const [wishlist, setWishlist] = useState<WishlistItem[]>([]);
  const [eventType, setEventType] = useState(safeInitialType);
  const [effectiveAt, setEffectiveAt] = useState("");
  const [condition, setCondition] = useState(prefill?.condition ?? "");
  const [poiId, setPoiId] = useState("");
  const [delay, setDelay] = useState(prefill?.delay_min ? String(prefill.delay_min) : "");
  const [opens, setOpens] = useState("");
  const [closes, setCloses] = useState("");
  const [reservationStart, setReservationStart] = useState("");
  const [reservationEnd, setReservationEnd] = useState("");
  const [preview, setPreview] = useState<ReplanResult | null>(null);
  const [pendingEvent, setPendingEvent] = useState<{ signature: string; id: string } | null>(null);
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let current = true;
    void Promise.all([getTrip(tripId), listPlans(tripId), listWishlist(tripId)]).then(([tripRow, plans, places]) => {
      if (!current) return;
      setTrip(tripRow); setPlan(plans[0] ?? null); setWishlist(places);
      // The event's effective time changes the route from that point onward; do not guess it.
    }).catch((cause) => { if (current) setError(cause instanceof Error ? cause.message : "无法读取当前行程"); });
    return () => { current = false; };
  }, [tripId]);

  const selectedPlace = useMemo(() => wishlist.find((place) => place.poi_id === poiId), [poiId, wishlist]);
  const days = useMemo(() => {
    const byDay = new Map<string, Plan["nodes"]>();
    for (const node of preview?.nodes ?? []) byDay.set(node.day, [...(byDay.get(node.day) ?? []), node]);
    return [...byDay.entries()].sort(([a], [b]) => a.localeCompare(b));
  }, [preview]);

  async function makePreview(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError(""); setPreview(null); setSaved(false);
    try {
      const payload: Record<string, unknown> = {};
      if (eventType === "WEATHER_CHANGE") payload.condition = condition;
      if (["POI_CANCELLED", "USER_ADD", "RESERVATION_CHANGE"].includes(eventType)) payload.poi_id = poiId;
      if (eventType === "USER_ADD") payload.opening_hours = { open: opens, close: closes };
      if (eventType === "TIME_DELAY") payload.delay_min = Number(delay);
      if (eventType === "RESERVATION_CHANGE") { payload.start_time = reservationStart; payload.end_time = reservationEnd; }
      // TravelPilot stores trip dates in China Standard Time, matching the existing events form.
      const effectiveIso = `${effectiveAt}:00+08:00`;
      const signature = JSON.stringify({ eventType, effectiveIso, payload });
      const eventId = pendingEvent?.signature === signature
        ? pendingEvent.id
        : (await createEvent(tripId, eventType, effectiveIso, payload)).id;
      setPendingEvent({ signature, id: eventId });
      setPreview(await replan(tripId, eventId));
    } catch (cause) { setError(cause instanceof Error ? cause.message : "调整预览生成失败，请检查信息后重试"); }
    finally { setBusy(false); }
  }

  async function applyPreview() {
    if (!preview) return;
    setBusy(true); setError("");
    try { await confirmReplan(tripId, preview); setSaved(true); onApplied(); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "保存失败；正式行程未确认更新"); }
    finally { setBusy(false); }
  }

  return <div className="advisor-whatif-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !busy) onClose(); }}>
    <section className="advisor-whatif" role="dialog" aria-modal="true" aria-labelledby="advisor-whatif-title">
      <header><div><small>旅途中调整</small><h2 id="advisor-whatif-title">先试算，再决定是否采用</h2><p>{trip?.destination ?? "当前旅行"}{trip ? ` · ${trip.start_date} 至 ${trip.end_date}` : ""}</p></div><button type="button" aria-label="关闭调整窗口" disabled={busy} onClick={onClose}>×</button></header>
      {error && <p className="advisor-whatif-error" role="alert">{error}</p>}
      {!preview ? <form onSubmit={(event) => void makePreview(event)}>
        <p className="advisor-whatif-note">提交后会记录这次变化用于试算；放弃预览不会修改正式行程，只有确认采用才会保存新版本。</p>
        <label>发生了什么？<select value={eventType} onChange={(event) => setEventType(event.target.value)}>{EVENT_TYPES.map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label>
        <label>从什么时候开始生效？<input aria-label="生效时间" required type="datetime-local" min={trip ? `${trip.start_date}T00:00` : undefined} max={trip ? `${trip.end_date}T23:59` : undefined} value={effectiveAt} onChange={(event) => setEffectiveAt(event.target.value)} /></label>
        {eventType === "WEATHER_CHANGE" && <label>实际天气<select aria-label="天气情况" required value={condition} onChange={(event) => setCondition(event.target.value)}><option value="">请选择</option>{WEATHER.map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label>}
        {["POI_CANCELLED", "USER_ADD", "RESERVATION_CHANGE"].includes(eventType) && <label>涉及地点<select required value={poiId} onChange={(event) => setPoiId(event.target.value)}><option value="">选择行程地点</option>{wishlist.map((place) => <option value={place.poi_id} key={place.id}>{place.name}</option>)}</select></label>}
        {eventType === "TIME_DELAY" && <label>延误分钟数<input required type="number" min="1" max="180" value={delay} onChange={(event) => setDelay(event.target.value)} /></label>}
        {eventType === "USER_ADD" && <><p className="advisor-whatif-note">新地点：{selectedPlace?.name ?? "请先选择地点"}。请填写你已核实的营业时间，系统不会猜测。</p><div className="advisor-whatif-pair"><label>营业开始<input required type="time" value={opens} onChange={(event) => setOpens(event.target.value)} /></label><label>营业结束<input required type="time" value={closes} onChange={(event) => setCloses(event.target.value)} /></label></div></>}
        {eventType === "RESERVATION_CHANGE" && <div className="advisor-whatif-pair"><label>预约开始<input required type="time" value={reservationStart} onChange={(event) => setReservationStart(event.target.value)} /></label><label>预约结束<input required type="time" value={reservationEnd} onChange={(event) => setReservationEnd(event.target.value)} /></label></div>}
        {!plan && <p className="advisor-whatif-note">这段旅行还没有正式行程，无法进行途中调整。</p>}
        <footer><button type="button" className="secondary" disabled={busy} onClick={onClose}>取消</button><button type="submit" className="primary" disabled={busy || !plan}>{busy ? "正在试算…" : "生成预览"}</button></footer>
      </form> : <div className="advisor-whatif-preview" aria-live="polite">
        <div className="advisor-whatif-status"><strong>{saved ? "调整已保存" : preview.proposed_plan_status === "READY" ? "可供确认" : "需要处理后再保存"}</strong><span>基于正式计划 v{preview.based_on_plan_version} · 预览 v{preview.proposed_version}</span><small>未确认前，正式行程保持不变。</small></div>
        {preview.warnings.map((warning, index) => <p className="advisor-whatif-note" key={index}>{warning}</p>)}
        {days.map(([day, nodes]) => <section className="advisor-whatif-day" key={day}><h3>{day}</h3><ol>{nodes.map((node) => <li key={node.id}><time>{node.start_time.slice(0, 5)}–{node.end_time.slice(0, 5)}</time><strong>{node.name}</strong></li>)}</ol></section>)}
        <section className="advisor-whatif-changes"><h3>本次变化</h3>{preview.diff.length ? preview.diff.map((change) => <p key={`${change.stable_key}:${change.operation}`}><b>{diffLabel(change)}</b> {String(change.after?.name ?? change.before?.name ?? change.stable_key)} <small>{String(change.before?.start_time ?? "")}{change.before?.start_time ? " → " : ""}{String(change.after?.start_time ?? "")}</small></p>) : <p>计划内容没有变化。</p>}</section>
        <footer>{saved ? <button type="button" className="primary" onClick={onClose}>完成</button> : <><button type="button" className="secondary" disabled={busy} onClick={() => setPreview(null)}>返回修改</button><button type="button" className="secondary" disabled={busy} onClick={onClose}>放弃预览</button><button type="button" className="primary" disabled={busy || preview.proposed_plan_status !== "READY"} onClick={() => void applyPreview()}>{busy ? "正在保存…" : "确认并更新正式行程"}</button></>}</footer>
      </div>}
    </section>
  </div>;
}
