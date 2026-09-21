"use client";

import Link from "next/link";
import { FormEvent, useState } from "react";
import { useParams } from "next/navigation";
import { confirmCandidates, importContent, type ImportedContent } from "../../../../lib/api";

export default function ImportPage() {
  const tripId = useParams<{ tripId: string }>().tripId;
  const [text, setText] = useState("");
  const [content, setContent] = useState<ImportedContent | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError("");
    try { setContent(await importContent(tripId, text)); setSelected([]); }
    catch (err) { setError(err instanceof Error ? err.message : "导入失败"); }
    finally { setBusy(false); }
  }
  async function confirm() {
    if (!content || selected.length === 0) return;
    setBusy(true); setError("");
    try { setContent(await confirmCandidates(content.id, selected)); setSelected([]); }
    catch (err) { setError(err instanceof Error ? err.message : "确认失败"); }
    finally { setBusy(false); }
  }
  function toggle(id: string) {
    setSelected((current) => current.includes(id) ? current.filter((item) => item !== id) : [...current, id]);
  }

  return <main className="shell">
    <nav className="nav"><Link className="brand" href={`/trips/${tripId}`}>TravelPilot</Link><span className="status">内容导入</span></nav>
    <section className="hero"><p className="eyebrow">Import</p><h1>粘贴旅行攻略</h1>
      <p className="lede">系统提取地点候选。只有你确认的地点才会进入 Wishlist。</p></section>
    <form className="trip-form" onSubmit={submit}><label>攻略原文<textarea required rows={8} value={text} onChange={(event) => setText(event.target.value)} /></label>
      <button className="primary" disabled={busy}>提取地点</button></form>
    {error ? <p role="alert">{error}</p> : null}
    {content ? <section><h2>抽取结果 · {content.status}</h2><p>{content.error}</p><blockquote>{content.text}</blockquote>
      {content.entities.map((entity) => <article className="layer" key={entity.id}>
        <span className="index">置信度 {Math.round(entity.confidence * 100)}%</span><h2>{entity.name}</h2>
        {entity.suggestion ? <p>{entity.suggestion}</p> : null}
        {entity.candidates.length === 0 ? <p>没有找到地图候选，请手动添加。</p> : null}
        {entity.candidates.map((candidate) => <label key={candidate.id} className="candidate-row">
          <input type="checkbox" checked={selected.includes(candidate.id) || candidate.confirmed} disabled={candidate.confirmed || busy} onChange={() => toggle(candidate.id)} />
          {candidate.name} · {candidate.address} · 匹配度 {Math.round(candidate.score * 100)}%
          {candidate.confirmed ? " · 已加入" : ""}
        </label>)}
      </article>)}
      <button className="primary" disabled={busy || selected.length === 0} onClick={() => void confirm()}>确认并加入 Wishlist</button>
    </section> : null}
  </main>;
}
