"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { createPlanRun, listPlans, type Plan } from "../../../../lib/api";

export default function PlanPage() {
  const tripId = useParams<{ tripId: string }>().tripId;
  const [plans, setPlans] = useState<Plan[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => { void listPlans(tripId).then(setPlans).catch((err) => setError(err.message)); }, [tripId]);

  async function generate() {
    setBusy(true); setError("");
    try { await createPlanRun(tripId); setPlans(await listPlans(tripId)); }
    catch (err) { setError(err instanceof Error ? err.message : "规划失败"); }
    finally { setBusy(false); }
  }
  const latest = plans[0];
  const days = latest ? Array.from(new Set(latest.nodes.map((node) => node.day))) : [];
  return <main className="shell">
    <nav className="nav"><Link className="brand" href={`/trips/${tripId}`}>TravelPilot</Link><span className="status">计划</span></nav>
    <section className="hero"><p className="eyebrow">Itinerary</p><h1>旅行计划</h1><p className="lede">按真实时长、交通和营业时间生成可追溯的计划版本。</p>
      <button className="primary" disabled={busy} onClick={() => void generate()}>{busy ? "正在规划…" : "生成新版本"}</button>
    </section>
    {error ? <p role="alert">{error}</p> : null}
    {latest ? <><p>Plan v{latest.version} · {latest.status}</p>
      {latest.warnings.map((warning, index) => <p role="status" key={index}>⚠ {warning}</p>)}
      {days.map((day) => <section key={day}><h2>{day}</h2><div className="wishlist-list">{latest.nodes.filter((node) => node.day === day).map((node) => <article className="layer" key={node.id}>
        <span className="index">{node.start_time.slice(0, 5)}–{node.end_time.slice(0, 5)} · 优先级 {node.priority}</span>
        <h2>{node.name}</h2><p>{node.status}</p>{node.note ? <p>{node.note}</p> : null}
      </article>)}</div></section>)}
      <div className="actions"><Link className="secondary" href={`/trips/${tripId}/events`}>模拟事件并查看 Diff</Link></div>
      <h2>历史版本</h2>{plans.map((plan) => <p key={plan.id}>v{plan.version} · {plan.created_at}</p>)}
    </> : <p>暂无计划，请先添加 Wishlist 地点。</p>}
  </main>;
}
