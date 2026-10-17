"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { createTripShare, getTrip, listFriends, listTripShares, revokeTripShare, type Friendship, type Trip, type TripShare } from "../lib/api";

type TripSharingToolProps = { tripId: string; onClose?: () => void };

function SharingControls({ tripId }: { tripId: string }) {
  const [trip, setTrip] = useState<Trip | null>(null);
  const [friends, setFriends] = useState<Friendship[]>([]);
  const [shares, setShares] = useState<TripShare[]>([]);
  const [friendId, setFriendId] = useState("");
  const [scope, setScope] = useState<TripShare["scope"]>("TRIP_SUMMARY");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  async function load() {
    try {
      setLoading(true); setError("");
      const [tripData, friendRows, shareRows] = await Promise.all([getTrip(tripId), listFriends(), listTripShares(tripId)]);
      setTrip(tripData); setFriends(friendRows); setShares(shareRows);
      setFriendId((current) => current && friendRows.some((friend) => friend.friend_user_id === current) ? current : friendRows[0]?.friend_user_id ?? "");
    } catch (cause) { setError(cause instanceof Error ? cause.message : "分享设置暂时无法加载"); }
    finally { setLoading(false); }
  }

  useEffect(() => { void load(); }, [tripId]);

  async function grant() {
    if (!friendId) return;
    setBusy(true); setError(""); setNotice("");
    try {
      await createTripShare(tripId, friendId, scope);
      setNotice("旅行只读访问已授予。好友可在“好友圈”中查看。");
      await load();
    } catch (cause) { setError(cause instanceof Error ? cause.message : "授权失败"); }
    finally { setBusy(false); }
  }

  async function revoke(share: TripShare) {
    if (!window.confirm(`撤销 ${share.email} 对此旅行的访问？`)) return;
    setBusy(true); setError(""); setNotice("");
    try { await revokeTripShare(tripId, share.id); setNotice(`已撤销 ${share.email} 的访问。`); await load(); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "撤销失败"); }
    finally { setBusy(false); }
  }

  return <>
    <div className="trip-sharing-intro"><p className="eyebrow">旅行权限</p><h2>分享这段旅程</h2><p>{trip ? `${trip.destination} · ${trip.start_date} 至 ${trip.end_date}` : "只读分享由你逐个授权，随时可以撤销。"}</p></div>
    {error && <p className="social-message is-error" role="alert">{error}</p>}
    {notice && <p className="social-message" role="status">{notice}</p>}
    {loading ? <p className="status">正在读取分享权限……</p> : <>
      <section className="social-card">
        <h2>授予好友只读访问</h2>
        <p>好友关系不会自动开放此旅行。授权后，对方不能修改你的计划、收藏地点或旅行设置。</p>
        {friends.length === 0 ? <div className="social-empty">你还没有已接受的好友。<Link href="/friends">先添加好友</Link>，然后回来设置分享。</div> : <div className="social-form social-share-form">
          <label>选择好友<select value={friendId} onChange={(event) => setFriendId(event.target.value)}>{friends.map((friend) => <option key={friend.friend_user_id} value={friend.friend_user_id}>{friend.display_name || friend.email}</option>)}</select></label>
          <label>可查看内容<select value={scope} onChange={(event) => setScope(event.target.value as TripShare["scope"])}><option value="TRIP_SUMMARY">旅行摘要（目的地和日期）</option><option value="TRIP_AND_PLAN">旅行摘要与当前计划（只读）</option></select></label>
          <button className="primary" disabled={busy || !friendId} onClick={() => void grant()}>{busy ? "正在保存…" : "保存授权"}</button>
        </div>}
      </section>
      <section className="social-card">
        <h2>当前有权访问的好友 <span className="social-count">{shares.length}</span></h2>
        {shares.length === 0 ? <p className="social-empty">目前没有好友可以查看这趟旅行。</p> : <ul className="social-list">{shares.map((share) => <li key={share.id}>
          <div><strong>{share.email}</strong><small>{share.scope === "TRIP_AND_PLAN" ? "旅行摘要与计划" : "仅旅行摘要"} · 已授权只读</small></div>
          <button className="social-danger" disabled={busy} onClick={() => void revoke(share)}>撤销访问</button>
        </li>)}</ul>}
      </section>
    </>}
  </>;
}

export function TripSharingTool({ tripId, onClose }: TripSharingToolProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    if (onClose) dialogRef.current?.showModal();
    return () => dialogRef.current?.close();
  }, [onClose]);

  if (onClose) return <dialog className="trip-sharing-dialog" ref={dialogRef} onCancel={onClose} onClick={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    <section className="trip-sharing-dialog-panel">
      <header className="trip-sharing-dialog-header"><div><p className="eyebrow">旅行设置</p><strong>分享权限</strong></div><button type="button" className="moment-dialog-close" aria-label="关闭分享设置" onClick={onClose}>×</button></header>
      <div className="trip-sharing-dialog-body"><SharingControls tripId={tripId} /></div>
    </section>
  </dialog>;

  return <main className="shell social-page trip-sharing-page">
    <nav className="nav"><Link className="trip-import-back" href={`/trips/${tripId}`}>← 返回旅行概览</Link><span className="status">分享权限</span></nav>
    <SharingControls tripId={tripId} />
  </main>;
}
