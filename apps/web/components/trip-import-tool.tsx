"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { ChangeEvent, FormEvent, useEffect, useRef, useState } from "react";
import styles from "./trip-import-tool.module.css";
import { confirmCandidates, createAdvisorSession, deleteImportedContent, getImportedContent, importContent, listTripContent, recognizeContentImage, retryContentExtraction, sendAdvisorMessage, updateGuideFacts, type ImportedContent, type ImportedContentSummary, type ImportedGuideFact } from "../lib/api";

type TripImportToolProps = {
  tripId: string;
  onClose?: () => void;
  onImported?: () => void;
};

const FACT_PRIORITY: Record<ImportedGuideFact["kind"], number> = {
  TICKET: 9, OPENING_HOURS: 8, FOOD: 7, ACCOMMODATION: 6, TRANSPORT: 6,
  ROUTE: 5, SUGGESTED_TIME: 5, TIP: 3, OTHER: 1,
};

function recommendedFacts(facts: ImportedGuideFact[]) {
  const seen = new Set<string>();
  return facts
    .filter((fact) => {
      const places = [...fact.related_places].sort().join("/");
      const key = `${fact.kind}:${places}:${fact.content.toLocaleLowerCase().replace(/[\s，。；：、“”‘’,.!?！？]/g, "")}`;
      if (!fact.content.trim() || seen.has(key)) return false;
      seen.add(key);
      return fact.kind !== "OTHER";
    })
    .sort((a, b) => {
      const score = (fact: ImportedGuideFact) => FACT_PRIORITY[fact.kind] +
        (/\d{1,2}[:：点时]|闭馆|营业|开放|预约|门票|停车|步行|打车|地铁|公交|午餐|晚餐|早餐/.test(fact.content) ? 2 : 0) +
        Math.min(fact.content.length, 80) / 200;
      return score(b) - score(a);
    })
    .slice(0, 6);
}

function defaultFactIds(facts: ImportedGuideFact[]) {
  return recommendedFacts(facts).filter((fact) => !fact.saved).map((fact) => fact.id);
}

function defaultCandidateIds(entities: ImportedContent["entities"]) {
  return entities.flatMap((entity) => {
    const best = [...entity.candidates].sort((a, b) => b.score - a.score)[0];
    return best && !best.confirmed ? [best.id] : [];
  });
}

function guideNarrative(content: ImportedContent, selectedFactIds: string[]) {
  const facts = content.guide_facts.filter((fact) => fact.saved || selectedFactIds.includes(fact.id));
  const names = (category: string) => content.entities
    .filter((entity) => entity.category === category)
    .map((entity) => entity.name)
    .slice(0, 4);
  const attractions = names("ATTRACTION");
  const foodPlaces = names("FOOD");
  const lodgingPlaces = names("LODGING");
  const visitNotes = facts.filter((fact) => ["SUGGESTED_TIME", "ROUTE", "TRANSPORT"].includes(fact.kind));
  const foodNotes = facts.filter((fact) => fact.kind === "FOOD");
  const lodgingNotes = facts.filter((fact) => fact.kind === "ACCOMMODATION");
  const reminders = facts.filter((fact) => ["TICKET", "OPENING_HOURS", "TIP"].includes(fact.kind));
  const shortFacts = (items: ImportedGuideFact[]) => {
    const selected = items.slice(0, 2).map((fact) => {
      const value = fact.content.trim();
      return value.length > 96 ? `${value.slice(0, 95).trimEnd()}…` : value;
    }).filter(Boolean);
    return selected.join("；");
  };
  const attractionTotal = content.entities.filter((entity) => entity.category === "ATTRACTION").length;
  const suggestions = content.entities.map((entity) => entity.suggestion?.trim()).filter((value): value is string => Boolean(value)).slice(0, 2);
  const clauses = [
    attractions.length ? `这份攻略提到了${attractions.join("、")}${attractionTotal > attractions.length ? "等景点" : ""}` : "这份攻略",
    suggestions.length ? `游览时可参考${suggestions.join("；")}` : "",
    shortFacts(visitNotes),
    foodPlaces.length || foodNotes.length
      ? `吃饭可以考虑${[foodPlaces.join("、"), shortFacts(foodNotes)].filter(Boolean).join("；")}`
      : "",
    lodgingPlaces.length || lodgingNotes.length
      ? `住宿可以关注${[lodgingPlaces.join("、"), shortFacts(lodgingNotes)].filter(Boolean).join("；")}`
      : "",
    reminders.length ? `还要留意${shortFacts(reminders)}` : "",
  ].filter(Boolean);

  if (!clauses.length) return "暂时没有提取到可直接用于旅行的地点或实用信息，可以展开核对原文，或调整攻略内容后重新识别。";
  const hasOpeningDetails = facts.some((fact) => fact.kind === "OPENING_HOURS");
  const hasTicketDetails = facts.some((fact) => fact.kind === "TICKET");
  const reminder = [
    hasOpeningDetails ? "开放时间" : "",
    hasTicketDetails ? "购票预约" : "",
  ].filter(Boolean).join("和");
  return `${clauses.join("；")}。${reminder ? `${reminder}来自攻略原文，出发前请再核实。` : "内容来自攻略原文，保存前可展开核对。"}`;
}

function ImportForm({ tripId, onImported }: Pick<TripImportToolProps, "tripId" | "onImported">) {
  const router = useRouter();
  const [text, setText] = useState("");
  const [content, setContent] = useState<ImportedContent | null>(null);
  const [history, setHistory] = useState<ImportedContentSummary[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [selectedFactIds, setSelectedFactIds] = useState<string[]>([]);
  const [factsDirty, setFactsDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [ocrRecognized, setOcrRecognized] = useState(false);
  const resultsRef = useRef<HTMLElement>(null);

  async function askAdvisorToPlan() {
    if (!content) return;
    const places = content.entities
      .filter((entity) => entity.candidates.some((candidate) => candidate.confirmed))
      .map((entity) => entity.name);
    const selectedFacts = content.guide_facts.filter((fact) => fact.saved);
    const byKind = new Map<string, string[]>();
    const kindLabels: Record<ImportedGuideFact["kind"], string> = {
      TICKET: "购票与预约", OPENING_HOURS: "开放时间", SUGGESTED_TIME: "推荐时段",
      FOOD: "美食", ROUTE: "路线", TRANSPORT: "出行方式", ACCOMMODATION: "住宿",
      TIP: "实用提醒", OTHER: "其他",
    };
    for (const fact of selectedFacts) {
      const label = kindLabels[fact.kind];
      byKind.set(label, [...(byKind.get(label) ?? []), `${fact.related_places.length ? `【${fact.related_places.join("、")}】` : ""}${fact.content}`]);
    }
    const guideContext = [
      places.length ? `攻略中已确认的地点：${places.join("、")}` : "攻略中没有已确认的地点",
      ...[...byKind].map(([label, items]) => `${label}：${items.join("；")}`),
    ].join("\n").slice(0, 2600);
    const request = [
      "请把这份已确认的旅行攻略作为上下文，结合当前旅行、已收藏地点和现有行程，给出简洁、可执行的调整建议。",
      "重点检查相邻景点是否适合顺路安排、已有行程哪些日期或时段值得调整，以及用餐、住宿、购票预约和交通提示是否应加入。按景点、美食、住宿、其他实用信息区分建议；不要把餐厅当景点。",
      "先说明你建议改什么和原因，再提供可操作的选项。日期、时间或住宿晚数等信息若无法从当前旅行和攻略确定，先询问我；不要猜测。不要直接修改正式行程。若我选择采纳，请生成含日期、时间、类别和变更摘要的行程草案，待我确认后再写入正式计划。",
      "导入攻略内容：\n" + guideContext,
    ].join("\n\n");

    setBusy(true); setError("");
    try {
      const session = await createAdvisorSession({ mode: "existing", trip_id: tripId, pace_level: 3 });
      await sendAdvisorMessage(session.id, request);
      router.push(`/advisor?sessionId=${session.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "启动旅行顾问失败");
    } finally { setBusy(false); }
  }

  useEffect(() => {
    let alive = true;
    listTripContent(tripId).then((items) => { if (alive) setHistory(items); }).catch(() => undefined);
    return () => { alive = false; };
  }, [tripId]);

  useEffect(() => {
    if (content) resultsRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [content?.id, content?.status]);

  async function refreshHistory() {
    try { setHistory(await listTripContent(tripId)); } catch { /* Current import result remains usable. */ }
  }

  async function reopenContent(id: string) {
    setBusy(true); setError("");
    try {
      const result = await getImportedContent(id);
      setContent({ ...result, guide_facts: result.guide_facts ?? [] });
      setText(result.text);
      setSelected(defaultCandidateIds(result.entities));
      setSelectedFactIds(defaultFactIds(result.guide_facts));
      setFactsDirty(false);
    } catch (err) { setError(err instanceof Error ? err.message : "打开导入记录失败"); }
    finally { setBusy(false); }
  }

  async function deleteHistoryItem(item: ImportedContentSummary) {
    const confirmed = window.confirm(
      "删除这条攻略导入记录？识别结果和候选项会一并删除；已经确认加入旅行的地点与备注会保留。",
    );
    if (!confirmed) return;
    setBusy(true); setError("");
    try {
      await deleteImportedContent(item.id);
      setHistory((current) => current.filter((entry) => entry.id !== item.id));
      setContent((current) => current?.id === item.id ? null : current);
      if (content?.id === item.id) {
        setSelected([]); setSelectedFactIds([]); setFactsDirty(false); setText("");
      }
    } catch (err) { setError(err instanceof Error ? err.message : "删除导入记录失败"); }
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
      } else {
        setText(result.text);
        setOcrRecognized(true);
      }
    } catch (err) { setError(err instanceof Error ? err.message : "图片文字识别失败"); }
    finally { setBusy(false); input.value = ""; }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError("");
    try {
      const response = await importContent(tripId, text);
      const result = { ...response, guide_facts: response.guide_facts ?? [] };
      setContent(result); setSelected(defaultCandidateIds(result.entities));
      setSelectedFactIds(defaultFactIds(result.guide_facts));
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
      setSelectedFactIds((current) => [...new Set([...current, ...defaultFactIds(result.guide_facts)])]);
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
  const retryWholeGuide = content?.extraction_metadata?.failure_code === "RESULT_LIMIT_EXCEEDED";
  const hasRetryableFailures = Boolean(content?.extraction_metadata?.failed_chunks?.length || retryWholeGuide);
  const suggestedFacts = content ? recommendedFacts(content.guide_facts) : [];
  const suggestedFactIds = new Set(suggestedFacts.map((fact) => fact.id));
  const otherFacts = content?.guide_facts.filter((fact) => !suggestedFactIds.has(fact.id)) ?? [];
  const activeStep = !content ? 0 : content.status === "IMPORTED" ? 2 : 1;
  const guideSummary = content ? guideNarrative(content, selectedFactIds) : "";
  const selectedPlaceCount = content?.entities.reduce((count, entity) => count + entity.candidates.filter((candidate) => selected.includes(candidate.id) || candidate.confirmed).length, 0) ?? 0;
  const selectedFactCount = content?.guide_facts.filter((fact) => selectedFactIds.includes(fact.id) || fact.saved).length ?? 0;

  return <>
    <div className="trip-import-intro"><p className="eyebrow">导入攻略</p><h2>把攻略里的地点和实用细节留下来</h2><p>识别地点后匹配地图候选；同时提取购票、推荐时段、美食、路线和出行提示。核对原文依据后，由你选择哪些要点附加到已确认地点。</p></div>
    <ol className={styles.workflow} aria-label="攻略整理流程">
      {["导入攻略", "核对实用信息", "顾问调整计划"].map((label, index) => <li className={index < activeStep ? styles.stepDone : index === activeStep ? styles.stepCurrent : ""} key={label}>
        <span>{index < activeStep ? "✓" : index + 1}</span><strong>{label}</strong>
      </li>)}
    </ol>
    <section className={`${styles.uploadArea} trip-import-image layer`}>
      <h3>从攻略截图识别文字</h3>
      <label>选择图片（JPEG、PNG 或 WebP，最多 10 MB）<input type="file" accept="image/jpeg,image/png,image/webp" disabled={busy} onChange={(event) => void recognizeImage(event)} /></label>
      <p>图片在本地 OCR 流程中临时处理，不保存原图，也不会发送给 LLM。识别出的文字会填入下方文本框供你校对。</p>
    </section>
    {!!history.length && <details className={styles.importHistory}>
      <summary>这段旅行的攻略导入记录（{history.length}）</summary>
      <div>{history.map((item) => <article className={styles.historyItem} key={item.id}>
        <button className={styles.historyOpen} type="button" disabled={busy} onClick={() => void reopenContent(item.id)}>
          <span>{new Date(item.created_at).toLocaleString()} · {item.status}</span><small>{item.error ?? "打开并继续查看或处理"}</small>
        </button>
        <button className={styles.historyDelete} type="button" disabled={busy} onClick={() => void deleteHistoryItem(item)}>删除记录</button>
      </article>)}</div>
    </details>}
    <form className={`${styles.importComposer} trip-import-form trip-form`} onSubmit={submit}>
      <label>{ocrRecognized ? "图片识别文字（请校对）" : "攻略原文"}<textarea required rows={8} maxLength={20000} value={text} onChange={(event) => { setText(event.target.value); setOcrRecognized(false); }} /></label>
      <button className="primary" disabled={busy}>{busy ? "正在整理攻略…" : "整理攻略"}</button>
    </form>
    {error && <p className="trip-import-error" role="alert">{error}</p>}
    {content && <section className="trip-import-results" ref={resultsRef}><header className={styles.resultHeading}><div><h3>攻略整理结果</h3><p>{content.status === "IMPORTED" ? "攻略信息已保存到这段旅行，可以继续让顾问结合行程分析。" : "请快速核对自动整理的重点，勾选需要保留的内容。"}</p></div>{content.status === "IMPORTED" && <span className={styles.savedMark}>已保存</span>}</header>
      <section className={styles.guideSummary} aria-label="攻略重点">
        <p>{guideSummary}</p>
        {content.status === "NEEDS_CONFIRMATION" && <div className={styles.summaryActions}>
          <span>已选 {selectedPlaceCount} 个地点 · {selectedFactCount} 条实用信息</span>
          <button className="primary" type="button" disabled={busy || (selected.length === 0 && selectedFactIds.length === 0)} onClick={() => void confirm()}>
            确认保存重点
          </button>
        </div>}
      </section>
      {content.status === "IMPORTED" && <section className={styles.advisorStart}>
        <div><strong>下一步：让顾问结合整段旅行整理建议</strong><p>顾问会读取当前行程和已确认的攻略内容，检查景点顺路、时间安排、餐饮、住宿与票务；只有你确认草案后才更新正式计划。</p></div>
        <button type="button" className="primary" disabled={busy} onClick={() => void askAdvisorToPlan()}>{busy ? "正在准备旅行上下文…" : "和顾问一起整理行程"} ↗</button>
      </section>}
      {content.status === "PARTIAL" && <p className={styles.resultSummary} role="status">已整理出可用内容，部分段落还没读完。下面先显示实用重点；保存前可展开原文核对。</p>}
      {content.status === "FAILED" && content.error && <p role="status">{content.error}</p>}
      {hasRetryableFailures && (content.extraction_metadata?.retry_attempts ?? 0) < 2
        ? <button type="button" disabled={busy} onClick={() => void retryFailedParts()}>{busy ? "正在重试…" : retryWholeGuide ? "按新上限重新提取整篇攻略" : `缩小片段重试 ${content.extraction_metadata?.failed_chunks?.length ?? 0} 个失败片段`}</button>
        : hasRetryableFailures && <p role="status">失败片段已达到重试次数上限；可以删除这条记录后重新导入原文。</p>}
      <details className={styles.sourceText}><summary>查看攻略原文</summary><blockquote>{content.text}</blockquote></details>
      {(!!content.guide_facts.length || !!content.entities.length) && <details className={styles.reviewDetails}>
        <summary>核对地点与攻略细节</summary>
        {!!content.guide_facts.length && <section className="trip-guide-facts" aria-label="攻略细节">
        <div><h3>为你整理的实用信息</h3><p>已优先挑出时间、预约、餐饮、住宿和路线等内容。确认后会加入旅行备注。</p></div>
        {Object.entries(suggestedFacts.reduce<Record<string, ImportedGuideFact[]>>((groups, fact) => {
          const label = factGroupLabels[fact.kind]; (groups[label] ??= []).push(fact); return groups;
        }, {})).map(([group, facts]) => <section className={styles.factGroup} key={group}>
          <h4 className={styles.groupTitle}>{group}</h4>
          {facts.map((fact) => <article className="trip-guide-fact" key={fact.id}>
            <input aria-label={`保存${fact.title}`} type="checkbox" checked={selectedFactIds.includes(fact.id) || Boolean(fact.saved)} disabled={Boolean(fact.saved) || busy} onChange={() => setSelectedFactIds((current) => current.includes(fact.id) ? current.filter((id) => id !== fact.id) : [...current, fact.id])} />
            <div className={styles.factContent}>
              <strong>{fact.title}</strong><small>{fact.related_places.length ? `适用于：${fact.related_places.join("、")}` : "适用于这段旅行"}{fact.edited ? " · 已编辑" : ""}{fact.saved ? " · 已保存" : ""}</small>
              <span>{fact.content}</span>
              {fact.evidence && <details className={styles.factEvidence}><summary>查看原文依据</summary><span>{fact.evidence}</span></details>}
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
        {!!otherFacts.length && <details className={styles.moreFacts}><summary>查看其他 {otherFacts.length} 条识别信息</summary>
          {Object.entries(otherFacts.reduce<Record<string, ImportedGuideFact[]>>((groups, fact) => {
            const label = factGroupLabels[fact.kind]; (groups[label] ??= []).push(fact); return groups;
          }, {})).map(([group, facts]) => <section className={styles.factGroup} key={group}>
            <h4 className={styles.groupTitle}>{group}</h4>
            {facts.map((fact) => <article className="trip-guide-fact" key={fact.id}>
              <input aria-label={`保存${fact.title}`} type="checkbox" checked={selectedFactIds.includes(fact.id) || Boolean(fact.saved)} disabled={Boolean(fact.saved) || busy} onChange={() => setSelectedFactIds((current) => current.includes(fact.id) ? current.filter((id) => id !== fact.id) : [...current, fact.id])} />
              <div className={styles.factContent}><strong>{fact.title}</strong><span>{fact.content}</span>
                {fact.evidence && <details className={styles.factEvidence}><summary>查看原文依据</summary><span>{fact.evidence}</span></details>}
                {!fact.saved && content.status !== "IMPORTED" && <details className={styles.factEditor}><summary>编辑或删除</summary>
                  <label>标题<input value={fact.title} maxLength={100} onChange={(event) => editFact(fact.id, { title: event.target.value })} /></label>
                  <label>内容<textarea value={fact.content} maxLength={600} rows={3} onChange={(event) => editFact(fact.id, { content: event.target.value })} /></label>
                  <label>分类<select value={fact.kind} onChange={(event) => editFact(fact.id, { kind: event.target.value as ImportedGuideFact["kind"] })}><option value="SUGGESTED_TIME">游览与时间</option><option value="TICKET">票务与预约</option><option value="OPENING_HOURS">开放时间</option><option value="FOOD">餐饮</option><option value="TRANSPORT">交通方式</option><option value="ROUTE">路线顺序</option><option value="ACCOMMODATION">住宿</option><option value="TIP">其他提醒</option><option value="OTHER">其他</option></select></label>
                  <label>关联地点<select value={fact.related_places[0] ?? ""} onChange={(event) => editFact(fact.id, { related_places: event.target.value ? [event.target.value] : [] })}><option value="">旅行级提示（不关联地点）</option>{content.entities.map((entity) => <option key={entity.id} value={entity.name}>{entity.name}</option>)}</select></label>
                  <button type="button" onClick={() => { setContent((current) => current ? { ...current, guide_facts: current.guide_facts.filter((item) => item.id !== fact.id) } : current); setSelectedFactIds((current) => current.filter((id) => id !== fact.id)); setFactsDirty(true); }}>删除此要点</button>
                </details>}</div>
            </article>)}
          </section>)}
        </details>}
        {factsDirty && <button type="button" disabled={busy} onClick={() => void saveFactEdits()}>保存攻略要点修改</button>}
      </section>}
      {content.entities.map((entity) => {
        const best = [...entity.candidates].sort((a, b) => b.score - a.score)[0];
        const alternatives = entity.candidates.filter((candidate) => candidate.id !== best?.id);
        const renderCandidate = (candidate: typeof entity.candidates[number]) => <label key={candidate.id} className="candidate-row">
          <input type="checkbox" checked={selected.includes(candidate.id) || candidate.confirmed} disabled={candidate.confirmed || busy} onChange={() => toggle(candidate.id)} />
          <span>{candidate.name} · {candidate.address}{candidate.confirmed ? " · 已加入" : ""}<small className={styles.candidateSource}>地图匹配结果，营业时间与票务信息尚未核实</small></span>
        </label>;
        return <article className={`${styles.importPlace} layer`} key={entity.id}>
          <h3>{entity.name}</h3>
          {entity.suggestion && <p>{entity.suggestion}</p>}
          {best ? renderCandidate(best) : <p>暂时没有找到地图结果，原文信息仍已保留。</p>}
          {!!alternatives.length && <details className={styles.moreFacts}><summary>更换地点匹配（{alternatives.length}）</summary>{alternatives.map(renderCandidate)}</details>}
          {entity.evidence && <details className={styles.factEvidence}><summary>查看攻略中的对应文字</summary><span>{entity.evidence}</span></details>}
        </article>;
      })}
      </details>}
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
