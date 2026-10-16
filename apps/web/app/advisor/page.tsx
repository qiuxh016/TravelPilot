"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { RouteMap } from "../../components/route-map";
import {
  AdvisorSession, Trip, confirmAdvisorDraft, createAdvisorDraft, createAdvisorSession,
  listAdvisorSessions, listTrips, sendAdvisorMessage,
} from "../../lib/api";

type Mode = "discover" | "destination" | "existing";
const modes: { id: Mode; title: string; description: string }[] = [
  { id: "discover", title: "帮我选目的地", description: "先告诉顾问城市、日期和偏好，再由你决定目的地。" },
  { id: "destination", title: "我有目的地", description: "从想去的地点出发，逐步整理候选和行程。" },
  { id: "existing", title: "调整现有旅行", description: "读取你自己的旅行信息，草案确认后更新计划。" },
];

function dateAfter(days: number) {
  const date = new Date();
  date.setDate(date.getDate() + days);
  return date.toISOString().slice(0, 10);
}

export default function AdvisorPage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [mode, setMode] = useState<Mode>("destination");
  const [destination, setDestination] = useState("");
  const [startDate, setStartDate] = useState(dateAfter(14));
  const [endDate, setEndDate] = useState(dateAfter(16));
  const [budget, setBudget] = useState("");
  const [pace, setPace] = useState(3);
  const [preference, setPreference] = useState("");
  const [tripId, setTripId] = useState("");
  const [trips, setTrips] = useState<Trip[]>([]);
  const [sessions, setSessions] = useState<AdvisorSession[]>([]);
  const [active, setActive] = useState<AdvisorSession | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [mapDay, setMapDay] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const refresh = useCallback(async () => {
    const [nextTrips, nextSessions] = await Promise.all([listTrips(), listAdvisorSessions()]);
    setTrips(nextTrips);
    setSessions(nextSessions);
    const targetSession = searchParams.get("sessionId");
    const targetTrip = searchParams.get("tripId");
    const found = nextSessions.find((item) => item.id === targetSession);
    if (found) {
      setActive(found);
      setSelected(found.context?.selected_candidate_ids ?? []);
    }
    if (targetTrip) {
      setTripId(targetTrip);
      setMode("existing");
    }
  }, [searchParams]);

  useEffect(() => {
    refresh().catch((reason: Error) => setError(reason.message));
  }, [refresh]);

  const selectedTrip = trips.find((trip) => trip.id === tripId);
  const draftSelectionChanged = Boolean(active?.draft && (
    active.draft.selected_candidate_ids.length !== selected.length ||
    active.draft.selected_candidate_ids.some((id) => !selected.includes(id))
  ));
  const draftIsStale = Boolean(active?.draft_stale || draftSelectionChanged);

  async function startSession(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true); setError(""); setNotice("");
    try {
      const created = await createAdvisorSession({
        mode,
        ...(mode === "existing" ? { trip_id: tripId } : {
          destination: destination.trim(), start_date: startDate, end_date: endDate,
          budget: budget ? Number(budget) : null,
        }),
        pace_level: pace,
        preference_text: preference.trim(),
      });
      setActive(created); setSelected(created.context?.selected_candidate_ids ?? []); setNotice("会话已保存，可以开始聊地点和偏好。");
      await refresh();
    } catch (reason) { setError((reason as Error).message); }
    finally { setBusy(false); }
  }

  async function submitMessage(event?: FormEvent<HTMLFormElement>, preset?: string) {
    event?.preventDefault();
    if (!active) return;
    const content = (preset ?? message).trim();
    if (!content) return;
    setBusy(true); setError(""); setNotice("");
    try {
      const updated = await sendAdvisorMessage(active.id, content);
      setActive(updated); setMessage("");
      const candidateIds = updated.messages.at(-1)?.payload?.candidate_ids;
      if (updated.trip_id) {
        setSelected(updated.context.selected_candidate_ids ?? []);
      } else if (Array.isArray(candidateIds) && candidateIds.length) {
        setSelected((current) => [...new Set([...current, ...candidateIds.filter((id): id is string => typeof id === "string")])]);
      }
      setSessions((current) => [updated, ...current.filter((item) => item.id !== updated.id)]);
    } catch (reason) { setError((reason as Error).message); }
    finally { setBusy(false); }
  }

  async function generateDraft() {
    if (!active || !selected.length) return;
    setBusy(true); setError(""); setNotice("");
    try {
      const updated = await createAdvisorDraft(active.id, selected);
      setActive(updated); setNotice(`已生成第 ${updated.draft_version} 版草案，确认前不会写入正式旅行数据。`);
      setSelected(updated.draft?.selected_candidate_ids ?? selected);
      setMapDay(updated.draft?.stops[0]?.day ?? "");
      setSessions((current) => [updated, ...current.filter((item) => item.id !== updated.id)]);
    } catch (reason) { setError((reason as Error).message); }
    finally { setBusy(false); }
  }

  async function confirmDraft() {
    if (!active?.draft) return;
    setBusy(true); setError("");
    try {
      const result = await confirmAdvisorDraft(active.id, active.draft.version);
      router.push(`/trips/${result.trip_id}/plan`);
    } catch (reason) { setError((reason as Error).message); }
    finally { setBusy(false); }
  }

  function toggleCandidate(id: string) {
    setSelected((current) => current.includes(id) ? current.filter((item) => item !== id) : [...current, id]);
  }

  const canCreateSession = mode === "existing" ? Boolean(tripId) : Boolean(destination.trim() && startDate && endDate && endDate >= startDate);
  const draftDays = [...new Set(active?.draft?.stops.map((stop) => stop.day) ?? [])];
  const visibleMapDay = draftDays.includes(mapDay) ? mapDay : draftDays[0] ?? "";
  const mapStops = active?.draft?.stops.filter((stop) => stop.day === visibleMapDay) ?? [];

  function focusPlanningSetup(nextMode: Mode, nextPreference?: string) {
    setMode(nextMode);
    if (nextPreference) setPreference(nextPreference);
    document.getElementById("advisor-start")?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  return (
    <main className="shell advisor-page advisor-live">
      <header className="advisor-heading">
        <div><h1>和旅行顾问一起，<br className="advisor-title-break" />把想法变成行程。</h1><p>先说说你喜欢什么。地点和日期可以慢慢补齐，正式行程会等你确认后再保存。</p></div>
      </header>

      <section className="advisor-start panel" id="advisor-start">
        <div className="advisor-panel-title"><div><h2>开始一次规划</h2><p>目前顾问能识别明确提到的地点并交给地图服务核验。目的地灵感推荐与开放式多轮推理需要配置模型能力。</p></div></div>
        <div className="advisor-mode-switch" role="group" aria-label="选择规划方式">
          {modes.map((item) => <button key={item.id} type="button" className={mode === item.id ? "is-selected" : ""} aria-pressed={mode === item.id} onClick={() => setMode(item.id)}>
            <strong>{item.title}</strong><small>{item.description}</small>
          </button>)}
        </div>
        <form className="advisor-live-form" onSubmit={startSession}>
          {mode === "existing" ? <label>选择你的旅行<select value={tripId} onChange={(event) => setTripId(event.target.value)} required>
            <option value="">选择旅行</option>{trips.map((trip) => <option key={trip.id} value={trip.id}>{trip.destination} · {trip.start_date} 至 {trip.end_date}</option>)}
          </select>{selectedTrip && <small>将以 {selectedTrip.destination} 的日期和节奏作为规划上下文。</small>}</label> : <>
            <label>目的地<input value={destination} onChange={(event) => setDestination(event.target.value)} placeholder={mode === "discover" ? "还没决定？先填感兴趣的城市或区域" : "例如：成都"} required /></label>
            <label>开始日期<input type="date" value={startDate} onChange={(event) => setStartDate(event.target.value)} required /></label>
            <label>结束日期<input type="date" value={endDate} min={startDate} onChange={(event) => setEndDate(event.target.value)} required /></label>
            <label>预算（可选）<input type="number" min="0" value={budget} onChange={(event) => setBudget(event.target.value)} placeholder="元" /></label>
          </>}
          <label>旅行节奏<select value={pace} onChange={(event) => setPace(Number(event.target.value))}><option value={1}>很轻松</option><option value={2}>轻松</option><option value={3}>适中</option><option value={4}>紧凑</option><option value={5}>很紧凑</option></select></label>
          <label className="advisor-live-preference">偏好与限制<input value={preference} onChange={(event) => setPreference(event.target.value)} placeholder="例如：喜欢美食和博物馆，不安排爬山" /></label>
          <button type="submit" className="primary" disabled={!canCreateSession || busy}>{busy ? "处理中…" : "创建会话"}</button>
        </form>
        {error && <p className="advisor-live-error" role="alert">{error}</p>}
      </section>

      <div className="advisor-live-columns">
        <section className="advisor-chat panel" aria-label="顾问对话">
          <div className="advisor-panel-title"><div><h2>对话</h2><p>{active ? `${active.destination || selectedTrip?.destination || "未设置目的地"} · ${active.status === "ACTIVE" ? "草案待确认" : "已确认"}` : "创建或打开一个会话后开始"}</p></div>
            {active && <span className="advisor-demo-mark">已保存</span>}</div>
          {active ? <>
            {active.trip_id && <div className="advisor-context-card">
              <strong>当前计划与已确认记忆</strong>
              {active.context?.current_plan ? <p>计划 v{active.context.current_plan.version} · {active.context.current_plan.stops.length} 个地点 · {active.context.current_plan.warnings.length} 条原计划提示</p> : <p>这趟旅行还没有已保存计划。</p>}
              {active.context?.confirmed_memories?.length ? <ul>{active.context.confirmed_memories.slice(0, 4).map((memory) => <li key={memory.id}>{memory.text}</li>)}</ul> : <small>没有已确认的旅行记忆</small>}
            </div>}
            <div className="advisor-messages" aria-live="polite">
              {active.messages.map((item) => <article className={`advisor-message ${item.role}`} key={item.id}><span>{item.role === "user" ? "你" : "顾问"}</span><p>{item.content}</p></article>)}
              {!active.messages.length && <p className="advisor-empty">告诉顾问你想去的地点或偏好。</p>}
            </div>
            <div className="advisor-quick-prompts"><button type="button" disabled={busy || active.status !== "ACTIVE"} onClick={() => submitMessage(undefined, "我喜欢节奏轻松一些，多留休息时间")}>节奏轻松一点</button><button type="button" disabled={busy || active.status !== "ACTIVE"} onClick={() => submitMessage(undefined, "我想去宽窄巷子")}>示例：搜索宽窄巷子</button></div>
            <form className="advisor-composer" onSubmit={submitMessage}>
              <label className="sr-only" htmlFor="advisor-message">告诉顾问你的想法</label>
              <textarea id="advisor-message" value={message} onChange={(event) => setMessage(event.target.value)} placeholder="说出地点、偏好或想调整的内容…" rows={2} disabled={busy || active.status !== "ACTIVE"} />
              <button className="primary" type="submit" disabled={!message.trim() || busy || active.status !== "ACTIVE"}>发送</button>
            </form>
          </> : <div className="advisor-welcome">
            <span className="advisor-welcome-mark" aria-hidden="true">✳</span>
            <h3>不用先想好完整路线。</h3>
            <p>选一种开始方式，填写大致日期和偏好，再把想去的地方告诉顾问。你可以边聊边调整。</p>
            <div className="advisor-welcome-prompts"><button type="button" onClick={() => focusPlanningSetup("destination", "喜欢本地美食和老街巷，行程节奏轻松一些")}>想慢慢逛，看看本地生活 <span>↗</span></button><button type="button" onClick={() => focusPlanningSetup("destination", "我会逐个告诉你想去的地点，请帮我合理安排顺序")}>我已经有几个想去的地方 <span>↗</span></button><button type="button" onClick={() => focusPlanningSetup("existing")}>调整一趟已经创建的旅行 <span>↗</span></button></div>
          </div>}
        </section>

        <div className="advisor-plan">
          <section className="advisor-preferences panel">
            <div className="advisor-panel-title"><div><h2>地图地点候选</h2><p>只会将你选中的已核验地点放入草案。</p></div><button type="button" className="secondary" disabled={!active || !selected.length || busy || active.status !== "ACTIVE"} onClick={generateDraft}>{active?.draft ? `按当前选择重新生成（${selected.length}）` : `生成草案（${selected.length}）`}</button></div>
            {active?.candidates.length ? <div className="advisor-live-candidates">{active.candidates.map((candidate) => <label className="advisor-live-candidate" key={candidate.id}>
              <input type="checkbox" checked={selected.includes(candidate.id)} onChange={() => toggleCandidate(candidate.id)} disabled={active.status !== "ACTIVE"} />
              <span><strong>{candidate.name}{candidate.in_current_plan ? <em>当前计划</em> : null}</strong><small>{candidate.category} · {candidate.address || "地址未提供"} · 约 {candidate.duration_min} 分钟</small></span>
            </label>)}</div> : <p className="advisor-empty">还没有地点候选。请在对话中给出地点名称，顾问会查询地图服务。</p>}
            {active?.draft && <div className="advisor-draft-summary"><strong>第 {active.draft.version} 版草案</strong><span>{active.draft.stops.length} 个已安排地点</span><span>{active.draft.warnings.length} 条排程提示</span></div>}
            {active?.draft && <div className="advisor-live-diff" aria-label="草案变更摘要">
              <strong>相对当前计划的变化</strong>
              <span>新增 {active.draft.change_summary.add ?? 0}</span><span>移除 {active.draft.change_summary.remove ?? 0}</span>
              <span>调整时间 {active.draft.change_summary.move ?? 0}</span><span>保留 {active.draft.change_summary.keep ?? 0}</span>
              {active.draft.pace_change && <span>节奏 {active.draft.pace_change.before} → {active.draft.pace_change.after}</span>}
              {active.draft.changes.filter((change) => change.operation !== "KEEP").map((change) => <div key={`${change.stable_key}-${change.operation}`} className={`advisor-live-diff-row ${change.operation.toLowerCase()}`}>
                <b>{({ ADD: "新增", REMOVE: "移除", MOVE: "调整" } as Record<string, string>)[change.operation]}</b><span>{change.name}</span>
                {change.before && <small>原：{String(change.before.day)} {String(change.before.start_time).slice(0, 5)}</small>}
                {change.after && <small>新：{String(change.after.day)} {String(change.after.start_time).slice(0, 5)}</small>}
              </div>)}
            </div>}
          </section>

          <section className="advisor-itinerary panel">
            <div className="advisor-panel-title"><div><h2>行程草案</h2><p>{active?.destination || selectedTrip?.destination || "尚未设置目的地"} · {active?.start_date || selectedTrip?.start_date || "日期待定"} 至 {active?.end_date || selectedTrip?.end_date || "日期待定"}</p></div><span className="advisor-draft-state">{active?.draft ? "未确认" : "待生成"}</span></div>
            {draftDays.length > 1 && <label className="advisor-live-day-picker">查看日期<select value={visibleMapDay} onChange={(event) => setMapDay(event.target.value)}>{draftDays.map((day, index) => <option key={day} value={day}>第 {index + 1} 天 · {day}</option>)}</select></label>}
            {active?.draft?.stops.length ? <div className="advisor-stop-list">{(draftDays.length > 1 ? mapStops : active.draft.stops).map((stop, index) => <article className="advisor-stop" key={`${stop.candidate_id}-${stop.day}`}>
              <span className="advisor-stop-index">{index + 1}</span><div className="advisor-stop-time">{stop.start_time.slice(0, 5)}<br /><span>{stop.end_time.slice(0, 5)}</span></div>
              <div className="advisor-stop-content"><h3>{stop.name}</h3><small>{stop.day} · {stop.category}</small><p>{stop.address || "地图候选地点"}</p></div>
            </article>)}</div> : <p className="advisor-empty">选择地点并生成草案后，这里会显示按日期和地图交通时间排出的路线。</p>}
            {active?.draft?.stops.length ? <div className="advisor-live-map"><RouteMap title="当日真实地点地图" points={mapStops.map((stop) => ({ id: stop.candidate_id, name: stop.name, detail: `${stop.start_time.slice(0, 5)}–${stop.end_time.slice(0, 5)}`, latitude: stop.latitude, longitude: stop.longitude }))} disclaimer="编号与草案中的游览顺序一致。" /></div> : null}
            {active?.draft?.warnings.map((warning) => <p className="advisor-live-warning" key={warning}>{warning}</p>)}
          </section>
        </div>
      </div>

      <section className="advisor-live-history panel">
        <div className="advisor-panel-title"><div><h2>最近会话</h2><p>会话属于当前账户，可继续编辑未确认草案。</p></div></div>
        {sessions.length ? <div className="advisor-live-session-list">{sessions.map((item) => <button type="button" key={item.id} className={active?.id === item.id ? "is-active" : ""} onClick={() => { setActive(item); setSelected(item.context?.selected_candidate_ids ?? []); setError(""); }}>
          <span><strong>{item.destination || "未指定目的地"}</strong><small>{item.updated_at.slice(0, 16).replace("T", " ")} · {item.status === "CONFIRMED" ? "已确认" : item.draft ? "草案待确认" : "对话中"}</small></span><span>打开 →</span>
        </button>)}</div> : <p className="advisor-empty">还没有顾问会话。</p>}
      </section>

      {(active?.draft || active?.confirmed_trip_id) && <footer className="advisor-confirm-bar">
        <div><strong>{active?.status === "CONFIRMED" ? "草案已保存" : "确认后写入正式旅行"}</strong><p>保存时会创建或更新 Wishlist 与 Plan，重复确认不会创建重复计划。</p></div>
        {notice && <span role="status" className="advisor-action-notice">{notice}</span>}
        {active?.draft && draftIsStale && <span role="status" className="advisor-live-error">对话、节奏或选点已变化，请重新生成草案</span>}
        {active?.status === "CONFIRMED" && active.confirmed_trip_id ? <Link className="primary" href={`/trips/${active.confirmed_trip_id}/plan`}>查看已保存行程</Link> : <button type="button" className="primary" onClick={confirmDraft} disabled={!active?.draft || draftIsStale || busy}>{busy ? "保存中…" : "确认并保存行程"}</button>}
      </footer>}
    </main>
  );
}
