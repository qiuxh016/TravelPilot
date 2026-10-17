"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { RouteMap } from "../../components/route-map";
import {
  AdvisorDestinationRecommendation, AdvisorRuntimeStatus, AdvisorSession, Trip, confirmAdvisorDraft, createAdvisorDraft, createAdvisorSession,
  getAdvisorRuntimeStatus, listAdvisorSessions, listTrips, recommendAdvisorDestinations, sendAdvisorMessage, updateAdvisorSessionContext,
} from "../../lib/api";

type Mode = "discover" | "destination" | "existing";
const modes: { id: Mode; title: string; description: string }[] = [
  { id: "discover", title: "先聊旅行想法", description: "可以从偏好开始，之后再补城市与日期。" },
  { id: "destination", title: "我有目的地", description: "从想去的地点出发，逐步整理候选和行程。" },
  { id: "existing", title: "调整现有旅行", description: "读取你自己的旅行信息，草案确认后更新计划。" },
];

function dateAfter(days: number) {
  const date = new Date();
  date.setDate(date.getDate() + days);
  return date.toISOString().slice(0, 10);
}

function splitTags(value: string) {
  return [...new Set(value.split(/[，,、;；]/).map((item) => item.trim()).filter(Boolean))].slice(0, 10);
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
  const [origin, setOrigin] = useState("");
  const [companionsText, setCompanionsText] = useState("");
  const [constraintsText, setConstraintsText] = useState("");
  const [tripId, setTripId] = useState("");
  const [trips, setTrips] = useState<Trip[]>([]);
  const [sessions, setSessions] = useState<AdvisorSession[]>([]);
  const [runtimeStatus, setRuntimeStatus] = useState<AdvisorRuntimeStatus | null>(null);
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
      setDestination(found.destination || "");
      setStartDate(found.start_date || "");
      setEndDate(found.end_date || "");
      setBudget(found.budget == null ? "" : String(found.budget));
      setPace(found.pace_level);
      setPreference(found.preference_text || "");
      setOrigin(found.context?.planning_inputs?.origin ?? "");
      setCompanionsText((found.context?.planning_inputs?.companions ?? []).join("、"));
      setConstraintsText((found.context?.planning_inputs?.hard_constraints ?? []).join("、"));
    }
    if (targetTrip) {
      setTripId(targetTrip);
      setMode("existing");
    }
  }, [searchParams]);

  useEffect(() => {
    refresh().catch((reason: Error) => setError(reason.message));
  }, [refresh]);

  useEffect(() => {
    getAdvisorRuntimeStatus().then(setRuntimeStatus).catch(() => setRuntimeStatus({
      llm_status: "UNAVAILABLE", model_name: null, output_token_limit: 0,
      upstream_status: "CHECKED_PER_REQUEST",
    }));
  }, []);

  const selectedTrip = trips.find((trip) => trip.id === tripId);
  const destinationRecommendations = active?.messages.flatMap((item) => {
    const value = item.payload?.recommendations;
    return Array.isArray(value) ? value as AdvisorDestinationRecommendation[] : [];
  }) ?? [];
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
          destination: destination.trim(),
          ...(startDate && endDate ? { start_date: startDate, end_date: endDate } : {}),
          budget: budget ? Number(budget) : null,
        }),
        pace_level: pace,
        preference_text: preference.trim(),
        origin: origin.trim(), companions: splitTags(companionsText), hard_constraints: splitTags(constraintsText),
      });
      setActive(created); setSelected(created.context?.selected_candidate_ids ?? []);
      setDestination(created.destination || ""); setStartDate(created.start_date || ""); setEndDate(created.end_date || "");
      setBudget(created.budget == null ? "" : String(created.budget)); setPace(created.pace_level); setPreference(created.preference_text || "");
      setNotice("会话已保存，可以开始聊地点和偏好。");
      await refresh();
    } catch (reason) { setError((reason as Error).message); }
    finally { setBusy(false); }
  }

  async function saveSessionContext(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!active) return;
    if (Boolean(startDate) !== Boolean(endDate)) {
      setError("开始和结束日期需要一起填写。");
      return;
    }
    setBusy(true); setError(""); setNotice("");
    try {
      const updated = await updateAdvisorSessionContext(active.id, {
        destination: destination.trim(), start_date: startDate || null, end_date: endDate || null,
        budget: budget ? Number(budget) : null, pace_level: pace, preference_text: preference.trim(),
        origin: origin.trim(), companions: splitTags(companionsText), hard_constraints: splitTags(constraintsText),
      });
      setActive(updated);
      setSessions((current) => [updated, ...current.filter((item) => item.id !== updated.id)]);
      setNotice("旅行条件已保存；旧草案如有变化需要重新生成。");
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

  async function recommendDestinations() {
    if (!active) return;
    setBusy(true); setError(""); setNotice("");
    try {
      const updated = await recommendAdvisorDestinations(active.id);
      setActive(updated);
      setSessions((current) => [updated, ...current.filter((item) => item.id !== updated.id)]);
      setNotice("已按地图检索证据整理目的地候选；地点与出行条件仍需你确认。 ");
    } catch (reason) { setError((reason as Error).message); }
    finally { setBusy(false); }
  }

  async function chooseDestination(city: string) {
    if (!active) return;
    setBusy(true); setError(""); setNotice("");
    try {
      const updated = await updateAdvisorSessionContext(active.id, {
        destination: city, start_date: startDate || null, end_date: endDate || null,
        budget: budget ? Number(budget) : null, pace_level: pace, preference_text: preference.trim(),
        origin: origin.trim(), companions: splitTags(companionsText), hard_constraints: splitTags(constraintsText),
      });
      setDestination(city); setActive(updated);
      setSessions((current) => [updated, ...current.filter((item) => item.id !== updated.id)]);
      setNotice(`${city} 已设为当前目的地。接下来可以搜索该城市的具体地点。`);
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

  const canCreateSession = mode === "existing" ? Boolean(tripId) : mode === "discover" || Boolean(destination.trim() && startDate && endDate && endDate >= startDate);
  const draftDays = [...new Set(active?.draft?.stops.map((stop) => stop.day) ?? [])];
  const visibleMapDay = draftDays.includes(mapDay) ? mapDay : draftDays[0] ?? "";
  const mapStops = active?.draft?.stops.filter((stop) => stop.day === visibleMapDay) ?? [];

  function focusPlanningSetup(nextMode: Mode, nextPreference?: string) {
    setMode(nextMode);
    if (nextMode === "destination" && (!startDate || !endDate)) {
      setStartDate(dateAfter(14)); setEndDate(dateAfter(16));
    }
    if (nextPreference) setPreference(nextPreference);
    document.getElementById("advisor-start")?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function chooseMode(nextMode: Mode) {
    setMode(nextMode);
    if (nextMode === "discover") { setStartDate(""); setEndDate(""); }
    else if (!startDate || !endDate) { setStartDate(dateAfter(14)); setEndDate(dateAfter(16)); }
  }

  return (
    <main className="shell advisor-page advisor-live">
      <header className="advisor-heading">
        <div><h1>和旅行顾问一起，<br className="advisor-title-break" />把想法变成行程。</h1><p>先说说你喜欢什么。地点和日期可以慢慢补齐，正式行程会等你确认后再保存。</p></div>
      </header>

      <section className="advisor-start panel" id="advisor-start">
        <div className="advisor-panel-title"><div><h2>开始一次规划</h2><p>顾问会把明确提到的地点和兴趣类别交给地图服务核验；候选由你选择后再进入行程草案。</p></div></div>
        <div className="advisor-mode-switch" role="group" aria-label="选择规划方式">
          {modes.map((item) => <button key={item.id} type="button" className={mode === item.id ? "is-selected" : ""} aria-pressed={mode === item.id} onClick={() => chooseMode(item.id)}>
            <strong>{item.title}</strong><small>{item.description}</small>
          </button>)}
        </div>
        {runtimeStatus && <p className={`advisor-provider-status${runtimeStatus.llm_status !== "CONFIGURED" ? " is-local" : ""}`} role="status">
          {runtimeStatus.llm_status === "CONFIGURED"
            ? `模型已配置：${runtimeStatus.model_name} · 单轮输出上限 ${runtimeStatus.output_token_limit} tokens。上游连通状态会在实际请求后显示。`
            : runtimeStatus.llm_status === "NOT_CONFIGURED"
              ? "模型未配置 · 将使用本地规则识别与模板回复。"
              : "暂时无法读取模型配置状态；请检查 API 服务，并确认它已更新至当前版本。"}
        </p>}
        <form className="advisor-live-form" onSubmit={startSession}>
          {mode === "existing" ? <label>选择你的旅行<select value={tripId} onChange={(event) => setTripId(event.target.value)} required>
            <option value="">选择旅行</option>{trips.map((trip) => <option key={trip.id} value={trip.id}>{trip.destination} · {trip.start_date} 至 {trip.end_date}</option>)}
          </select>{selectedTrip && <small>将以 {selectedTrip.destination} 的日期和节奏作为规划上下文。</small>}</label> : <>
            <label>目的地<input value={destination} onChange={(event) => setDestination(event.target.value)} placeholder={mode === "discover" ? "可以先留空，之后再补城市或区域" : "例如：成都"} required={mode !== "discover"} /></label>
            <label>开始日期<input type="date" value={startDate} onChange={(event) => setStartDate(event.target.value)} required={mode !== "discover"} /></label>
            <label>结束日期<input type="date" value={endDate} min={startDate || undefined} onChange={(event) => setEndDate(event.target.value)} required={mode !== "discover"} /></label>
            <label>预算（可选）<input type="number" min="0" value={budget} onChange={(event) => setBudget(event.target.value)} placeholder="元" /></label>
          </>}
          <label>旅行节奏<select value={pace} onChange={(event) => setPace(Number(event.target.value))}><option value={1}>很轻松</option><option value={2}>轻松</option><option value={3}>适中</option><option value={4}>紧凑</option><option value={5}>很紧凑</option></select></label>
          <label className="advisor-live-preference">偏好与限制<input value={preference} onChange={(event) => setPreference(event.target.value)} placeholder="例如：喜欢美食和博物馆，不安排爬山" /></label>
          <label>出发地（可选）<input value={origin} onChange={(event) => setOrigin(event.target.value)} placeholder="例如：成都东站" /></label>
          <label>同行人（可选）<input value={companionsText} onChange={(event) => setCompanionsText(event.target.value)} placeholder="例如：儿童、长辈，用顿号分隔" /></label>
          <label>硬性限制（可选）<input value={constraintsText} onChange={(event) => setConstraintsText(event.target.value)} placeholder="例如：不去博物馆、花生过敏" /></label>
          <small className="advisor-context-note">出发地会尝试用地图精确核验，并计入首日首个地点的交通时间；同行人与无法由地图数据验证的限制会显示在规划上下文或人工核对提示中。</small>
          <button type="submit" className="primary" disabled={!canCreateSession || busy}>{busy ? "处理中…" : "创建会话"}</button>
        </form>
        {error && <p className="advisor-live-error" role="alert">{error}</p>}
        {notice && <p role="status" className="advisor-action-notice">{notice}</p>}
      </section>

      <div className="advisor-live-columns">
        <section className="advisor-chat panel" aria-label="顾问对话">
          <div className="advisor-panel-title"><div><h2>对话</h2><p>{active ? `${active.destination || selectedTrip?.destination || "未设置目的地"} · ${active.status === "ACTIVE" ? "草案待确认" : "已确认"}` : "创建或打开一个会话后开始"}</p></div>
            {active && <span className="advisor-demo-mark">已保存</span>}</div>
          {active ? <>
            {active.status === "ACTIVE" && <details className="advisor-context-editor" open={Boolean(active.context?.planning_state?.missing_required?.length)}>
              <summary>{active.context?.planning_state?.missing_required?.length
                ? `旅行条件待补充：${active.context.planning_state.missing_required.map((slot) => ({ destination: "目的地", start_date: "开始日期", end_date: "结束日期" } as Record<string, string>)[slot] ?? slot).join("、")}`
                : "旅行条件 · 可随时调整"}</summary>
              <form className="advisor-context-form" onSubmit={saveSessionContext}>
                {active.mode === "existing" && <p className="advisor-context-note">修改只写入待确认草案；确认时才会更新这趟旅行。</p>}
                <label>目的地<input value={destination} onChange={(event) => setDestination(event.target.value)} placeholder="城市或区域" /></label>
                <label>开始日期<input type="date" value={startDate} onChange={(event) => setStartDate(event.target.value)} /></label>
                <label>结束日期<input type="date" value={endDate} min={startDate || undefined} onChange={(event) => setEndDate(event.target.value)} /></label>
                <label>预算<input type="number" min="0" value={budget} onChange={(event) => setBudget(event.target.value)} placeholder="可选，元" /></label>
                <label>节奏<select value={pace} onChange={(event) => setPace(Number(event.target.value))}><option value={1}>很轻松</option><option value={2}>轻松</option><option value={3}>适中</option><option value={4}>紧凑</option><option value={5}>很紧凑</option></select></label>
                <label className="advisor-context-preference">偏好与限制<input value={preference} onChange={(event) => setPreference(event.target.value)} placeholder="美食、博物馆、饮食限制等" /></label>
                <label>出发地<input value={origin} onChange={(event) => setOrigin(event.target.value)} placeholder="例如：成都东站" /></label>
                <label>同行人<input value={companionsText} onChange={(event) => setCompanionsText(event.target.value)} placeholder="儿童、长辈，用顿号分隔" /></label>
                <label>硬性限制<input value={constraintsText} onChange={(event) => setConstraintsText(event.target.value)} placeholder="例如：不去博物馆、花生过敏" /></label>
                <small className="advisor-context-note">出发地经地图精确核验后，会参与首日首个地点的交通时间估算。</small>
                <button type="submit" className="secondary" disabled={busy}>{busy ? "保存中…" : "保存旅行条件"}</button>
              </form>
              {active.mode === "discover" && !active.destination && <button type="button" className="secondary advisor-recommend-button" onClick={recommendDestinations} disabled={busy}>
                {busy ? "正在查询地图…" : "按偏好比较目的地"}
              </button>}
              {active.mode === "discover" && !active.destination && <small className="advisor-context-note">支持自然/公园、美食、历史街区、海滨偏好。最多对 6 座城市执行 12 次高德地点搜索；只比较地点证据，不含城市间交通、价格或季节评分。</small>}
            </details>}
            {active.trip_id && <div className="advisor-context-card">
              <strong>当前计划与已确认记忆</strong>
              {active.context?.current_plan ? <p>计划 v{active.context.current_plan.version} · {active.context.current_plan.stops.length} 个地点 · {active.context.current_plan.warnings.length} 条原计划提示</p> : <p>这趟旅行还没有已保存计划。</p>}
              {active.context?.confirmed_memories?.length ? <ul>{active.context.confirmed_memories.slice(0, 4).map((memory) => <li key={memory.id}>{memory.text}</li>)}</ul> : <small>没有已确认的旅行记忆</small>}
            </div>}
            <div className="advisor-messages" aria-live="polite">
              {active.messages.map((item) => <article className={`advisor-message ${item.role}`} key={item.id}>
                <span>{item.role === "user" ? "你" : "顾问"}</span><p>{item.content}</p>
                {item.role === "assistant" && typeof item.payload?.extraction_source === "string" ? <small className="advisor-message-source">
                  {item.payload.extraction_source === "MODEL" ? "模型已理解" : item.payload.model_fallback_reason ? "模型暂不可用，已切换本地识别" : "本地规则识别"}
                  {" · "}{item.payload.response_source === "MODEL" ? "模型回复" : "模板回复"}
                </small> : null}
              </article>)}
              {!active.messages.length && <p className="advisor-empty">告诉顾问你想去的地点或偏好。</p>}
            </div>
            {destinationRecommendations.length > 0 && <section className="advisor-destination-results" aria-label="目的地候选">
              <div><strong>地图证据支持的目的地</strong><small>候选来自高德地点检索 · {destinationRecommendations[0].retrieved_at?.slice(0, 16).replace("T", " ")}</small></div>
              <div className="advisor-destination-grid">{destinationRecommendations.map((item) => <article key={item.city}>
                <h3>{item.city}</h3><p>{item.reasons.join("；")}</p>
                <ul>{item.places.slice(0, 3).map((place) => <li key={place.provider_id}>{place.name}<small>{place.category}{place.address ? ` · ${place.address}` : ""}</small></li>)}</ul>
                <button type="button" className="secondary" onClick={() => chooseDestination(item.city)} disabled={busy || active.status !== "ACTIVE"}>选择这个城市</button>
              </article>)}</div>
            </section>}
            <div className="advisor-quick-prompts">{active.preference_text && <button type="button" disabled={busy || active.status !== "ACTIVE" || !active.destination} onClick={() => submitMessage(undefined, `请按我的偏好搜索地图候选：${active.preference_text}`)}>按当前偏好找候选</button>}<button type="button" disabled={busy || active.status !== "ACTIVE"} onClick={() => submitMessage(undefined, "我喜欢节奏轻松一些，多留休息时间")}>节奏轻松一点</button><button type="button" disabled={busy || active.status !== "ACTIVE"} onClick={() => submitMessage(undefined, "我想去宽窄巷子")}>示例：搜索宽窄巷子</button></div>
            <form className="advisor-composer" onSubmit={submitMessage}>
              <label className="sr-only" htmlFor="advisor-message">告诉顾问你的想法</label>
              <textarea id="advisor-message" value={message} onChange={(event) => setMessage(event.target.value)} placeholder="说出地点、偏好或想调整的内容…" rows={2} disabled={busy || active.status !== "ACTIVE"} />
              <button className="primary" type="submit" disabled={!message.trim() || busy || active.status !== "ACTIVE"}>发送</button>
            </form>
          </> : <div className="advisor-welcome">
            <span className="advisor-welcome-mark" aria-hidden="true">✳</span>
            <h3>不用先想好完整路线。</h3>
            <p>可以先聊偏好，也可以直接填写城市和日期。条件补齐后，地图候选由你选择，再生成可确认的行程。</p>
            <div className="advisor-welcome-prompts"><button type="button" onClick={() => focusPlanningSetup("destination", "喜欢本地美食和老街巷，行程节奏轻松一些")}>想慢慢逛，看看本地生活 <span>↗</span></button><button type="button" onClick={() => focusPlanningSetup("destination", "我会逐个告诉你想去的地点，请帮我合理安排顺序")}>我已经有几个想去的地方 <span>↗</span></button><button type="button" onClick={() => focusPlanningSetup("existing")}>调整一趟已经创建的旅行 <span>↗</span></button></div>
          </div>}
        </section>

        <div className="advisor-plan">
          <section className="advisor-preferences panel">
            <div className="advisor-panel-title"><div><h2>地图地点候选</h2><p>只会将你选中的已核验地点放入草案。</p></div><button type="button" className="secondary" disabled={!active || !selected.length || Boolean(active.context?.planning_state?.missing_required?.length) || busy || active.status !== "ACTIVE"} onClick={generateDraft}>{active?.draft ? `按当前选择重新生成（${selected.length}）` : `生成草案（${selected.length}）`}</button></div>
            {active?.candidates.length ? <div className="advisor-live-candidates">{active.candidates.map((candidate) => <label className="advisor-live-candidate" key={candidate.id}>
              <input type="checkbox" checked={selected.includes(candidate.id)} onChange={() => toggleCandidate(candidate.id)} disabled={active.status !== "ACTIVE"} />
              <span><strong>{candidate.name}{candidate.in_current_plan ? <em>当前计划</em> : null}</strong><small>{candidate.verification_status === "VERIFIED" ? (candidate.provider === "amap" ? "高德地图已核验" : "地图服务已核验") : candidate.provider === "fake" ? "模拟地点数据" : "已保存地点"} · {candidate.category} · {candidate.address || "地址未提供"} · 约 {candidate.duration_min} 分钟</small>{candidate.suggestion && <small>{candidate.suggestion}</small>}</span>
            </label>)}</div> : <p className="advisor-empty">{active && !active.destination ? "先在旅行条件中补充目的地城市，再按偏好搜索候选。" : "还没有地点候选。请在对话中给出地点或兴趣类别，顾问会查询地图服务。"}</p>}
            {active?.draft && <div className="advisor-draft-summary"><strong>第 {active.draft.version} 版草案</strong><span>{active.draft.stops.length} 个已安排地点</span><span>{active.draft.warnings.length} 条排程提示</span></div>}
            {active?.draft?.applied_personal_memories?.length ? <p className="advisor-context-note">本次采用的已确认偏好：{active.draft.applied_personal_memories.map((memory) => memory.text).join("；")}</p> : null}
            {active?.draft?.transport && <small className="advisor-context-note">交通时间：{active.draft.transport.status === "VERIFIED" ? `${active.draft.transport.source} 已核验` : active.draft.transport.status === "SIMULATED" ? "本地模拟数据" : "仅安排单个地点，无需地点间交通矩阵"}{active.draft.transport.retrieved_at ? ` · ${active.draft.transport.retrieved_at.slice(0, 16).replace("T", " ")}` : ""}</small>}
            {active?.draft && <div className="advisor-live-diff" aria-label="草案变更摘要">
              <strong>相对当前计划的变化</strong>
              <span>新增 {active.draft.change_summary.add ?? 0}</span><span>移除 {active.draft.change_summary.remove ?? 0}</span>
              <span>调整时间 {active.draft.change_summary.move ?? 0}</span><span>保留 {active.draft.change_summary.keep ?? 0}</span>
              {active.draft.pace_change && <span>节奏 {active.draft.pace_change.before} → {active.draft.pace_change.after}</span>}
              {active.draft.trip_changes && Object.entries(active.draft.trip_changes.after).filter(([key, value]) => active.draft?.trip_changes?.before[key] !== value).map(([key, value]) => <span key={key}>{({ destination: "目的地", start_date: "开始日期", end_date: "结束日期", budget: "预算", pace_level: "节奏" } as Record<string, string>)[key] ?? key}：{String(active.draft?.trip_changes?.before[key] ?? "未设置")} → {String(value ?? "未设置")}</span>)}
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
            {active?.draft?.stops.length ? <div className="advisor-live-map"><RouteMap title="当日真实地点地图" points={mapStops.map((stop) => ({ id: stop.candidate_id, name: stop.name, detail: `${stop.start_time.slice(0, 5)}–${stop.end_time.slice(0, 5)}`, latitude: stop.latitude, longitude: stop.longitude }))} disclaimer="编号与草案中的游览顺序一致。" showDrivingRoute={mapStops.length > 1} /></div> : null}
            {active?.draft?.origin_leg && active.draft.origin_leg.status !== "NOT_PROVIDED" && <p className="advisor-context-note">
              {active.draft.origin_leg.status === "VERIFIED" || active.draft.origin_leg.status === "SIMULATED"
                ? `首日出发交通：${active.draft.origin_leg.from} → 首个地点，约 ${active.draft.origin_leg.duration_min ?? "—"} 分钟 · ${active.draft.origin_leg.source ?? "地图数据"}`
                : "出发地未能唯一核验或交通查询不可用；草案没有把这段时间计入排程。"}
            </p>}
            {active?.draft?.weather && <div className="advisor-weather-summary">
              <strong>天气参考 · {active.draft.weather.source}</strong>
              {active.draft.weather.days.length ? <div className="advisor-weather-days">{active.draft.weather.days.map((day) => <span key={day.date}>
                <b>{day.date.slice(5)}</b> {day.description} · {day.temperature_min_c ?? "—"}°–{day.temperature_max_c ?? "—"}°
                {day.precipitation_probability_max != null ? ` · 降水 ${day.precipitation_probability_max}%` : ""}
              </span>)}</div> : <p>{active.draft.weather.message || "当前没有可用预报。"}</p>}
              <small>{active.draft.weather.attribution} · 更新于 {active.draft.weather.fetched_at.slice(0, 16).replace("T", " ")}</small>
            </div>}
            {active?.draft?.warnings.map((warning) => <p className="advisor-live-warning" key={warning}>{warning}</p>)}
          </section>
        </div>
      </div>

      <section className="advisor-live-history panel">
        <div className="advisor-panel-title"><div><h2>最近会话</h2><p>会话属于当前账户，可继续编辑未确认草案。</p></div></div>
        {sessions.length ? <div className="advisor-live-session-list">{sessions.map((item) => <button type="button" key={item.id} className={active?.id === item.id ? "is-active" : ""} onClick={() => { setActive(item); setSelected(item.context?.selected_candidate_ids ?? item.draft?.selected_candidate_ids ?? []); setDestination(item.destination || ""); setStartDate(item.start_date || ""); setEndDate(item.end_date || ""); setBudget(item.budget == null ? "" : String(item.budget)); setPace(item.pace_level); setPreference(item.preference_text || ""); setError(""); }}>
          <span><strong>{item.destination || "未指定目的地"}</strong><small>{item.updated_at.slice(0, 16).replace("T", " ")} · {item.status === "CONFIRMED" ? "已确认" : item.draft ? "草案待确认" : "对话中"}</small></span><span>打开 →</span>
        </button>)}</div> : <p className="advisor-empty">还没有顾问会话。</p>}
      </section>

      {(active?.draft || active?.confirmed_trip_id) && <footer className="advisor-confirm-bar">
        <div><strong>{active?.status === "CONFIRMED" ? "草案已保存" : "确认后写入正式旅行"}</strong><p>保存时会创建或更新 Wishlist 与 Plan，重复确认不会创建重复计划。</p></div>
        {active?.draft && draftIsStale && <span role="status" className="advisor-live-error">对话、节奏或选点已变化，请重新生成草案</span>}
        {active?.status === "CONFIRMED" && active.confirmed_trip_id ? <Link className="primary" href={`/trips/${active.confirmed_trip_id}/plan`}>查看已保存行程</Link> : <button type="button" className="primary" onClick={confirmDraft} disabled={!active?.draft || draftIsStale || busy}>{busy ? "保存中…" : "确认并保存行程"}</button>}
      </footer>}
    </main>
  );
}
