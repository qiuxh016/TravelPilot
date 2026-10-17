"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ApiError, listTrips, type Trip } from "../../lib/api";
import { DashboardJourney } from "../../components/dashboard-journey";

export default function TripsPage() {
  const [trips, setTrips] = useState<Trip[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [errorCode, setErrorCode] = useState("");

  async function loadTrips() {
    try {
      setLoading(true); setError(""); setErrorCode("");
      setTrips(await listTrips());
    } catch (cause) {
      if (cause instanceof ApiError) { setError(cause.message); setErrorCode(cause.code); }
      else setError(cause instanceof Error ? cause.message : "加载旅行失败");
    } finally { setLoading(false); }
  }

  useEffect(() => { void loadTrips(); }, []);

  return <main className="shell dashboard-page">
    <header className="dashboard-welcome">
      <div><h1>下一段旅行，<br className="desktop-break" />从一张地图开始。</h1><p>找到想去的地方，慢慢排成自己的节奏。已经出发过的旅程，也值得好好记下来。</p></div>
      <div className="dashboard-welcome-actions"><Link className="primary" href="/advisor">开始规划 <span aria-hidden="true">↗</span></Link></div>
    </header>

    {loading && <div className="dashboard-loading" role="status">正在整理你的旅程…</div>}
    {!loading && error && <section className="dashboard-error" role="alert"><div><h2>旅行暂时没有加载出来</h2><p>{error}{errorCode ? ` · ${errorCode}` : ""}</p></div><button className="secondary" type="button" onClick={() => void loadTrips()}>重试</button></section>}
    {!loading && !error && trips.length > 0 && <DashboardJourney trips={trips} />}
    {!loading && !error && trips.length === 0 && <section className="dashboard-first-trip">
      <div><span className="dashboard-first-trip-index">01</span><h2>先从一个想去的地方开始。</h2><p>告诉旅行顾问你喜欢什么，或自己创建一趟旅行。地点、计划和沿途记忆都会留在这里。</p><div className="dashboard-first-trip-actions"><Link className="text-link" href="/trips/new">我已经有计划 →</Link></div></div>
      <div className="dashboard-first-trip-mark" aria-hidden="true"><span>⌖</span><i /><i /><i /></div>
    </section>}

    {!loading && !error && trips.length > 1 && <section className="journey-index">
      <header className="journey-index-head"><h2>其它旅程</h2><Link href="/trips/new">创建新旅程 <span aria-hidden="true">＋</span></Link></header>
      <div className="journey-index-list">{trips.slice(1).map((trip) => <Link className="journey-index-row" href={`/trips/${trip.id}`} key={trip.id}>
        <span className="journey-index-dates">{trip.start_date}<small>{trip.end_date}</small></span><strong>{trip.destination}</strong><span className="journey-index-status">{trip.status}</span><span className="journey-index-arrow" aria-hidden="true">↗</span>
      </Link>)}</div>
    </section>}

    <section className="journal-invitation">
      <img src="/images/journey-dawn.webp" alt="清晨湖面与山峦，旅行灵感图" />
      <div className="journal-invitation-copy"><span>旅途灵感</span><h2>有些瞬间，<br />只想留给未来的自己。</h2><p>写下今天的心情，挑几张喜欢的照片。你可以私藏，也可以分享给好友。</p><Link href="/moments">写一段旅行记录 <span aria-hidden="true">→</span></Link></div>
    </section>
  </main>;
}
