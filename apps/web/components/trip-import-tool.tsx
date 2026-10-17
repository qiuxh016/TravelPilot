"use client";

import Link from "next/link";
import { ChangeEvent, FormEvent, useEffect, useRef, useState } from "react";
import { confirmCandidates, importContent, recognizeContentImage, type ImportedContent } from "../lib/api";

type TripImportToolProps = {
  tripId: string;
  onClose?: () => void;
  onImported?: () => void;
};

function ImportForm({ tripId, onImported }: Pick<TripImportToolProps, "tripId" | "onImported">) {
  const [text, setText] = useState("");
  const [content, setContent] = useState<ImportedContent | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
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
    try { setContent(await importContent(tripId, text)); setSelected([]); }
    catch (err) { setError(err instanceof Error ? err.message : "导入失败"); }
    finally { setBusy(false); }
  }

  async function confirm() {
    if (!content || selected.length === 0) return;
    setBusy(true); setError("");
    try { setContent(await confirmCandidates(content.id, selected)); setSelected([]); onImported?.(); }
    catch (err) { setError(err instanceof Error ? err.message : "确认失败"); }
    finally { setBusy(false); }
  }

  function toggle(id: string) {
    setSelected((current) => current.includes(id) ? current.filter((item) => item !== id) : [...current, id]);
  }

  return <>
    <div className="trip-import-intro"><p className="eyebrow">导入攻略</p><h2>把攻略里的地点，收进这段旅行</h2><p>粘贴文字或上传截图识别文字。系统会列出地点候选，只有你确认的地点才会加入 Wishlist。</p></div>
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
    {content && <section className="trip-import-results"><h3>抽取结果 · {content.status}</h3>{content.error && <p>{content.error}</p>}<blockquote>{content.text}</blockquote>
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
      <button className="primary" disabled={busy || selected.length === 0} onClick={() => void confirm()}>确认并加入 Wishlist</button>
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
