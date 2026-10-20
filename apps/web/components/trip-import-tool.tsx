"use client";

import Link from "next/link";
import { ChangeEvent, FormEvent, useEffect, useRef, useState } from "react";
import { confirmCandidates, importContent, recognizeContentImage, type ImportedContent, type ImportedGuideFact } from "../lib/api";

type TripImportToolProps = {
  tripId: string;
  onClose?: () => void;
  onImported?: () => void;
};

function ImportForm({ tripId, onImported }: Pick<TripImportToolProps, "tripId" | "onImported">) {
  const [text, setText] = useState("");
  const [content, setContent] = useState<ImportedContent | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [selectedFactIds, setSelectedFactIds] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [ocrConfidence, setOcrConfidence] = useState<number | null>(null);

  async function recognizeImage(event: ChangeEvent<HTMLInputElement>) {
    const input = event.currentTarget;
    const file = input.files?.[0];
    if (!file) return;
    setBusy(true); setError(""); setContent(null); setSelected([]);
    try {
      const result = await recognizeContentImage(tripId, file);
      if (!result.text.trim()) {
        setError("没有识别到文字。可以换一张更清晰的图片，或直接粘贴文字。");
        setOcrConfidence(null);
      } else {
        setText(result.text);
        setOcrConfidence(result.confidence);
      }
    } catch (err) { setError(err instanceof Error ? err.message : "图片文字识别失败"); }
    finally { setBusy(false); input.value = ""; }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError("");
    try {
      const response = await importContent(tripId, text);
      const result = { ...response, guide_facts: response.guide_facts ?? [] };
      setContent(result); setSelected([]);
      setSelectedFactIds(result.guide_facts.map((fact) => fact.id));
    }
    catch (err) { setError(err instanceof Error ? err.message : "导入失败"); }
    finally { setBusy(false); }
  }

  async function confirm() {
    if (!content || selected.length === 0) return;
    setBusy(true); setError("");
    try {
      const response = await confirmCandidates(content.id, selected, selectedFactIds);
      setContent({ ...response, guide_facts: response.guide_facts ?? [] }); setSelected([]); onImported?.();
    }
    catch (err) { setError(err instanceof Error ? err.message : "确认失败"); }
    finally { setBusy(false); }
  }

  function toggle(id: string) {
    setSelected((current) => current.includes(id) ? current.filter((item) => item !== id) : [...current, id]);
  }

  return <>
    <div className="trip-import-intro"><p className="eyebrow">导入攻略</p><h2>把攻略里的地点和实用细节留下来</h2><p>识别地点后匹配地图候选；同时提取购票、推荐时段、美食、路线和出行提示。核对原文依据后，由你选择哪些要点附加到已确认地点。</p></div>
    <section className="trip-import-image layer">
      <h3>从攻略截图识别文字</h3>
      <label>选择图片（JPEG、PNG 或 WebP，最多 10 MB）<input type="file" accept="image/jpeg,image/png,image/webp" disabled={busy} onChange={(event) => void recognizeImage(event)} /></label>
      <p>图片在本地 OCR 流程中临时处理，不保存原图，也不会发送给 LLM。识别出的文字会填入下方文本框供你校对。</p>
    </section>
    <form className="trip-import-form trip-form" onSubmit={submit}>
      <label>{ocrConfidence === null ? "攻略原文" : `识别文字（OCR 平均置信度 ${Math.round(ocrConfidence * 100)}%，请校对）`}<textarea required rows={8} maxLength={20000} value={text} onChange={(event) => setText(event.target.value)} /></label>
      <button className="primary" disabled={busy}>{busy ? "正在识别…" : "提取地点"}</button>
    </form>
    {error && <p className="trip-import-error" role="alert">{error}</p>}
    {content && <section className="trip-import-results"><h3>攻略识别 · {content.status}</h3>{content.error && <p role="status">{content.error}</p>}<blockquote>{content.text}</blockquote>
      {!!content.guide_facts.length && <section className="trip-guide-facts" aria-label="攻略细节">
        <div><h3>识别到的攻略细节</h3><p>要点已保存在本次导入记录。勾选的内容会附加到对应已确认地点的旅行备注；请按原文核对，模型提取不等于事实核验。</p></div>
        {content.guide_facts.map((fact: ImportedGuideFact) => <label className="trip-guide-fact" key={fact.id}>
          <input type="checkbox" checked={selectedFactIds.includes(fact.id) || Boolean(fact.saved)} disabled={Boolean(fact.saved) || busy} onChange={() => setSelectedFactIds((current) => current.includes(fact.id) ? current.filter((id) => id !== fact.id) : [...current, fact.id])} />
          <span><strong>{fact.title}</strong><small>{fact.related_places.length ? `关联地点：${fact.related_places.join("、")}` : "攻略整体提示"} · 提取置信度 {Math.round(fact.confidence * 100)}%</small><span>{fact.content}</span>{fact.evidence && <small>原文依据：{fact.evidence}</small>}{fact.saved && <small>已保存到旅行地点备注</small>}</span>
        </label>)}
      </section>}
      {!content.entities.length && !content.guide_facts.length && content.status !== "FAILED" && <p>没有提取到地点或可记录的攻略细节。可以检查文字后重新提取。</p>}
      {content.entities.map((entity) => <article className="layer" key={entity.id}>
        <span className="index">置信度 {Math.round(entity.confidence * 100)}%</span><h3>{entity.name}</h3>
        {entity.suggestion && <p>{entity.suggestion}</p>}
        {entity.evidence ? <blockquote>原文依据：{entity.evidence}</blockquote> : <p>模型未提供可引用的原文依据，请对照上方攻略确认。</p>}
        {entity.candidates.length === 0 && <p>没有找到地图候选，可以稍后手动添加。</p>}
        {entity.candidates.map((candidate) => <label key={candidate.id} className="candidate-row">
          <input type="checkbox" checked={selected.includes(candidate.id) || candidate.confirmed} disabled={candidate.confirmed || busy} onChange={() => toggle(candidate.id)} />
          {candidate.name} · {candidate.address} · 匹配度 {Math.round(candidate.score * 100)}%{candidate.confirmed ? " · 已加入" : ""}
        </label>)}
      </article>)}
      <button className="primary" disabled={busy || selected.length === 0} onClick={() => void confirm()}>确认 {selected.length} 个地点并保存所选攻略要点</button>
    </section>}
  </>;
}

export function TripImportTool({ tripId, onClose, onImported }: TripImportToolProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    if (onClose) dialogRef.current?.showModal();
    return () => dialogRef.current?.close();
  }, [onClose]);

  if (onClose) {
    return <dialog className="trip-import-dialog" ref={dialogRef} onCancel={onClose} onClick={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <section className="trip-import-dialog-panel">
        <header className="trip-import-dialog-header"><div><p className="eyebrow">旅行工具</p><strong>导入攻略</strong></div><button type="button" className="moment-dialog-close" aria-label="关闭导入窗口" onClick={onClose}>×</button></header>
        <div className="trip-import-dialog-body"><ImportForm tripId={tripId} onImported={onImported} /></div>
      </section>
    </dialog>;
  }

  return <main className="shell trip-import-page">
    <nav className="nav"><Link className="trip-import-back" href={`/trips/${tripId}`}>← 返回旅行概览</Link><span className="status">内容导入</span></nav>
    <ImportForm tripId={tripId} onImported={onImported} />
  </main>;
}
