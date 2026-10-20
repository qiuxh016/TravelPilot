"use client";

import Link from "next/link";
import { ChangeEvent, FormEvent, useEffect, useRef, useState } from "react";
import styles from "./trip-import-tool.module.css";
import { confirmCandidates, getImportedContent, importContent, listTripContent, recognizeContentImage, retryContentExtraction, updateGuideFacts, type ImportedContent, type ImportedContentSummary, type ImportedGuideFact } from "../lib/api";

type TripImportToolProps = {
  tripId: string;
  onClose?: () => void;
  onImported?: () => void;
};

function ImportForm({ tripId, onImported }: Pick<TripImportToolProps, "tripId" | "onImported">) {
  const [text, setText] = useState("");
  const [content, setContent] = useState<ImportedContent | null>(null);
  const [history, setHistory] = useState<ImportedContentSummary[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [selectedFactIds, setSelectedFactIds] = useState<string[]>([]);
  const [factsDirty, setFactsDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [ocrConfidence, setOcrConfidence] = useState<number | null>(null);

  useEffect(() => {
    let alive = true;
    listTripContent(tripId).then((items) => { if (alive) setHistory(items); }).catch(() => undefined);
    return () => { alive = false; };
  }, [tripId]);

  async function refreshHistory() {
    try { setHistory(await listTripContent(tripId)); } catch { /* Current import result remains usable. */ }
  }

  async function reopenContent(id: string) {
    setBusy(true); setError("");
    try {
      const result = await getImportedContent(id);
      setContent({ ...result, guide_facts: result.guide_facts ?? [] });
      setText(result.text);
      setSelected(result.entities.flatMap((entity) => entity.candidates.filter((candidate) => !candidate.confirmed).map((candidate) => candidate.id)));
      setSelectedFactIds(result.guide_facts.filter((fact) => !fact.saved).map((fact) => fact.id));
      setFactsDirty(false);
    } catch (err) { setError(err instanceof Error ? err.message : "打开导入记录失败"); }
    finally { setBusy(false); }
  }

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
      setFactsDirty(false); await refreshHistory();
    }
    catch (err) { setError(err instanceof Error ? err.message : "导入失败"); }
    finally { setBusy(false); }
  }

  async function confirm() {
    if (!content || (selected.length === 0 && selectedFactIds.length === 0)) return;
    setBusy(true); setError("");
    try {
      const response = await confirmCandidates(content.id, selected, selectedFactIds);
      setContent({ ...response, guide_facts: response.guide_facts ?? [] }); setSelected([]); await refreshHistory(); onImported?.();
    }
    catch (err) { setError(err instanceof Error ? err.message : "确认失败"); }
    finally { setBusy(false); }
  }

  function editFact(id: string, patch: Partial<ImportedGuideFact>) {
    setFactsDirty(true);
    setContent((current) => current ? {
      ...current,
      guide_facts: current.guide_facts.map((fact) => fact.id === id ? { ...fact, ...patch } : fact),
    } : current);
  }

  async function saveFactEdits() {
    if (!content) return;
    setBusy(true); setError("");
    try {
      const response = await updateGuideFacts(content.id, content.guide_facts);
      setContent({ ...response, guide_facts: response.guide_facts ?? [] });
      setFactsDirty(false); await refreshHistory();
    } catch (err) { setError(err instanceof Error ? err.message : "保存攻略修改失败"); }
    finally { setBusy(false); }
  }

  async function retryFailedParts() {
    if (!content) return;
    setBusy(true); setError("");
    try {
      const response = await retryContentExtraction(content.id);
      const result = { ...response, guide_facts: response.guide_facts ?? [] };
      setContent(result);
      setSelectedFactIds((current) => [...new Set([...current, ...result.guide_facts.map((fact) => fact.id)])]);
      await refreshHistory();
    } catch (err) { setError(err instanceof Error ? err.message : "重试失败片段失败"); }
    finally { setBusy(false); }
  }

  function toggle(id: string) {
    setSelected((current) => current.includes(id) ? current.filter((item) => item !== id) : [...current, id]);
  }

  const factGroupLabels: Record<ImportedGuideFact["kind"], string> = {
    SUGGESTED_TIME: "游览与时间", TICKET: "票务与预约", OPENING_HOURS: "票务与开放时间",
    FOOD: "餐饮", TRANSPORT: "交通与路线", ROUTE: "交通与路线",
    ACCOMMODATION: "住宿", TIP: "其他提醒", OTHER: "其他提醒",
  };
  const groupedFacts = content?.guide_facts.reduce<Record<string, ImportedGuideFact[]>>((groups, fact) => {
    const label = factGroupLabels[fact.kind];
    (groups[label] ??= []).push(fact);
    return groups;
  }, {}) ?? {};

  return <>
    <div className="trip-import-intro"><p className="eyebrow">导入攻略</p><h2>把攻略里的地点和实用细节留下来</h2><p>识别地点后匹配地图候选；同时提取购票、推荐时段、美食、路线和出行提示。核对原文依据后，由你选择哪些要点附加到已确认地点。</p></div>
    <section className="trip-import-image layer">
      <h3>从攻略截图识别文字</h3>
      <label>选择图片（JPEG、PNG 或 WebP，最多 10 MB）<input type="file" accept="image/jpeg,image/png,image/webp" disabled={busy} onChange={(event) => void recognizeImage(event)} /></label>
      <p>图片在本地 OCR 流程中临时处理，不保存原图，也不会发送给 LLM。识别出的文字会填入下方文本框供你校对。</p>
    </section>
    {!!history.length && <details className={styles.importHistory}>
      <summary>这段旅行的攻略导入记录（{history.length}）</summary>
      <div>{history.map((item) => <button type="button" key={item.id} disabled={busy} onClick={() => void reopenContent(item.id)}>
        <span>{new Date(item.created_at).toLocaleString()} · {item.status}</span><small>{item.error ?? "打开并继续查看或处理"}</small>
      </button>)}</div>
    </details>}
    <form className="trip-import-form trip-form" onSubmit={submit}>
      <label>{ocrConfidence === null ? "攻略原文" : `识别文字（OCR 平均置信度 ${Math.round(ocrConfidence * 100)}%，请校对）`}<textarea required rows={8} maxLength={20000} value={text} onChange={(event) => setText(event.target.value)} /></label>
      <button className="primary" disabled={busy}>{busy ? "正在识别…" : "提取地点"}</button>
    </form>
    {error && <p className="trip-import-error" role="alert">{error}</p>}
    {content && <section className="trip-import-results"><h3>攻略识别 · {content.status}</h3>{content.error && <p role="status">{content.error}</p>}
      {content.extraction_metadata?.duplicate_source_id && <p className={styles.duplicateNotice} role="status">发现这段旅行中已有相同原文的导入记录（{content.extraction_metadata.duplicate_source_id.slice(0, 8)}…）。请检查是否重复保存地点或攻略要点。</p>}
      {content.extraction_metadata && content.extraction_metadata.total_chunks! > 1 && <p role="status">已处理 {content.extraction_metadata.completed_chunks ?? 0}/{content.extraction_metadata.total_chunks} 段</p>}
      {!!content.extraction_metadata?.failed_chunks?.length && (content.extraction_metadata.retry_attempts ?? 0) < 1
        ? <button type="button" disabled={busy} onClick={() => void retryFailedParts()}>{busy ? "正在重试…" : `只重试 ${content.extraction_metadata.failed_chunks.length} 个失败片段`}</button>
        : !!content.extraction_metadata?.failed_chunks?.length && <p role="status">失败片段已达到重试次数上限；可以重新导入原文后再尝试。</p>}
      <blockquote>{content.text}</blockquote>
      {!!content.guide_facts.length && <section className="trip-guide-facts" aria-label="攻略细节">
        <div><h3>识别到的攻略细节</h3><p>要点已保存在本次导入记录。勾选的内容会附加到对应已确认地点的旅行备注；请按原文核对，模型提取不等于事实核验。</p></div>
        {Object.entries(groupedFacts).map(([group, facts]) => <section className={styles.factGroup} key={group}>
          <h4 className={styles.groupTitle}>{group}</h4>
          {facts.map((fact) => <article className="trip-guide-fact" key={fact.id}>
            <input aria-label={`保存${fact.title}`} type="checkbox" checked={selectedFactIds.includes(fact.id) || Boolean(fact.saved)} disabled={Boolean(fact.saved) || busy} onChange={() => setSelectedFactIds((current) => current.includes(fact.id) ? current.filter((id) => id !== fact.id) : [...current, fact.id])} />
            <div className={styles.factContent}>
              <strong>{fact.title}</strong><small>{fact.related_places.length ? `关联地点：${fact.related_places.join("、")}` : "旅行级攻略提示"} · 来源：用户原文{fact.edited ? " · 用户已编辑" : ""} · 尚未核验 · 提取置信度 {Math.round(fact.confidence * 100)}%</small>
              <span>{fact.content}</span>{fact.evidence && <small>原文依据：{fact.evidence}</small>}{fact.saved && <small>已保存</small>}
              {!fact.saved && content.status !== "IMPORTED" && <details className={styles.factEditor}><summary>编辑、关联或删除</summary>
                <label>标题<input value={fact.title} maxLength={100} onChange={(event) => editFact(fact.id, { title: event.target.value })} /></label>
                <label>内容<textarea value={fact.content} maxLength={600} rows={3} onChange={(event) => editFact(fact.id, { content: event.target.value })} /></label>
                <label>分类<select value={fact.kind} onChange={(event) => editFact(fact.id, { kind: event.target.value as ImportedGuideFact["kind"] })}>
                  <option value="SUGGESTED_TIME">游览与时间</option><option value="TICKET">票务与预约</option><option value="OPENING_HOURS">开放时间</option>
                  <option value="FOOD">餐饮</option><option value="TRANSPORT">交通方式</option><option value="ROUTE">路线顺序</option>
                  <option value="ACCOMMODATION">住宿</option><option value="TIP">其他提醒</option><option value="OTHER">其他</option>
                </select></label>
                <label>关联地点<select value={fact.related_places[0] ?? ""} onChange={(event) => editFact(fact.id, { related_places: event.target.value ? [event.target.value] : [] })}>
                  <option value="">旅行级提示（不关联地点）</option>
                  {content.entities.map((entity) => <option key={entity.id} value={entity.name}>{entity.name}</option>)}
                </select></label>
                <button type="button" onClick={() => { setContent((current) => current ? { ...current, guide_facts: current.guide_facts.filter((item) => item.id !== fact.id) } : current); setSelectedFactIds((current) => current.filter((id) => id !== fact.id)); setFactsDirty(true); }}>删除此要点</button>
              </details>}
            </div>
          </article>)}
        </section>)}
        {factsDirty && <button type="button" disabled={busy} onClick={() => void saveFactEdits()}>保存攻略要点修改</button>}
      </section>}
      {!content.entities.length && !content.guide_facts.length && content.status !== "FAILED" && <p>没有提取到地点或可记录的攻略细节。可以检查文字后重新提取。</p>}
      {content.entities.map((entity) => <article className="layer" key={entity.id}>
        <span className="index">置信度 {Math.round(entity.confidence * 100)}%</span><h3>{entity.name}</h3>
        {entity.suggestion && <p>{entity.suggestion}</p>}
        {entity.evidence ? <blockquote>原文依据：{entity.evidence}</blockquote> : <p>模型未提供可引用的原文依据，请对照上方攻略确认。</p>}
        {entity.candidates.length === 0 && <p>没有找到地图候选，可以稍后手动添加。</p>}
        {entity.candidates.map((candidate) => <label key={candidate.id} className="candidate-row">
          <input type="checkbox" checked={selected.includes(candidate.id) || candidate.confirmed} disabled={candidate.confirmed || busy} onChange={() => toggle(candidate.id)} />
          <span>{candidate.name} · {candidate.address} · 匹配度 {Math.round(candidate.score * 100)}%{candidate.confirmed ? " · 已加入" : ""}<small className={styles.candidateSource}>{candidate.provider === "amap" ? "高德地图候选" : candidate.provider === "fake" ? "模拟地图候选" : `${candidate.provider} 地图候选`} · 仅匹配地点，营业时间与票务未核验</small></span>
        </label>)}
      </article>)}
      {content.status === "NEEDS_CONFIRMATION" && <button className="primary" disabled={busy || (selected.length === 0 && selectedFactIds.length === 0)} onClick={() => void confirm()}>保存 {selected.length} 个地点与 {selectedFactIds.length} 条攻略要点</button>}
      {content.status === "IMPORTED" && <Link className={styles.advisorLink} href={`/advisor?tripId=${tripId}`}>让 AI 顾问基于已保存地点继续规划 ↗</Link>}
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
