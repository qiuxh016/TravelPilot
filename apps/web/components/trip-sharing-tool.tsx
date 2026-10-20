"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import {
  createTripShare, getTrip, inviteTripMember, listFriends, listTripMembers, listTripShares,
  revokeTripMember, revokeTripShare, updateTripMemberRole,
  type Friendship, type Trip, type TripMember, type TripMemberRole, type TripShare,
} from "../lib/api";

type TripSharingToolProps = { tripId: string; onClose?: () => void };

function SharingControls({ tripId }: { tripId: string }) {
  const [trip, setTrip] = useState<Trip | null>(null);
  const [friends, setFriends] = useState<Friendship[]>([]);
  const [shares, setShares] = useState<TripShare[]>([]);
  const [members, setMembers] = useState<TripMember[]>([]);
  const [friendId, setFriendId] = useState("");
  const [scope, setScope] = useState<TripShare["scope"]>("TRIP_SUMMARY");
  const [memberRole, setMemberRole] = useState<TripMemberRole>("EDITOR");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  async function load() {
    try {
      setLoading(true); setError("");
      const [tripData, friendRows, shareRows, memberRows] = await Promise.all([
        getTrip(tripId), listFriends(), listTripShares(tripId), listTripMembers(tripId),
      ]);
      setTrip(tripData); setFriends(friendRows); setShares(shareRows); setMembers(memberRows);
      const activeIds = new Set(memberRows.filter((member) => member.status === "PENDING" || member.status === "ACCEPTED").map((member) => member.user_id));
      const eligibleFriends = friendRows.filter((friend) => !activeIds.has(friend.friend_user_id));
      setFriendId((current) => current && eligibleFriends.some((friend) => friend.friend_user_id === current)
        ? current
        : eligibleFriends[0]?.friend_user_id ?? "");
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

  async function inviteMember() {
    if (!friendId) return;
    setBusy(true); setError(""); setNotice("");
    try {
      await inviteTripMember(tripId, friendId, memberRole);
      setNotice("已发送小团体旅行邀请；对方接受后才能查看这段旅行。");
      await load();
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : "邀请失败";
      setError(message.includes("read-only") || message.includes("只读")
        ? "这位好友已有只读分享。请先在下方撤销只读权限，再邀请加入小团体。"
        : message);
    } finally { setBusy(false); }
  }

  async function changeMemberRole(member: TripMember, role: TripMemberRole) {
    setBusy(true); setError(""); setNotice("");
    try {
      await updateTripMemberRole(tripId, member.id, role);
      setNotice(`已将 ${member.display_name || member.username} 设为${role === "EDITOR" ? "编辑者" : "查看者"}。`);
      await load();
    } catch (cause) { setError(cause instanceof Error ? cause.message : "修改成员权限失败"); }
    finally { setBusy(false); }
  }

  async function removeMember(member: TripMember) {
    const name = member.display_name || member.username;
    if (!window.confirm(`撤销 ${name} 对这段旅行的访问？`)) return;
    setBusy(true); setError(""); setNotice("");
    try {
      await revokeTripMember(tripId, member.id);
      setNotice(`已撤销 ${name} 的旅行访问权限。`);
      await load();
    } catch (cause) { setError(cause instanceof Error ? cause.message : "撤销成员权限失败"); }
    finally { setBusy(false); }
  }

  const activeMemberIds = new Set(members.filter((member) => member.status === "PENDING" || member.status === "ACCEPTED").map((member) => member.user_id));
  const availableFriends = friends.filter((friend) => !activeMemberIds.has(friend.friend_user_id));

  return <>
    <div className="trip-sharing-intro"><p className="eyebrow">旅行权限</p><h2>分享这段旅程</h2><p>{trip ? `${trip.destination} · ${trip.start_date} 至 ${trip.end_date}` : "只读分享由你逐个授权，随时可以撤销。"}</p></div>
    {error && <p className="social-message is-error" role="alert">{error}</p>}
    {notice && <p className="social-message" role="status">{notice}</p>}
    {loading ? <p className="status">正在读取分享权限……</p> : <>
      <section className="social-card">
        <h2>创建小团体旅行</h2>
        <p>邀请好友共同查看和规划。对方接受前看不到旅行内容；编辑者可以修改，查看者只能查看和讨论。个人记忆与私人记录不会共享。</p>
        {availableFriends.length === 0 ? <div className="social-empty">没有可邀请的好友。你可以先添加好友，或在下方撤销已有成员权限后重新邀请。<Link href="/friends">管理好友</Link></div> : <div className="social-form social-share-form">
          <label>邀请好友<select value={friendId} onChange={(event) => setFriendId(event.target.value)}>{availableFriends.map((friend) => <option key={friend.friend_user_id} value={friend.friend_user_id}>{friend.display_name || friend.email}</option>)}</select></label>
          <label>成员权限<select value={memberRole} onChange={(event) => setMemberRole(event.target.value as TripMemberRole)}><option value="EDITOR">编辑者 · 可共同修改</option><option value="VIEWER">查看者 · 只读</option></select></label>
          <button className="primary" disabled={busy || !friendId} onClick={() => void inviteMember()}>{busy ? "正在发送邀请…" : "邀请加入旅行"}</button>
        </div>}
        <ul className="social-list trip-member-list">{members.map((member) => <li key={member.id}>
          <div><strong>{member.display_name || member.username}</strong><small>{member.status === "PENDING" ? "等待接受邀请" : member.status === "ACCEPTED" ? "已加入小团体" : member.status === "DECLINED" ? "已拒绝邀请" : "已撤销访问"}</small></div>
          {(member.status === "PENDING" || member.status === "ACCEPTED") && <div className="social-actions">
            <select aria-label={`设置 ${member.display_name || member.username} 的权限`} value={member.role} disabled={busy} onChange={(event) => void changeMemberRole(member, event.target.value as TripMemberRole)}><option value="EDITOR">编辑者</option><option value="VIEWER">查看者</option></select>
            <button className="social-danger" disabled={busy} onClick={() => void removeMember(member)}>撤销访问</button>
          </div>}
        </li>)}</ul>
      </section>
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
