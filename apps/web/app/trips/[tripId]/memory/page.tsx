"use client";

import Link from "next/link";
import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "next/navigation";
import { deleteMemory, getTrip, listMemories, submitFeedback, updateMemory, type Memory, type Trip } from "../../../../lib/api";

const memoryPrompts = ["今天最喜欢的时刻是…", "下次旅行我想…", "这个地方让我觉得…"];

export default function MemoryPage() {
  const tripId = useParams<{ tripId: string }>().tripId;
  const [trip, setTrip] = useState<Trip | null>(null);
  const [text, setText] = useState("");
  const [memories, setMemories] = useState<Memory[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [composerOpen, setComposerOpen] = useState(false);
  const composerDialog = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    let active = true;
    void Promise.all([getTrip(tripId), listMemories(tripId)]).then(([tripRow, rows]) => {
      if (!active) return;
      setTrip(tripRow); setMemories(rows);
    }).catch((cause) => { if (active) setError(cause instanceof Error ? cause.message : "旅行记忆暂时无法加载"); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [tripId]);

  useEffect(() => {
    const dialog = composerDialog.current;
    if (!dialog) return;
    if (composerOpen && !dialog.open) dialog.showModal();
    else if (!composerOpen && dialog.open) dialog.close();
  }, [composerOpen]);

  async function refresh() { setMemories(await listMemories(tripId)); }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError("");
    try { await submitFeedback(tripId, text); setText(""); setComposerOpen(false); await refresh(); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "提交失败"); }
    finally { setBusy(false); }
  }

  async function act(id: string, action: "confirm" | "delete") {
    setBusy(true); setError("");
    try {
      if (action === "confirm") await updateMemory(tripId, id, { confirmed: true });
      else await deleteMemory(tripId, id);
      await refresh();
    } catch (cause) { setError(cause instanceof Error ? cause.message : "操作失败"); }
    finally { setBusy(false); }
  }

  const pending = useMemo(() => memories.filter((memory) => !memory.confirmed), [memories]);
  const confirmed = useMemo(() => memories.filter((memory) => memory.confirmed), [memories]);

  if (loading) return <main className="shell"><p className="page-loading">正在翻开这段旅程…</p></main>;

  return <main className="shell memory-page">
    <header className="memory-heading">
      <div><Link className="context-backlink" href={`/trips/${tripId}`}>← {trip?.destination ?? "返回旅行"}</Link><h1>把感受留在这段旅程里。</h1><p>说说今天的节奏、喜欢的地方，或下次想怎么安排。记忆会先等你确认，再成为之后规划的参考。</p></div>
      <div className="memory-heading-actions"><button type="button" className="primary" onClick={() => setComposerOpen(true)}>留下一句话 <span aria-hidden="true">↗</span></button><div className="memory-orbit" aria-hidden="true"><span>✳</span><i /><i /><i /></div></div>
    </header>

    {error && <p className="memory-alert" role="alert">{error}</p>}

    <dialog className="memory-dialog" ref={composerDialog} onClose={() => setComposerOpen(false)} onClick={(event) => { if (event.target === event.currentTarget) setComposerOpen(false); }} aria-labelledby="memory-composer-title">
      <section className="memory-compose">
        <div className="memory-compose-top"><div><span className="memory-step">留下一句话</span><span className="memory-date">{trip?.start_date} — {trip?.end_date}</span></div><button type="button" className="moment-dialog-close" onClick={() => setComposerOpen(false)} aria-label="关闭记录窗口">×</button></div>
        <h2 id="memory-composer-title" className="memory-dialog-title">这段旅程，想记住什么？</h2>
        <form onSubmit={submit}>
          <label className="memory-feedback-label">写下你的感受<textarea required minLength={2} maxLength={2000} rows={6} value={text} onChange={(event) => setText(event.target.value)} placeholder="例如：今天很累，希望下午少安排景点" /></label>
          <div className="memory-prompt-list" aria-label="写作提示">{memoryPrompts.map((prompt) => <button type="button" key={prompt} onClick={() => setText((current) => current ? `${current}${current.endsWith(" ") ? "" : " "}${prompt}` : prompt)}>{prompt}</button>)}</div>
          <div className="memory-submit-row"><small>提交后会生成一条待确认记忆，你可以随时修改或删除。</small><button className="primary" disabled={busy}>{busy ? "正在整理…" : "保存这段感受"}<span aria-hidden="true">→</span></button></div>
        </form>
        <div className="memory-privacy-note"><span aria-hidden="true">⌑</span><p>这些旅行记忆只属于你。只有确认后，AI 旅行顾问才会在之后的规划中参考。</p></div>
      </section>
    </dialog>

    <div className="memory-layout">
      <section className="memory-collection" aria-label="旅行记忆列表">
        <header className="memory-collection-heading"><div><span className="memory-step">这段旅程的回声</span><h2>{memories.length ? `${memories.length} 条记忆` : "从第一条开始"}</h2></div><span className="memory-count" aria-label={`共 ${memories.length} 条`}>{String(memories.length).padStart(2, "0")}</span></header>
        {!memories.length ? <div className="memory-empty"><div className="memory-empty-thread" aria-hidden="true"><span>●</span><i /><span>○</span></div><h3>旅行记忆还空着</h3><p>一条真实的感受，比完整的游记更容易记住当时。</p><button className="secondary" type="button" onClick={() => setComposerOpen(true)}>现在记下一句话</button></div> : <>
          {pending.length > 0 && <section className="memory-group"><h3><span className="memory-state-dot is-pending" />等你确认 <small>{pending.length}</small></h3><ol>{pending.map((memory, index) => <li className="memory-entry" key={memory.id}><span className="memory-entry-index">{String(index + 1).padStart(2, "0")}</span><div className="memory-entry-body"><small>{memory.memory_type} · 待确认</small><blockquote>{memory.text}</blockquote><div className="memory-entry-actions"><button className="primary" disabled={busy} onClick={() => void act(memory.id, "confirm")}>确认记忆</button><button className="memory-delete" disabled={busy} onClick={() => void act(memory.id, "delete")}>删除</button></div></div></li>)}</ol></section>}
          {confirmed.length > 0 && <section className="memory-group"><h3><span className="memory-state-dot is-confirmed" />已经收好 <small>{confirmed.length}</small></h3><ol>{confirmed.map((memory, index) => <li className="memory-entry is-confirmed" key={memory.id}><span className="memory-entry-index">{String(index + 1).padStart(2, "0")}</span><div className="memory-entry-body"><small>{memory.memory_type} · 已确认</small><blockquote>{memory.text}</blockquote><button className="memory-delete" disabled={busy} onClick={() => void act(memory.id, "delete")}>从记忆中删除</button></div></li>)}</ol></section>}
        </>}
      </section>
    </div>
  </main>;
}
