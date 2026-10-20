"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { ApiError, listTrips, listTripLifecyclePrompts, updateTripLifecyclePrompt, type Trip, type TripReminder } from "../../lib/api";
import { DashboardJourney } from "../../components/dashboard-journey";

export default function TripsPage() {
  const [filter, setFilter] = useState<"ALL" | Trip["lifecycle_status"]>("ALL");
  const [trips, setTrips] = useState<Trip[]>([]);
  const [prompts, setPrompts] = useState<TripReminder[]>([]);
  const [promptBusy, setPromptBusy] = useState("");
  const [promptNotice, setPromptNotice] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [errorCode, setErrorCode] = useState("");

  async function loadTrips() {
    try {
      setLoading(true); setError(""); setErrorCode("");
      const [nextTrips] = await Promise.all([listTrips(), listTripLifecyclePrompts().then(setPrompts).catch(() => setPrompts([]))]);
      setTrips(nextTrips);
    } catch (cause) {
      if (cause instanceof ApiError) { setError(cause.message); setErrorCode(cause.code); }
      else setError(cause instanceof Error ? cause.message : "加载旅行失败");
    } finally { setLoading(false); }
  }

  useEffect(() => { void loadTrips(); }, []);

  const filteredTrips = useMemo(() => filter === "ALL" ? trips : trips.filter((trip) => trip.lifecycle_status === filter), [filter, trips]);
  const counts = useMemo(() => ({
    ALL: trips.length,
    UPCOMING: trips.filter((trip) => trip.lifecycle_status === "UPCOMING").length,
    ONGOING: trips.filter((trip) => trip.lifecycle_status === "ONGOING").length,
    COMPLETED: trips.filter((trip) => trip.lifecycle_status === "COMPLETED").length,
  }), [trips]);

  async function handlePrompt(prompt: TripReminder, action: "DONE" | "LATER" | "DISMISS") {
    setPromptBusy(prompt.id); setPromptNotice("");
    try {
      await updateTripLifecyclePrompt(prompt.id, action);
      setPrompts((current) => current.filter((item) => item.id !== prompt.id));
      if (action === "DONE") window.location.assign(prompt.kind === "AFTER_TRIP" ? `/trips/${prompt.trip_id}/memory` : `/advisor?tripId=${prompt.trip_id}`);
      else setPromptNotice(action === "LATER" ? "好的，明天再提醒你。" : "这条提醒已关闭。 ");
    } catch (cause) { setPromptNotice(cause instanceof Error ? cause.message : "提醒暂时无法更新，请重试。"); }
    finally { setPromptBusy(""); }
  }

  const lifecycleLabel = (value: Trip["lifecycle_status"]) => ({ UPCOMING: "即将出发", ONGOING: "旅途中", COMPLETED: "已完成" })[value];

  return <main className="shell dashboard-page">
    <header className="dashboard-welcome">
      <div><h1>下一段旅行，<br className="desktop-break" />从一张地图开始。</h1><p>找到想去的地方，慢慢排成自己的节奏。已经出发过的旅程，也值得好好记下来。</p></div>
      <div className="dashboard-welcome-actions"><Link className="primary" href="/advisor">开始规划 <span aria-hidden="true">↗</span></Link></div>
    </header>

    {loading && <div className="dashboard-loading" role="status">正在整理你的旅程…</div>}
    {!loading && error && <section className="dashboard-error" role="alert"><div><h2>旅行暂时没有加载出来</h2><p>{error}{errorCode ? ` · ${errorCode}` : ""}</p></div><button className="secondary" type="button" onClick={() => void loadTrips()}>重试</button></section>}
    {!loading && !error && prompts.length > 0 && <section className="trip-lifecycle-prompts" aria-label="旅行提醒"><div className="trip-lifecycle-prompts-heading"><span>TRAVEL NOTES</span><h2>为下一段旅程留一点准备时间</h2></div>{prompts.map((prompt) => <article className="trip-lifecycle-prompt" key={prompt.id}>
      <div className="trip-lifecycle-prompt-mark" aria-hidden="true">{prompt.kind === "AFTER_TRIP" ? "✳" : "⌖"}</div><div className="trip-lifecycle-prompt-copy"><strong>{prompt.destination} · {prompt.kind === "AFTER_TRIP" ? "旅程刚刚结束" : prompt.kind === "PLAN_2D" ? "还有两天出发" : "一周后出发"}</strong><p>{prompt.kind === "AFTER_TRIP" ? "要不要挑一张照片，写下这趟旅程里最想记住的瞬间？" : "行程还没有计划。可以先和旅行顾问梳理路线、用餐和出发准备。"}</p></div><div className="trip-lifecycle-prompt-actions"><button className="primary" type="button" disabled={promptBusy === prompt.id} onClick={() => void handlePrompt(prompt, "DONE")}>{prompt.kind === "AFTER_TRIP" ? "去写旅行记忆" : "开始做计划"}</button><button className="secondary" type="button" disabled={promptBusy === prompt.id} onClick={() => void handlePrompt(prompt, "LATER")}>明天提醒</button><button className="trip-lifecycle-dismiss" type="button" disabled={promptBusy === prompt.id} onClick={() => void handlePrompt(prompt, "DISMISS")}>关闭</button></div>
    </article>)}</section>}
    {!loading && promptNotice && <p className="dashboard-feedback" role="status">{promptNotice}</p>}
    {!loading && !error && trips.length > 0 && <>
      <nav className="trip-lifecycle-filters" aria-label="按旅行时间筛选">{(["ALL", "UPCOMING", "ONGOING", "COMPLETED"] as const).map((value) => <button type="button" key={value} aria-pressed={filter === value} className={filter === value ? "is-selected" : ""} onClick={() => setFilter(value)}><span>{value === "ALL" ? "全部" : lifecycleLabel(value)}</span><small>{counts[value]}</small></button>)}</nav>
      {filteredTrips.length > 0 ? <DashboardJourney trips={filteredTrips} /> : <section className="trip-lifecycle-empty"><span aria-hidden="true">✦</span><h2>这里暂时没有{lifecycleLabel(filter as Trip["lifecycle_status"])}的旅行</h2><p>日期状态会随旅行当地日期自动变化，你仍可在“全部”中查看历史安排。</p></section>}
    </>}
    {!loading && !error && trips.length === 0 && <section className="dashboard-first-trip">
      <div><span className="dashboard-first-trip-index">01</span><h2>先从一个想去的地方开始。</h2><p>告诉旅行顾问你喜欢什么，或自己创建一趟旅行。地点、计划和沿途记忆都会留在这里。</p><div className="dashboard-first-trip-actions"><Link className="text-link" href="/trips/new">我已经有计划 →</Link></div></div>
      <div className="dashboard-first-trip-mark" aria-hidden="true"><span>⌖</span><i /><i /><i /></div>
    </section>}

    {!loading && !error && filteredTrips.length > 1 && <section className="journey-index">
      <header className="journey-index-head"><h2>其它旅程</h2><Link href="/trips/new">创建新旅程 <span aria-hidden="true">＋</span></Link></header>
      <div className="journey-index-list">{filteredTrips.slice(1).map((trip) => <Link className="journey-index-row" href={`/trips/${trip.id}`} key={trip.id}>
        <span className="journey-index-dates">{trip.start_date}<small>{trip.end_date}</small></span><strong>{trip.destination}</strong><span className="journey-index-status">{lifecycleLabel(trip.lifecycle_status)}</span><span className="journey-index-arrow" aria-hidden="true">↗</span>
      </Link>)}</div>
    </section>}

    <section className="journal-invitation">
      <img src="/images/journey-dawn.webp" alt="清晨湖面与山峦，旅行灵感图" />
      <div className="journal-invitation-copy"><span>旅途灵感</span><h2>有些瞬间，<br />只想留给未来的自己。</h2><p>写下今天的心情，挑几张喜欢的照片。你可以私藏，也可以分享给好友。</p><Link href="/moments">写一段旅行记录 <span aria-hidden="true">→</span></Link></div>
    </section>
  </main>;
}
