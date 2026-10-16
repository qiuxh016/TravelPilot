"use client";

import Link from "next/link";
import { FormEvent, useEffect, useState } from "react";
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
  const [commentText, setCommentText] = useState<Record<string, string>>({});
  const [comments, setComments] = useState<Record<string, MomentComment[]>>({});
  const [openComments, setOpenComments] = useState<Record<string, boolean>>({});
  const [captions, setCaptions] = useState<Record<string, string>>({});
  const [suggestions, setSuggestions] = useState<Record<string, MemorySuggestion>>({});
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  async function load() {
    try {
      setLoading(true); setError("");
      const [rows, tripRows, user] = await Promise.all([listMoments(), listTrips(), getCurrentUser()]);
      setMoments(rows); setTrips(tripRows); setUserId(user.id);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "旅行动态暂时无法加载"); }
    finally { setLoading(false); }
  }

  useEffect(() => { void load(); }, []);

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
      setMemoryConsent(false); setPhotos([]); setNotice(audience === "FRIENDS" ? "已发布到好友圈。" : "已保存为仅自己可见。相册照片已自动移除 EXIF/GPS 元数据。");
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
    <header className="hero">
      <p className="eyebrow">旅行记录</p><h1>旅行动态</h1>
      <p className="lede">写下此刻的感受，添加照片并决定分享范围。照片会在服务器端重新编码并移除 EXIF/GPS 元数据。</p>
    </header>
    {error && <p className="social-message is-error" role="alert">{error}</p>}
    {notice && <p className="social-message" role="status">{notice}</p>}
    <section className="social-card moment-composer">
      <h2>记录一个旅行片段</h2>
      <form className="moment-form" onSubmit={publish}>
        <label>写下旅行感受<textarea required minLength={1} maxLength={4000} value={body} onChange={(event) => setBody(event.target.value)} placeholder="今天最喜欢的一个瞬间是什么？" rows={4} /></label>
        <div className="moment-fields">
          <label>关联旅行（可选）<select value={tripId} onChange={(event) => setTripId(event.target.value)}><option value="">不关联旅行</option>{trips.map((trip) => <option key={trip.id} value={trip.id}>{trip.destination} · {trip.start_date}</option>)}</select></label>
          <label>日期<input type="date" value={day} onChange={(event) => setDay(event.target.value)} /></label>
          <label>地点<input maxLength={160} value={placeName} onChange={(event) => setPlaceName(event.target.value)} placeholder="可选" /></label>
          <label>分享范围<select value={audience} onChange={(event) => setAudience(event.target.value as "PRIVATE" | "FRIENDS")}><option value="PRIVATE">仅自己（默认）</option><option value="FRIENDS">好友圈</option></select></label>
        </div>
        <label>照片（JPEG、PNG 或 WebP，最多 12 张，每张 10 MB）<input type="file" accept="image/jpeg,image/png,image/webp" multiple onChange={(event) => setPhotos(Array.from(event.target.files ?? []).slice(0, 12))} /></label>
        {photos.length > 0 && <p className="moment-photo-selection">已选择 {photos.length} 张：{photos.map((photo) => photo.name).join("、")}</p>}
        <label className="moment-consent"><input type="checkbox" checked={memoryConsent} disabled={!tripId} onChange={(event) => setMemoryConsent(event.target.checked)} />允许从这条文字中提取旅行记忆建议。照片不会用于识别心情或生成记忆。</label>
        {!tripId && <small className="moment-help">如需将确认的记忆用于规划，请先关联自己的 Trip。</small>}
        <button className="primary" disabled={busy}>{busy ? "正在保存……" : audience === "FRIENDS" ? "保存并分享给好友" : "保存为私密旅行记录"}</button>
      </form>
    </section>
    <div className="moments-heading"><h2>动态</h2><Link href="/friends">管理好友与分享权限 →</Link></div>
    {loading ? <p className="status">正在加载旅行动态……</p> : moments.length === 0 ? <div className="social-card social-empty">还没有旅行动态。你可以先写一段感受，默认只有自己可见。</div> : <div className="moment-feed">{moments.map((moment) => {
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
