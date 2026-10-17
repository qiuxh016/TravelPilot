"use client";

import Link from "next/link";
import { FormEvent, useEffect, useRef, useState } from "react";
import {
  acceptFriendRequest, blockFriend, declineFriendRequest,
  deleteAccountAvatar, getAccountAvatarBlob, getAccountProfile, listBlockedFriends,
  listFriendRequests, listFriends, removeFriend, unblockFriend, updateAccountProfile,
  uploadAccountAvatar, type AccountUser, type Friendship,
} from "../lib/api";

type FriendTab = "friends" | "requests" | "blocked";

function emitProfileUpdate(user: AccountUser) {
  window.dispatchEvent(new CustomEvent("travelpilot:profile-updated", { detail: { user } }));
}

function initials(user: AccountUser | null) {
  const label = user?.display_name || user?.email || "旅";
  return label.trim().slice(0, 1).toUpperCase();
}

export function AccountProfilePanel() {
  const [profile, setProfile] = useState<AccountUser | null>(null);
  const [avatarUrl, setAvatarUrl] = useState("");
  const [friends, setFriends] = useState<Friendship[]>([]);
  const [requests, setRequests] = useState<Friendship[]>([]);
  const [blocked, setBlocked] = useState<Friendship[]>([]);
  const [tab, setTab] = useState<FriendTab>("friends");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [friendBusy, setFriendBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<{ text: string; error?: boolean } | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    let active = true;
    let objectUrl = "";
    void Promise.allSettled([getAccountProfile(), listFriends(), listFriendRequests(), listBlockedFriends()])
      .then(async ([profileResult, friendResult, requestResult, blockedResult]) => {
        if (!active) return;
        if (profileResult.status === "fulfilled") {
          const user = profileResult.value;
          setProfile(user);
          if (user.has_avatar) {
            try { objectUrl = await getAccountAvatarBlob(); if (active) setAvatarUrl(objectUrl); else URL.revokeObjectURL(objectUrl); }
            catch { /* Keep initials visible if the private avatar endpoint is unavailable. */ }
          }
        } else {
          setMessage({ text: profileResult.reason instanceof Error ? profileResult.reason.message : "个人资料暂时无法加载。", error: true });
        }
        if (friendResult.status === "fulfilled") setFriends(friendResult.value);
        if (requestResult.status === "fulfilled") setRequests(requestResult.value);
        if (blockedResult.status === "fulfilled") setBlocked(blockedResult.value);
        if ([friendResult, requestResult, blockedResult].some((result) => result.status === "rejected")) {
          setMessage({ text: "好友列表暂时不完整，可以打开好友圈刷新。", error: true });
        }
      })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, []);

  function replaceAvatar(nextUrl: string) {
    setAvatarUrl((current) => { if (current) URL.revokeObjectURL(current); return nextUrl; });
  }

  async function saveProfile(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!profile) return;
    setSaving(true); setMessage(null);
    try {
      const updated = await updateAccountProfile({ display_name: profile.display_name, home_city: profile.home_city, bio: profile.bio });
      setProfile(updated); emitProfileUpdate(updated); setMessage({ text: "个人资料已保存。" });
    } catch (error) { setMessage({ text: error instanceof Error ? error.message : "资料保存失败，请重试。", error: true }); }
    finally { setSaving(false); }
  }

  async function chooseAvatar(file?: File) {
    if (!file) return;
    if (!file.type.startsWith("image/")) { setMessage({ text: "请选择图片文件。", error: true }); return; }
    if (file.size > 5 * 1024 * 1024) { setMessage({ text: "头像不能超过 5 MB。", error: true }); return; }
    setSaving(true); setMessage(null);
    try {
      const updated = await uploadAccountAvatar(file);
      const nextUrl = await getAccountAvatarBlob();
      setProfile(updated); replaceAvatar(nextUrl); emitProfileUpdate(updated); setMessage({ text: "头像已更新，图片位置等信息已从文件中清除。" });
    } catch (error) { setMessage({ text: error instanceof Error ? error.message : "头像上传失败，请重试。", error: true }); }
    finally { setSaving(false); if (fileRef.current) fileRef.current.value = ""; }
  }

  async function removeAvatar() {
    if (!profile?.has_avatar) return;
    setSaving(true); setMessage(null);
    try {
      await deleteAccountAvatar();
      const updated = { ...profile, has_avatar: false };
      setProfile(updated); replaceAvatar(""); emitProfileUpdate(updated); setMessage({ text: "已移除头像。" });
    } catch (error) { setMessage({ text: error instanceof Error ? error.message : "头像移除失败。", error: true }); }
    finally { setSaving(false); }
  }

  async function loadFriends() {
    const [friendRows, requestRows, blockedRows] = await Promise.all([listFriends(), listFriendRequests(), listBlockedFriends()]);
    setFriends(friendRows); setRequests(requestRows); setBlocked(blockedRows);
  }

  async function friendAction(row: Friendship, action: "accept" | "decline" | "remove" | "block" | "unblock") {
    const destructive = action === "remove" || action === "block";
    if (destructive && !window.confirm(action === "block" ? `屏蔽 ${row.email}？这会撤销双方现有的旅行分享授权。` : `移除好友 ${row.email}？这会撤销双方现有的旅行分享授权。`)) return;
    setFriendBusy(row.id); setMessage(null);
    try {
      if (action === "accept") await acceptFriendRequest(row.id);
      if (action === "decline") await declineFriendRequest(row.id);
      if (action === "remove") await removeFriend(row.id);
      if (action === "block") await blockFriend(row.id);
      if (action === "unblock") await unblockFriend(row.id);
      await loadFriends(); setMessage({ text: "好友关系已更新。" });
    } catch (error) { setMessage({ text: error instanceof Error ? error.message : "好友操作失败。", error: true }); }
    finally { setFriendBusy(null); }
  }

  const rows = tab === "friends" ? friends : tab === "requests" ? requests : blocked;

  return <>
    {message ? <p className={`profile-feedback${message.error ? " is-error" : ""}`} role={message.error ? "alert" : "status"}>{message.text}</p> : null}
    <section className="profile-section" aria-labelledby="profile-title">
      <div className="profile-section-title"><span className="profile-route-stop" aria-hidden="true">01</span><div><h2 id="profile-title">个人资料</h2><p>让旅行顾问和好友用你喜欢的方式认识你。</p></div></div>
      {loading || !profile ? <p className="profile-loading">正在读取个人资料…</p> : <form className="profile-editor" onSubmit={(event) => void saveProfile(event)}>
        <div className="profile-avatar-column">
          <div className="profile-avatar" aria-label="当前头像">{avatarUrl ? <img src={avatarUrl} alt="个人头像" /> : <span>{initials(profile)}</span>}</div>
          <input ref={fileRef} className="visually-hidden-input" type="file" accept="image/jpeg,image/png,image/webp" aria-label="选择头像图片" onChange={(event) => void chooseAvatar(event.target.files?.[0])} />
          <button className="secondary profile-avatar-button" type="button" disabled={saving} onClick={() => fileRef.current?.click()}>{profile.has_avatar ? "更换头像" : "上传头像"}</button>
          {profile.has_avatar ? <button className="profile-text-button" type="button" disabled={saving} onClick={() => void removeAvatar()}>移除头像</button> : null}
          <small>JPEG、PNG 或 WebP，最大 5 MB</small>
        </div>
        <div className="profile-fields">
          <label>昵称<input value={profile.display_name} required maxLength={48} placeholder="给自己取个旅行昵称" onChange={(event) => setProfile({ ...profile, display_name: event.target.value })} /></label>
          <label>常用出发地<input value={profile.home_city} maxLength={120} placeholder="例如：杭州" onChange={(event) => setProfile({ ...profile, home_city: event.target.value })} /></label>
          <label className="profile-bio-label">个人简介<textarea value={profile.bio} maxLength={240} rows={3} placeholder="喜欢怎样的旅行？可以在这里写下来。" onChange={(event) => setProfile({ ...profile, bio: event.target.value })} /><small>{profile.bio.length}/240</small></label>
          <label className="profile-email-label">用户名 <span>用于登录</span><input value={profile.username} readOnly /></label>
          <label className="profile-email-label">邮箱 <span>不可修改</span><input value={profile.email} readOnly /></label>
          <button className="primary profile-save-button" type="submit" disabled={saving || !profile.display_name.trim()}>{saving ? "正在保存…" : "保存资料"}</button>
        </div>
      </form>}
    </section>

    <div className="profile-route-divider" aria-hidden="true"><span>✳</span><i /><span>⌖</span><i /><span>✳</span></div>

    <section className="profile-section friends-settings" aria-labelledby="friends-settings-title">
      <div className="profile-section-title"><span className="profile-route-stop" aria-hidden="true">02</span><div><h2 id="friends-settings-title">好友管理</h2><p>先成为好友，再由你决定是否分享某一趟旅行。</p></div><Link className="profile-manage-link" href="/friends">打开好友圈 ↗</Link></div>
      <Link className="secondary profile-friend-search-link" href="/friends">搜索用户并添加好友 ↗</Link>
      <div className="profile-friend-tabs" role="tablist" aria-label="好友分类">
        <button type="button" role="tab" aria-selected={tab === "friends"} className={tab === "friends" ? "is-selected" : ""} onClick={() => setTab("friends")}>好友 <span>{friends.length}</span></button>
        <button type="button" role="tab" aria-selected={tab === "requests"} className={tab === "requests" ? "is-selected" : ""} onClick={() => setTab("requests")}>申请 <span>{requests.length}</span></button>
        <button type="button" role="tab" aria-selected={tab === "blocked"} className={tab === "blocked" ? "is-selected" : ""} onClick={() => setTab("blocked")}>已屏蔽 <span>{blocked.length}</span></button>
      </div>
      {rows.length === 0 ? <div className="profile-friends-empty"><span aria-hidden="true">⌁</span><p>{tab === "friends" ? "好友列表还空着，邀请一位旅伴一起出发。" : tab === "requests" ? "目前没有待处理申请。" : "目前没有已屏蔽的好友。"}</p></div> : <ul className="profile-friend-list">{rows.map((row) => <li key={row.id}>
        <span className="friend-avatar" aria-hidden="true">{(row.display_name || row.email).slice(0, 1).toUpperCase()}</span>
        <div className="profile-friend-identity"><strong>{row.display_name || row.email.split("@")[0]}</strong><small>{row.email}{tab === "requests" ? ` · ${row.direction === "INCOMING" ? "对方发来的申请" : "等待对方接受"}` : ""}</small></div>
        <div className="profile-friend-actions">{tab === "friends" ? <><button className="profile-text-button" disabled={friendBusy !== null} onClick={() => void friendAction(row, "block")}>屏蔽</button><button className="secondary" disabled={friendBusy !== null} onClick={() => void friendAction(row, "remove")}>移除好友</button></> : tab === "requests" && row.direction === "INCOMING" ? <><button className="primary" disabled={friendBusy !== null} onClick={() => void friendAction(row, "accept")}>接受</button><button className="secondary" disabled={friendBusy !== null} onClick={() => void friendAction(row, "decline")}>忽略</button></> : tab === "blocked" ? <button className="secondary" disabled={friendBusy !== null} onClick={() => void friendAction(row, "unblock")}>取消屏蔽</button> : null}</div>
      </li>)}</ul>}
      <p className="profile-sharing-note">好友关系不会自动开放旅行。每趟旅行的分享对象和范围仍由你单独授权。</p>
    </section>
  </>;
}
