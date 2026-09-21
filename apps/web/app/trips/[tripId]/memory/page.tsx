"use client";

import Link from "next/link";
import { FormEvent, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { deleteMemory, listMemories, submitFeedback, updateMemory, type Memory } from "../../../../lib/api";

export default function MemoryPage() {
  const tripId = useParams<{ tripId: string }>().tripId;
  const [text, setText] = useState("");
  const [memories, setMemories] = useState<Memory[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => { void listMemories(tripId).then(setMemories).catch((err) => setError(err.message)); }, [tripId]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError("");
    try { await submitFeedback(tripId, text); setText(""); setMemories(await listMemories(tripId)); }
    catch (err) { setError(err instanceof Error ? err.message : "提交失败"); }
    finally { setBusy(false); }
  }
  async function act(id: string, action: "confirm" | "delete") {
    setBusy(true); setError("");
    try {
      if (action === "confirm") await updateMemory(tripId, id, { confirmed: true });
      else await deleteMemory(tripId, id);
      setMemories(await listMemories(tripId));
    } catch (err) { setError(err instanceof Error ? err.message : "操作失败"); }
    finally { setBusy(false); }
  }
  return <main className="shell">
    <nav className="nav"><Link className="brand" href={`/trips/${tripId}`}>TravelPilot</Link><span className="status">Traveler Memory</span></nav>
    <section className="hero"><p className="eyebrow">Feedback</p><h1>旅行记忆</h1>
      <p className="lede">反馈先成为待确认记忆；确认后才进入后续 Agent 的旅行上下文。</p></section>
    <form className="trip-form" onSubmit={submit}><label>反馈<textarea required minLength={2} rows={4} value={text} onChange={(event) => setText(event.target.value)} placeholder="例如：今天很累，希望下午少安排景点" /></label>
      <button className="primary" disabled={busy}>提交反馈</button></form>
    {error ? <p role="alert">{error}</p> : null}
    <div className="wishlist-list">{memories.map((memory) => <article className="layer" key={memory.id}>
      <span className="index">{memory.memory_type} · {memory.confirmed ? "已确认" : "待确认"}</span>
      <h2>{memory.text}</h2><div className="actions">
        {!memory.confirmed ? <button className="primary" disabled={busy} onClick={() => void act(memory.id, "confirm")}>确认记忆</button> : null}
        <button className="secondary" disabled={busy} onClick={() => void act(memory.id, "delete")}>删除</button>
      </div>
    </article>)}</div>
  </main>;
}
