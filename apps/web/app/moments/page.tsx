"use client";

import Link from "next/link";
import { FormEvent, useEffect, useRef, useState } from "react";
import {
  ApiError,
  createMemorySuggestion,
  createMoment,
  createMomentComment,
  deleteMoment,
  deleteMomentComment,
  deleteMomentReaction,
  draftMomentCaption,
  getCurrentUser,
  getMomentMediaBlob,
  listMomentComments,
  listMoments,
  listTrips,
  reviewMemorySuggestion,
  setMomentReaction,
  updateMoment,
  uploadMomentMedia,
  type MemorySuggestion,
  type MomentComment,
  type TravelMoment,
  type Trip,
} from "../../lib/api";
import { tripOptionLabel, useTripSelection } from "../../components/trip-selection-context";

function PrivatePhoto({ assetId, alt }: { assetId: string; alt: string }) {
  const [src, setSrc] = useState("");
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let active = true;
    let objectUrl = "";
    void getMomentMediaBlob(assetId).then((url) => {
      objectUrl = url;
      if (active) setSrc(url);
      else URL.revokeObjectURL(url);
    }).catch(() => { if (active) setFailed(true); });
    return () => { active = false; if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [assetId]);
  if (failed) return <div className="moment-photo-failed">图片当前无法读取</div>;
  return src ? <img src={src} alt={alt} loading="lazy" /> : <div className="moment-photo-loading" aria-label="图片加载中" />;
}

export default function MomentsPage() {
  const { selectedTripId } = useTripSelection();
  const [moments, setMoments] = useState<TravelMoment[]>([]);
  const [trips, setTrips] = useState<Trip[]>([]);
  const [userId, setUserId] = useState("");
  const [body, setBody] = useState("");
  const [tripId, setTripId] = useState("");
  const [day, setDay] = useState("");
  const [placeName, setPlaceName] = useState("");
  const [audience, setAudience] = useState<"PRIVATE" | "FRIENDS">("PRIVATE");
  const [memoryConsent, setMemoryConsent] = useState(false);
  const [photos, setPhotos] = useState<File[]>([]);
  const [photoPreviews, setPhotoPreviews] = useState<string[]>([]);
  const [commentText, setCommentText] = useState<Record<string, string>>({});
  const [comments, setComments] = useState<Record<string, MomentComment[]>>({});
  const [openComments, setOpenComments] = useState<Record<string, boolean>>({});
  const [captions, setCaptions] = useState<Record<string, string>>({});
  const [suggestions, setSuggestions] = useState<Record<string, MemorySuggestion>>({});
  const [loading, setLoading] = useState(true);
  const [composerOpen, setComposerOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const composerDialog = useRef<HTMLDialogElement>(null);

  async function load() {
    try {
      setLoading(true); setError("");
      const [rows, tripRows, user] = await Promise.all([listMoments(), listTrips(), getCurrentUser()]);
      setMoments(rows); setTrips(tripRows); setUserId(user.id);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "旅行动态暂时无法加载"); }
    finally { setLoading(false); }
  }

  useEffect(() => { void load(); }, []);

  useEffect(() => {
    const dialog = composerDialog.current;
    if (!dialog) return;
    if (composerOpen && !dialog.open) dialog.showModal();
    else if (!composerOpen && dialog.open) dialog.close();
  }, [composerOpen]);

  function openComposer() {
    setTripId(selectedTripId);
    setComposerOpen(true);
  }

  useEffect(() => {
    const urls = photos.map((photo) => URL.createObjectURL(photo));
    setPhotoPreviews(urls);
    return () => urls.forEach((url) => URL.revokeObjectURL(url));
  }, [photos]);

  async function publish(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError(""); setNotice("");
    if (photos.some((file) => file.size > 10 * 1024 * 1024)) {
      setError("单张照片不能超过 10 MB。"); setBusy(false); return;
    }
    try {
      const moment = await createMoment({
        body: body.trim(), trip_id: tripId || null, day: day || null,
        place_name: placeName.trim() || null,
        audience: photos.length ? "PRIVATE" : audience,
        memory_extraction_enabled: memoryConsent,
      });
      try {
        for (const photo of photos) await uploadMomentMedia(moment.id, photo);
        if (photos.length && audience === "FRIENDS") await updateMoment(moment.id, { audience: "FRIENDS" });
      } catch (cause) {
        setNotice("旅行片段已保存为仅自己可见，但有照片未能上传。可检查后重新发布。");
        setError(cause instanceof Error ? cause.message : "照片上传失败");
        setBody(""); setPhotos([]); await load(); return;
      }
      setBody(""); setTripId(""); setDay(""); setPlaceName(""); setAudience("PRIVATE");
      setMemoryConsent(false); setPhotos([]); setComposerOpen(false); setNotice(audience === "FRIENDS" ? "已发布到好友圈，动态已更新。" : "已保存为仅自己可见，动态已更新。相册照片已自动移除 EXIF/GPS 元数据。");
      await load();
    } catch (cause) { setError(cause instanceof Error ? cause.message : "保存失败"); }
    finally { setBusy(false); }
  }

  async function toggleAudience(moment: TravelMoment) {
    const next = moment.audience === "FRIENDS" ? "PRIVATE" : "FRIENDS";
    setBusy(true); setError("");
    try { await updateMoment(moment.id, { audience: next }); await load(); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "可见范围更新失败"); }
    finally { setBusy(false); }
  }

  async function removeMoment(moment: TravelMoment) {
    if (!window.confirm("删除这条旅行片段和其中的照片？此操作无法撤销。")) return;
    setBusy(true); setError("");
    try { await deleteMoment(moment.id); await load(); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "删除失败"); }
    finally { setBusy(false); }
  }

  async function generateCaption(moment: TravelMoment) {
    setBusy(true); setError("");
    try {
      const result = await draftMomentCaption(moment.id);
      setCaptions((current) => ({ ...current, [moment.id]: result.captions[0] }));
    } catch (cause) { setError(cause instanceof ApiError ? cause.message : cause instanceof Error ? cause.message : "文案暂时不可用"); }
    finally { setBusy(false); }
  }

  async function applyCaption(moment: TravelMoment) {
    const draft = captions[moment.id]?.trim();
    if (!draft) return;
    setBusy(true); setError("");
    try { await updateMoment(moment.id, { body: draft }); setNotice("文案已替换到旅行片段；请确认受众后再分享。"); setCaptions((current) => ({ ...current, [moment.id]: "" })); await load(); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "文案保存失败"); }
    finally { setBusy(false); }
  }

  async function copyCaption(momentId: string) {
    const text = captions[momentId]?.trim();
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      setNotice("文案已复制，可粘贴到你选择的社交平台。外部平台不会自动收到内容。");
    } catch { setError("复制失败，请手动选择文案复制。"); }
  }

  async function addMemorySuggestion(moment: TravelMoment) {
    setBusy(true); setError("");
    try {
      const suggestion = await createMemorySuggestion(moment.id);
      setSuggestions((current) => ({ ...current, [moment.id]: suggestion }));
    }
    catch (cause) { setError(cause instanceof Error ? cause.message : "记忆建议暂时不可用"); }
    finally { setBusy(false); }
  }

  async function reviewSuggestion(moment: TravelMoment, suggestion: MemorySuggestion, accepted: boolean) {
    setBusy(true); setError("");
    try {
      const reviewed = await reviewMemorySuggestion(moment.id, suggestion.id, suggestion.candidate, accepted);
      setSuggestions((current) => ({ ...current, [moment.id]: reviewed }));
      setNotice(accepted ? "已确认并写入关联旅行的私有记忆，可供后续规划参考。" : "已拒绝此记忆建议。");
    } catch (cause) { setError(cause instanceof Error ? cause.message : "记忆处理失败"); }
    finally { setBusy(false); }
  }

  async function toggleComments(moment: TravelMoment) {
    const opening = !openComments[moment.id];
    setOpenComments((current) => ({ ...current, [moment.id]: opening }));
    if (opening && !comments[moment.id]) {
      try {
        const rows = await listMomentComments(moment.id);
        setComments((current) => ({ ...current, [moment.id]: rows }));
      }
      catch (cause) { setError(cause instanceof Error ? cause.message : "评论暂时无法加载"); }
    }
  }

  async function sendComment(moment: TravelMoment) {
    const text = commentText[moment.id]?.trim(); if (!text) return;
    setBusy(true); setError("");
    try {
      const created = await createMomentComment(moment.id, text);
      setComments((current) => ({ ...current, [moment.id]: [...(current[moment.id] ?? []), created] }));
      setCommentText((current) => ({ ...current, [moment.id]: "" })); await load();
    } catch (cause) { setError(cause instanceof Error ? cause.message : "评论发送失败"); }
    finally { setBusy(false); }
  }

  async function removeComment(moment: TravelMoment, comment: MomentComment) {
    try { await deleteMomentComment(moment.id, comment.id); setComments((current) => ({ ...current, [moment.id]: (current[moment.id] ?? []).filter((row) => row.id !== comment.id) })); await load(); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "评论删除失败"); }
  }

  async function toggleReaction(moment: TravelMoment) {
    try { if (moment.reacted_by_me) await deleteMomentReaction(moment.id); else await setMomentReaction(moment.id); await load(); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "回应失败"); }
  }

  return <main className="shell moments-page">
    <header className="journal-page-head">
      <div><h1>记下旅途，也留住当时的心情。</h1><p>风景会变，心情也会变。有些瞬间值得被认真记下，留给以后的自己。</p></div>
      <button type="button" className="primary" onClick={openComposer}>记录这一刻 <span aria-hidden="true">↗</span></button>
    </header>
    {error && <p className="social-message is-error" role="alert">{error}</p>}
    {notice && <p className="social-message" role="status">{notice}</p>}
    <dialog className="moment-dialog" ref={composerDialog} onClose={() => setComposerOpen(false)} onClick={(event) => { if (event.target === event.currentTarget) setComposerOpen(false); }} aria-labelledby="moment-composer-title">
    <section className="social-card moment-composer" id="moment-composer">
      <div className="moment-composer-heading"><div><h2 id="moment-composer-title">今天有什么想留给以后的自己？</h2><small>写好后选择仅自己可见或发布到好友圈。照片默认仅自己可见。</small></div><button type="button" className="moment-dialog-close" onClick={() => setComposerOpen(false)} aria-label="关闭记录窗口">×</button></div>
      <form className="moment-form" onSubmit={publish}>
        <div className="moment-editor-grid">
        <label className="moment-story-field">写下旅行感受<textarea required minLength={1} maxLength={4000} value={body} onChange={(event) => setBody(event.target.value)} placeholder="今天有什么想留给以后的自己？" rows={6} /></label>
        <div className="moment-editor-media">
          <label className="moment-upload-field">照片（JPEG、PNG 或 WebP，最多 12 张，每张 10 MB）<input type="file" accept="image/jpeg,image/png,image/webp" multiple onChange={(event) => setPhotos(Array.from(event.target.files ?? []).slice(0, 12))} /><span>选择照片，或从电脑拖到这里</span></label>
          {photos.length > 0 && <p className="moment-photo-selection">已选择 {photos.length} 张</p>}
          {photoPreviews.length > 0 && <div className="moment-photo-previews" aria-label="照片预览">{photoPreviews.map((src, index) => <img src={src} alt={`待发布照片 ${index + 1}`} key={src} />)}</div>}
        </div>
        </div>
        <div className="moment-fields">
          <label>关联旅行（可选）<select value={tripId} onChange={(event) => setTripId(event.target.value)}><option value="">不关联旅行</option>{trips.map((trip) => <option key={trip.id} value={trip.id}>{tripOptionLabel(trip, trips)}</option>)}</select></label>
          <label>日期<input type="date" value={day} onChange={(event) => setDay(event.target.value)} /></label>
          <label>地点<input maxLength={160} value={placeName} onChange={(event) => setPlaceName(event.target.value)} placeholder="可选" /></label>
          <label>分享范围<select value={audience} onChange={(event) => setAudience(event.target.value as "PRIVATE" | "FRIENDS")}><option value="PRIVATE">仅自己（默认）</option><option value="FRIENDS">好友圈</option></select></label>
        </div>
        <div className="moment-publish-line"><div><label className="moment-consent"><input type="checkbox" checked={memoryConsent} disabled={!tripId} onChange={(event) => setMemoryConsent(event.target.checked)} />允许从这条文字中提取旅行记忆建议</label><small className="moment-help">{tripId ? "确认后的记忆可用于之后的行程建议；照片不会用于识别心情。" : "关联一趟旅行后，可选择将文字整理成记忆建议。"}</small></div><button className="primary" disabled={busy}>{busy ? "正在保存……" : audience === "FRIENDS" ? "保存并分享给好友" : "保存这段记忆"}</button></div>
      </form>
    </section>
    </dialog>
    <div className="moments-heading"><h2>动态</h2><Link href="/friends">管理好友与分享权限 →</Link></div>
    {loading ? <p className="status">正在加载旅行动态……</p> : moments.length === 0 ? <section className="journal-empty-visual"><img src="/images/journey-dawn.webp" alt="清晨山湖的旅行灵感图" /><div><span>还没有旅行记录</span><h2>每一次出发，<br />都是一段独一无二的故事。</h2><p>从今天开始，把走过的路、遇到的人和当时的心情，慢慢留在这里。</p><button className="primary" type="button" onClick={openComposer}>写下第一段旅行记录 <span aria-hidden="true">→</span></button></div></section> : <div className="moment-feed">{moments.map((moment) => {
      const own = moment.owner_user_id === userId;
      return <article className="moment-card" key={moment.id}>
        <header className="moment-header"><div className="moment-avatar" aria-hidden="true">{moment.author_email.slice(0, 1).toUpperCase()}</div><div><strong>{own ? "我" : moment.author_email}</strong><small>{moment.created_at.replace("T", " ").slice(0, 16)} · {own ? moment.audience === "PRIVATE" ? "仅自己可见" : "好友可见" : "好友分享"}</small></div>{own && <button className="social-danger" disabled={busy} onClick={() => void removeMoment(moment)}>删除</button>}</header>
        {moment.place_name || moment.day ? <p className="moment-location">{[moment.place_name, moment.day].filter(Boolean).join(" · ")}</p> : null}
        <p className="moment-body">{moment.body}</p>
        {moment.media.length > 0 && <div className={`moment-gallery ${moment.media.length === 1 ? "single" : ""}`}>{moment.media.map((asset) => <PrivatePhoto key={asset.id} assetId={asset.id} alt={moment.place_name ? `${moment.place_name} 的旅行照片` : "旅行照片"} />)}</div>}
        <div className="moment-controls">
          <button className={`moment-reaction ${moment.reacted_by_me ? "selected" : ""}`} onClick={() => void toggleReaction(moment)} aria-pressed={moment.reacted_by_me}>♥ {moment.reaction_count || "喜欢"}</button>
          <button className="moment-text-action" onClick={() => void toggleComments(moment)}>评论 {moment.comment_count ? `· ${moment.comment_count}` : ""}</button>
          {own && <><button className="moment-text-action" disabled={busy} onClick={() => void toggleAudience(moment)}>{moment.audience === "FRIENDS" ? "设为私密" : "分享给好友"}</button><button className="moment-text-action" disabled={busy} onClick={() => void generateCaption(moment)}>AI 文案草稿</button>{moment.memory_extraction_enabled && moment.trip_id && <button className="moment-text-action" disabled={busy} onClick={() => void addMemorySuggestion(moment)}>提取旅行记忆建议</button>}</>}
        </div>
        {own && captions[moment.id] !== undefined && <div className="moment-ai-box"><label>AI 文案草稿（请检查并修改后应用或复制）<textarea value={captions[moment.id]} onChange={(event) => setCaptions((current) => ({ ...current, [moment.id]: event.target.value }))} rows={3} /></label><div className="social-actions"><button className="primary" disabled={busy || !captions[moment.id]?.trim()} onClick={() => void applyCaption(moment)}>应用为动态文字</button><button className="secondary" disabled={!captions[moment.id]?.trim()} onClick={() => void copyCaption(moment.id)}>复制文案</button><button className="secondary" onClick={() => setCaptions((current) => ({ ...current, [moment.id]: "" }))}>放弃</button></div><small>外部社交平台仍需你手动发布。</small></div>}
        {own && suggestions[moment.id] && <div className="moment-ai-box"><strong>{suggestions[moment.id].status === "PENDING" ? "待确认的旅行记忆建议" : `已${suggestions[moment.id].status === "CONFIRMED" ? "确认" : "拒绝"}`}</strong><p>依据原文：“{suggestions[moment.id].evidence}”</p><textarea value={suggestions[moment.id].candidate} disabled={suggestions[moment.id].status !== "PENDING"} onChange={(event) => setSuggestions((current) => ({ ...current, [moment.id]: { ...current[moment.id], candidate: event.target.value } }))} rows={2} />{suggestions[moment.id].status === "PENDING" && <div className="social-actions"><button className="primary" disabled={busy} onClick={() => void reviewSuggestion(moment, suggestions[moment.id], true)}>确认写入旅行记忆</button><button className="secondary" disabled={busy} onClick={() => void reviewSuggestion(moment, suggestions[moment.id], false)}>拒绝</button></div>}<small>确认后仅写入关联旅行的私有记忆，好友看不到；会影响后续规划建议。</small></div>}
        {openComments[moment.id] && <section className="moment-comments"><h3>评论</h3>{(comments[moment.id] ?? []).map((comment) => <div className="moment-comment" key={comment.id}><p><strong>{comment.author_email}</strong> {comment.body}</p>{(comment.author_user_id === userId || own) && <button className="moment-text-action" onClick={() => void removeComment(moment, comment)}>删除</button>}</div>)}<div className="moment-comment-form"><input aria-label="写评论" maxLength={2000} value={commentText[moment.id] ?? ""} onChange={(event) => setCommentText((current) => ({ ...current, [moment.id]: event.target.value }))} placeholder="写一条评论" /><button className="secondary" disabled={busy || !commentText[moment.id]?.trim()} onClick={() => void sendComment(moment)}>发送</button></div></section>}
      </article>;
    })}</div>}
  </main>;
}
