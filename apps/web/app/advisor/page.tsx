"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { RouteMap } from "../../components/route-map";
import { tripOptionLabel, useTripSelection } from "../../components/trip-selection-context";
import {
  AdvisorDestinationRecommendation, AdvisorRuntimeStatus, AdvisorSession, Trip, confirmAdvisorDraft, createAdvisorDraft, createAdvisorSession,
  getAdvisorRuntimeStatus, listAdvisorSessions, listTrips, recommendAdvisorDestinations, sendAdvisorMessage, updateAdvisorSessionContext,
} from "../../lib/api";

type Mode = "discover" | "destination" | "existing";
type AdvisorStage = "home" | "setup" | "chat";

function splitTags(value: string) {
  return [...new Set(value.split(/[，,、;；]/).map((item) => item.trim()).filter(Boolean))].slice(0, 10);
}

function schedulesForSession(session: AdvisorSession) {
  const schedules: Record<string, { day: string; start_time: string }> = Object.fromEntries(
    session.candidates.filter((candidate) => candidate.explicit_day || candidate.explicit_start_time)
      .map((candidate) => [candidate.id, { day: candidate.explicit_day ?? "", start_time: candidate.explicit_start_time ?? "" }]),
  );
  for (const stop of session.draft?.stops ?? []) {
    schedules[stop.candidate_id] = { day: stop.day, start_time: stop.start_time.slice(0, 5) };
  }
  return schedules;
}

export default function AdvisorPage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { selectedTripId } = useTripSelection();
  const [mode, setMode] = useState<Mode>("destination");
  const [stage, setStage] = useState<AdvisorStage>("home");
  const [setupChoice, setSetupChoice] = useState<"trip" | "ideas" | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [destination, setDestination] = useState("");
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
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
  const [schedules, setSchedules] = useState<Record<string, { day: string; start_time: string }>>({});
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
      setStage("chat");
      setActive(found);
      setTripId(found.trip_id || "");
      setSelected(found.context?.selected_candidate_ids ?? []);
      setSchedules(schedulesForSession(found));
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
      setSetupChoice("trip");
      setStage("setup");
    } else if (!targetSession && selectedTripId) {
      setTripId(selectedTripId);
    }
  }, [searchParams, selectedTripId]);

  useEffect(() => {
    refresh().catch((reason: Error) => setError(reason.message));
  }, [refresh]);

  useEffect(() => {
    if (stage !== "setup" || searchParams.has("sessionId") || searchParams.has("tripId")) return;
    setTripId(selectedTripId);
    setMode(selectedTripId ? "existing" : "discover");
    setSetupChoice(selectedTripId ? "trip" : "ideas");
  }, [selectedTripId, stage, searchParams]);

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
  const draftScheduleChanged = Boolean(active?.draft && active.draft.stops.some((stop) => {
    const choice = schedules[stop.candidate_id];
    return !choice?.day || !choice.start_time || choice.day !== stop.day || choice.start_time !== stop.start_time.slice(0, 5);
  }));
  const draftIsStale = Boolean(active?.draft_stale || draftSelectionChanged || draftScheduleChanged);

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
      setTripId(created.trip_id || "");
      setDestination(created.destination || ""); setStartDate(created.start_date || ""); setEndDate(created.end_date || "");
      setBudget(created.budget == null ? "" : String(created.budget)); setPace(created.pace_level); setPreference(created.preference_text || "");
      setNotice("会话已保存，可以开始聊地点和偏好。");
      setStage("chat");
      setHistoryOpen(false);
      router.replace(`/advisor?sessionId=${created.id}`);
      setSessions(await listAdvisorSessions());
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
      setSchedules((current) => {
        const next = { ...current };
        for (const candidate of updated.candidates) {
          if (!next[candidate.id] && (candidate.explicit_day || candidate.explicit_start_time)) {
            next[candidate.id] = { day: candidate.explicit_day ?? "", start_time: candidate.explicit_start_time ?? "" };
          }
        }
        return next;
      });
      const assistantPayload = updated.messages.at(-1)?.payload;
      const candidateIds = Array.isArray(assistantPayload?.candidate_ids)
        ? assistantPayload.candidate_ids.filter((id): id is string => typeof id === "string")
        : [];
      const scheduleSuggestions = Array.isArray(assistantPayload?.schedule_suggestions)
        ? assistantPayload.schedule_suggestions.filter((item): item is { candidate_id: string; day: string; start_time: string } => Boolean(item && typeof item === "object" && typeof (item as Record<string, unknown>).candidate_id === "string" && typeof (item as Record<string, unknown>).day === "string" && typeof (item as Record<string, unknown>).start_time === "string"))
        : [];
      if (assistantPayload?.action === "SCHEDULE_PROPOSAL" && scheduleSuggestions.length) {
        const proposedIds = scheduleSuggestions.map((item) => item.candidate_id);
        const proposedSchedules = Object.fromEntries(scheduleSuggestions.map((item) => [item.candidate_id, { day: item.day, start_time: item.start_time }]));
        setSelected(proposedIds); setSchedules(proposedSchedules);
        setSessions((current) => [updated, ...current.filter((item) => item.id !== updated.id)]);
        setNotice("识别到你在询问当前计划的时间安排，正在生成带路线校验的建议草案……");
        try {
          const proposedDraft = await createAdvisorDraft(updated.id, proposedIds, scheduleSuggestions);
          setActive(proposedDraft); setSelected(proposedDraft.draft?.selected_candidate_ids ?? proposedIds);
          setMapDay(proposedDraft.draft?.stops[0]?.day ?? "");
          setSessions((current) => [proposedDraft, ...current.filter((item) => item.id !== proposedDraft.id)]);
          setNotice("时间建议已生成。请检查每一天的地点、交通和空档；只有点击“确认并保存行程”后才会修改当前旅行。");
        } catch (reason) {
          setError(`已读取当前计划，但建议草案生成失败：${(reason as Error).message}`);
        }
      } else {
        if (updated.trip_id) {
          setSelected(updated.context.selected_candidate_ids ?? []);
        } else if (candidateIds.length) {
          setSelected((current) => [...new Set([...current, ...candidateIds])]);
        }
        setSessions((current) => [updated, ...current.filter((item) => item.id !== updated.id)]);
      }
    } catch (reason) { setError((reason as Error).message); }
    finally { setBusy(false); }
  }

  async function generateDraft() {
    if (!active || !selected.length) return;
    if (!schedulesComplete) { setError("请为每个选中的地点确认日期和开始时间；只有对话中明确提到的时间才会预填。"); return; }
    setBusy(true); setError(""); setNotice("");
    try {
      const updated = await createAdvisorDraft(active.id, selected, selected.map((candidate_id) => ({ candidate_id, ...schedules[candidate_id] })));
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

  const canCreateSession = setupChoice === "ideas" || (setupChoice === "trip" && Boolean(tripId));
  const schedulesComplete = selected.length > 0 && selected.every((id) => Boolean(schedules[id]?.day && schedules[id]?.start_time));
  const travelDays = (() => {
    if (!active?.start_date || !active.end_date) return [];
    const start = new Date(`${active.start_date}T00:00:00Z`);
    const count = Math.min(31, Math.max(0, Math.round((Date.parse(`${active.end_date}T00:00:00Z`) - start.getTime()) / 86400000) + 1));
    return Array.from({ length: count }, (_, index) => {
      const day = new Date(start); day.setUTCDate(day.getUTCDate() + index); return day.toISOString().slice(0, 10);
    });
  })();
  const canCompareDestinations = Boolean(origin.trim() && startDate && endDate && endDate >= startDate && preference.trim());
  const draftDays = [...new Set(active?.draft?.stops.map((stop) => stop.day) ?? [])];
  const visibleMapDay = draftDays.includes(mapDay) ? mapDay : draftDays[0] ?? "";
  const mapStops = active?.draft?.stops.filter((stop) => stop.day === visibleMapDay) ?? [];
  const sessionList = sessions.length ? <div className="advisor-live-session-list">{sessions.map((item) => <button type="button" key={item.id} className={active?.id === item.id ? "is-active" : ""} onClick={() => openSession(item)}>
    <span><strong>{item.destination || "旅行想法"}</strong><small>{item.updated_at.slice(0, 16).replace("T", " ")} · {item.status === "CONFIRMED" ? "已确认" : item.draft ? "草案待确认" : "对话中"}</small></span><span>打开 →</span>
  </button>)}</div> : <p className="advisor-empty">还没有顾问会话。开始一段新对话后，它会保存在这里。</p>;

  function startNewConversation() {
    setActive(null); setMode(selectedTripId ? "existing" : "discover"); setSetupChoice(selectedTripId ? "trip" : "ideas"); setTripId(selectedTripId);
    setDestination(""); setStartDate(""); setEndDate(""); setBudget(""); setPace(3);
    setPreference(""); setOrigin(""); setCompanionsText(""); setConstraintsText("");
    setSelected([]); setSchedules({}); setMessage(""); setError(""); setNotice(""); setHistoryOpen(false); setStage("setup");
    router.replace("/advisor");
  }

  function focusPlanningSetup(nextMode: Mode, nextPreference?: string) {
    setActive(null); setStage("setup"); setHistoryOpen(false);
    setSetupChoice(nextMode === "existing" ? "trip" : "ideas");
    setMode(nextMode === "existing" ? "existing" : "discover");
    if (nextMode === "existing" && !tripId) setTripId(selectedTripId || trips[0]?.id || "");
    if (nextPreference) setPreference(nextPreference);
  }

  function openSession(item: AdvisorSession) {
    setActive(item); setSelected(item.context?.selected_candidate_ids ?? item.draft?.selected_candidate_ids ?? []);
    setSchedules(schedulesForSession(item));
    setTripId(item.trip_id || "");
    setDestination(item.destination || ""); setStartDate(item.start_date || ""); setEndDate(item.end_date || "");
    setBudget(item.budget == null ? "" : String(item.budget)); setPace(item.pace_level); setPreference(item.preference_text || "");
    setOrigin(item.context?.planning_inputs?.origin ?? "");
    setCompanionsText((item.context?.planning_inputs?.companions ?? []).join("、"));
    setConstraintsText((item.context?.planning_inputs?.hard_constraints ?? []).join("、"));
    setError(""); setNotice(""); setHistoryOpen(false); setStage("chat");
    router.replace(`/advisor?sessionId=${item.id}`);
  }

  function chooseSetup(choice: "trip" | "ideas") {
    setSetupChoice(choice);
    if (choice === "trip") {
      setMode("existing");
      if (!tripId) setTripId(selectedTripId || trips[0]?.id || "");
    } else {
      setMode("discover"); setTripId(""); setDestination(""); setStartDate(""); setEndDate(""); setBudget("");
    }
  }

  return (
    <main className="shell advisor-page advisor-live">
      <header className="advisor-heading">
        <div><h1>和旅行顾问一起，<br className="advisor-title-break" />把想法变成行程。</h1><p>先选择一段旅行，或从一个想法开始。对话和草案会自动保存，正式行程只在你确认后写入。</p></div>
        {stage === "chat" && <div className="advisor-chat-toolbar"><button type="button" className="secondary" onClick={startNewConversation}>＋ 新建对话</button><button type="button" className="secondary" onClick={() => setHistoryOpen((value) => !value)}>{historyOpen ? "收起历史" : "找之前的对话"}</button></div>}
      </header>
      {error && stage !== "setup" && <p className="advisor-live-error" role="alert">{error}</p>}
      {notice && stage !== "setup" && <p role="status" className="advisor-action-notice">{notice}</p>}

      {stage === "home" && <section className="advisor-launch panel">
        <div className="advisor-launch-copy"><span className="advisor-welcome-mark" aria-hidden="true">✳</span><div><p className="eyebrow">你的旅行，从这里开始</p><h2>今天想聊哪一段旅程？</h2><p>可以围绕已创建的旅行继续规划，也可以先聊聊想去哪里、喜欢什么。</p></div></div>
        <div className="advisor-launch-actions"><button type="button" className="primary" onClick={startNewConversation}>开始新对话 <span>↗</span></button><button type="button" className="secondary" onClick={() => document.getElementById("advisor-history")?.scrollIntoView({ behavior: "smooth", block: "start" })}>找之前的对话 ↓</button></div>
        <section className="advisor-home-history" id="advisor-history"><div className="advisor-panel-title"><div><h2>之前的对话</h2><p>打开后可以继续补充条件、挑选地点或确认草案。</p></div><span className="social-count">{sessions.length}</span></div>{sessionList}</section>
      </section>}

      {stage === "setup" && <section className="advisor-start panel" id="advisor-start">
        <div className="advisor-panel-title"><div><p className="eyebrow">新对话 · 第 1 步</p><h2>先选择这段旅行</h2><p>选择已有旅行会自动读取目的地、日期和计划状态；没有旅行也可以从想法开始，之后再创建行程。</p></div><button type="button" className="secondary" onClick={() => setStage("home")}>返回</button></div>
        <div className="advisor-entry-choice" role="group" aria-label="选择旅行来源">
          <button type="button" className={setupChoice === "trip" ? "is-selected" : ""} aria-pressed={setupChoice === "trip"} onClick={() => chooseSetup("trip")}><span>🧭</span><strong>关联已有旅行</strong><small>无论是否已有行程计划，都可以接着咨询</small></button>
          <button type="button" className={setupChoice === "ideas" ? "is-selected" : ""} aria-pressed={setupChoice === "ideas"} onClick={() => chooseSetup("ideas")}><span>✦</span><strong>我还没有旅行</strong><small>先聊聊旅行想法，地点和日期之后再确定</small></button>
        </div>
        {runtimeStatus && <p className={`advisor-provider-status${runtimeStatus.llm_status !== "CONFIGURED" ? " is-local" : ""}`} role="status">
          {runtimeStatus.llm_status === "CONFIGURED"
            ? `模型已配置：${runtimeStatus.model_name} · 单轮输出上限 ${runtimeStatus.output_token_limit} tokens。上游连通状态会在实际请求后显示。`
            : runtimeStatus.llm_status === "NOT_CONFIGURED"
              ? "模型未配置 · 将使用本地规则识别与模板回复。"
              : "暂时无法读取模型配置状态；请检查 API 服务，并确认它已更新至当前版本。"}
        </p>}
        <form className="advisor-live-form" onSubmit={startSession}>
          {setupChoice === "trip" ? <label className="advisor-setup-trip">选择要咨询的旅行<select value={tripId} onChange={(event) => setTripId(event.target.value)} required>
            <option value="">选择一段旅行</option>{trips.map((trip) => <option key={trip.id} value={trip.id}>{tripOptionLabel(trip, trips)} · {trip.has_plan ? "已有行程" : "尚无行程"}</option>)}
          </select>{selectedTrip && <small className="advisor-trip-status-note"><strong>{selectedTrip.has_plan ? "已有行程计划" : "这段旅行还没有行程计划"}</strong> · {selectedTrip.destination} · {selectedTrip.start_date} 至 {selectedTrip.end_date} · 预算 {selectedTrip.budget == null ? "未设置" : `${selectedTrip.budget} 元`} · 节奏 {selectedTrip.pace_level}/5。{selectedTrip.has_plan ? "顾问会读取现有计划，供你调整。" : "顾问可以从空白计划开始安排。"}</small>}</label> : setupChoice === "ideas" ? <div className="advisor-ideas-note"><strong>从想法开始，不必先创建旅行</strong><p>创建对话后，可以直接告诉顾问“想去海边”“想吃当地小吃”或任何还不成熟的念头。确认行程草案时再创建正式旅行。</p></div> : <div className="advisor-ideas-note"><strong>请选择一种开始方式</strong><p>关联已有旅行，或选择还没有旅行、先聊旅行想法。</p></div>}
          {(setupChoice === "trip" || setupChoice === "ideas") && <><label>旅行节奏<select value={pace} onChange={(event) => setPace(Number(event.target.value))}><option value={1}>很轻松</option><option value={2}>轻松</option><option value={3}>适中</option><option value={4}>紧凑</option><option value={5}>很紧凑</option></select></label>
          <label className="advisor-live-preference">先告诉顾问一点偏好（可选）<input value={preference} onChange={(event) => setPreference(event.target.value)} placeholder="比如：喜欢美食、慢节奏，或带老人同行" /></label></>}
          <button type="submit" className="primary" disabled={!canCreateSession || busy || (setupChoice === "trip" && trips.length === 0)}>{busy ? "处理中…" : setupChoice === "trip" ? "进入这段旅行的对话" : "开始聊旅行想法"}</button>
        </form>
        {setupChoice === "trip" && trips.length === 0 && <p className="advisor-empty">你还没有创建旅行。可以先切换到“我还没有旅行”开始聊天，也可以<Link href="/trips/new">先创建一段旅行</Link>。</p>}
      </section>}

      {stage === "chat" && <>{historyOpen && <section className="advisor-live-history panel"><div className="advisor-panel-title"><div><h2>切换到之前的对话</h2><p>选择一段已保存的顾问会话继续。</p></div></div>{sessionList}</section>}<div className="advisor-live-columns">
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
              {active.mode === "discover" && !active.destination && <button type="button" className="secondary advisor-recommend-button" onClick={recommendDestinations} disabled={busy || !canCompareDestinations}>
                {busy ? "正在查询地图…" : "按偏好比较目的地"}
              </button>}
              {active.mode === "discover" && !active.destination && <small className="advisor-context-note">比较目的地需要填写偏好、出发地和日期，并配置高德地图。若信息还不全，可以先在对话里说一点想法，顾问会追问最关键的一项。最多对 6 座城市执行 12 次高德地点搜索；只比较地图地点证据，不含城市间交通、价格或季节评分。</small>}
            </details>}
            {active.trip_id && <div className="advisor-context-card">
              <strong>当前计划与已确认记忆</strong>
              {active.context?.current_plan ? <p>计划 v{active.context.current_plan.version} · {active.context.current_plan.stops.length} 个地点 · {active.context.current_plan.warnings.length} 条原计划提示</p> : <p>这趟旅行还没有已保存计划。</p>}
              {active.context?.confirmed_memories?.length ? <ul>{active.context.confirmed_memories.slice(0, 4).map((memory) => <li key={memory.id}>{memory.text}</li>)}</ul> : <small>没有已确认的旅行记忆</small>}
            </div>}
            <div className="advisor-messages" aria-live="polite">
              {active.messages.map((item) => <article className={`advisor-message ${item.role}`} key={item.id}>
                <span>{item.role === "user" ? "你" : "顾问"}</span><p>{item.content}</p>
                {item.role === "assistant" && typeof item.payload?.agent_action === "string" ? <small className="advisor-message-source">
                  {({ SCHEDULE_PLAN: "行动 · 基于当前计划排时间", SCHEDULE_BLOCKED: "行动 · 检查排程条件", RECOMMEND_PLACES: "行动 · 按偏好搜索推荐", SEARCH_PLACES: "行动 · 地图搜索与核验", UPDATE_DRAFT_SELECTION: "行动 · 更新草案候选", ASK_CLARIFICATION: "行动 · 追问必要信息" } as Record<string, string>)[item.payload.agent_action] ?? "行动 · 处理旅行请求"}
                  {Array.isArray(item.payload.tool_calls) && item.payload.tool_calls.length > 0 ? ` · 调用 ${item.payload.tool_calls.length} 次地图工具` : ""}
                </small> : null}
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
            <div className="advisor-panel-title"><div><h2>地图地点候选</h2><p>选择地点后逐个确认日期和开始时间；地图提供的游览时长仅作估算。</p></div><button type="button" className="secondary" disabled={!active || !selected.length || !schedulesComplete || Boolean(active.context?.planning_state?.missing_required?.length) || busy || active.status !== "ACTIVE"} onClick={generateDraft}>{active?.draft ? `按当前选择重新生成（${selected.length}）` : `检查并生成草案（${selected.length}）`}</button></div>
            {active?.candidates.length ? <div className="advisor-live-candidates">{active.candidates.map((candidate) => {
              const schedule = schedules[candidate.id] ?? { day: "", start_time: "" };
              return <article className="advisor-live-candidate" key={candidate.id}>
                <label className="advisor-live-candidate-choice"><input type="checkbox" checked={selected.includes(candidate.id)} onChange={() => toggleCandidate(candidate.id)} disabled={active.status !== "ACTIVE"} />
                  <span><strong>{candidate.name}{candidate.in_current_plan ? <em>当前计划</em> : null}{candidate.popularity_score != null && <em>地图评分 {candidate.popularity_score.toFixed(1)}</em>}</strong><small>{candidate.verification_status === "VERIFIED" ? (candidate.provider === "amap" ? "高德地图已核验" : "地图服务已核验") : candidate.provider === "fake" ? "模拟地点数据" : "已保存地点"} · {candidate.category} · {candidate.address || "地址未提供"} · 地图建议游览约 {candidate.duration_min} 分钟</small>{candidate.suggestion && <small>{candidate.suggestion}</small>}</span>
                </label>
                {selected.includes(candidate.id) && <div className="advisor-candidate-schedule"><label>安排日期<select value={schedule.day} onChange={(event) => setSchedules((current) => ({ ...current, [candidate.id]: { ...schedule, day: event.target.value } }))}><option value="">请选择日期</option>{travelDays.map((day, index) => <option key={day} value={day}>第 {index + 1} 天 · {day}</option>)}</select></label><label>开始时间<input type="time" value={schedule.start_time} onChange={(event) => setSchedules((current) => ({ ...current, [candidate.id]: { ...schedule, start_time: event.target.value } }))} /></label>{candidate.explicit_day || candidate.explicit_start_time ? <small>根据你在对话中的明确表述预填，可自行修改。</small> : <small>尚未从对话中读到明确时间，请你选择；系统不会替你默认。</small>}</div>}
              </article>;
            })}</div> : <p className="advisor-empty">{active && !active.destination ? "先在旅行条件中补充目的地城市，再按偏好搜索候选。" : "还没有地点候选。你可以说“推荐几个热门地点”，也可以告诉顾问城市和兴趣类别。"}</p>}
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

      </>}

      {(active?.draft || active?.confirmed_trip_id) && <footer className="advisor-confirm-bar">
        <div><strong>{active?.status === "CONFIRMED" ? "草案已保存" : "确认后写入正式旅行"}</strong><p>保存时会创建或更新 Wishlist 与 Plan，重复确认不会创建重复计划。</p></div>
        {active?.draft && draftIsStale && <span role="status" className="advisor-live-error">对话、节奏或选点已变化，请重新生成草案</span>}
        {active?.status === "CONFIRMED" && active.confirmed_trip_id ? <Link className="primary" href={`/trips/${active.confirmed_trip_id}/plan`}>查看已保存行程</Link> : <button type="button" className="primary" onClick={confirmDraft} disabled={!active?.draft || draftIsStale || busy}>{busy ? "保存中…" : "确认并保存行程"}</button>}
      </footer>}
    </main>
  );
}
