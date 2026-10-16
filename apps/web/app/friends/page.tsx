"use client";

import Link from "next/link";
import { FormEvent, useEffect, useState } from "react";
import {
  acceptFriendRequest,
  blockFriend,
  createFriendRequest,
  declineFriendRequest,
  getSharedTrip,
  listFriendRequests,
  listBlockedFriends,
  listFriends,
  listSharedTrips,
  removeFriend,
  unblockFriend,
  type Friendship,
  type SharedTrip,
  type SharedTripDetail,
} from "../../lib/api";

export default function FriendsPage() {
  const [friends, setFriends] = useState<Friendship[]>([]);
  const [requests, setRequests] = useState<Friendship[]>([]);
  const [blocked, setBlocked] = useState<Friendship[]>([]);
  const [sharedTrips, setSharedTrips] = useState<SharedTrip[]>([]);
  const [selectedTrip, setSelectedTrip] = useState<SharedTripDetail | null>(null);
  const [email, setEmail] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  async function load() {
    try {
      setLoading(true);
      setError("");
      const [friendRows, requestRows, blockedRows, tripRows] = await Promise.all([listFriends(), listFriendRequests(), listBlockedFriends(), listSharedTrips()]);
      setFriends(friendRows);
      setRequests(requestRows);
      setBlocked(blockedRows);
      setSharedTrips(tripRows);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "好友信息暂时无法加载");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { void load(); }, []);

  async function sendRequest(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true); setNotice(""); setError("");
    try {
      const result = await createFriendRequest(email.trim());
      setNotice(result.message);
      setEmail("");
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "申请未能提交");
    } finally { setBusy(false); }
  }

  async function handleRequest(row: Friendship, action: "accept" | "decline") {
    setBusy(true); setError("");
    try {
      if (action === "accept") await acceptFriendRequest(row.id);
      else await declineFriendRequest(row.id);
      await load();
    } catch (cause) { setError(cause instanceof Error ? cause.message : "操作失败"); }
    finally { setBusy(false); }
  }

  async function handleFriend(row: Friendship, action: "remove" | "block") {
    const prompt = action === "block"
      ? `屏蔽 ${row.email}？这会撤销双方现有的旅行分享授权。`
      : `移除好友 ${row.email}？这会撤销双方现有的旅行分享授权。`;
    if (!window.confirm(prompt)) return;
    setBusy(true); setError("");
    try {
      if (action === "block") await blockFriend(row.id);
      else await removeFriend(row.id);
      await load();
    } catch (cause) { setError(cause instanceof Error ? cause.message : "操作失败"); }
    finally { setBusy(false); }
  }

  async function handleUnblock(row: Friendship) {
    setBusy(true); setError("");
    try { await unblockFriend(row.id); await load(); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "取消屏蔽失败"); }
    finally { setBusy(false); }
  }

  async function openSharedTrip(trip: SharedTrip) {
    setError(""); setSelectedTrip(null);
    try { setSelectedTrip(await getSharedTrip(trip.id)); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "此旅行当前不可查看"); await load(); }
  }

  return <main className="shell social-page">
    <header className="hero">
      <p className="eyebrow">好友与分享</p>
      <h1>好友圈</h1>
      <p className="lede">先建立好友关系，再由旅行所有者逐个授予只读权限。成为好友不会自动开放任何旅行。</p>
    </header>
    {error && <p className="social-message is-error" role="alert">{error}</p>}
    {notice && <p className="social-message" role="status">{notice}</p>}
    <section className="social-card">
      <h2>添加好友</h2>
      <p>输入对方注册邮箱发送申请。为了保护账户隐私，系统会对所有邮箱显示相同的提交结果。</p>
      <form className="social-form" onSubmit={sendRequest}>
        <label>好友邮箱<input type="email" required maxLength={320} value={email} onChange={(event) => setEmail(event.target.value)} placeholder="friend@example.com" /></label>
        <button className="primary" disabled={busy}>发送申请</button>
      </form>
    </section>
    <div className="social-grid">
      <section className="social-card">
        <h2>好友申请 <span className="social-count">{requests.length}</span></h2>
        {loading ? <p className="status">正在加载……</p> : requests.length === 0 ? <p className="social-empty">暂无待处理申请。</p> : <ul className="social-list">{requests.map((row) => <li key={row.id}>
          <div><strong>{row.email}</strong><small>{row.direction === "INCOMING" ? "对方发来的申请" : "等待对方接受"}</small></div>
          {row.direction === "INCOMING" ? <div className="social-actions"><button className="primary" disabled={busy} onClick={() => void handleRequest(row, "accept")}>接受</button><button className="secondary" disabled={busy} onClick={() => void handleRequest(row, "decline")}>忽略</button><button className="social-danger" disabled={busy} onClick={() => void handleFriend(row, "block")}>屏蔽</button></div> : null}
        </li>)}</ul>}
      </section>
      <section className="social-card">
        <h2>我的好友 <span className="social-count">{friends.length}</span></h2>
        {loading ? <p className="status">正在加载……</p> : friends.length === 0 ? <p className="social-empty">接受好友申请后，可在旅行设置中授予只读分享。</p> : <ul className="social-list">{friends.map((row) => <li key={row.id}>
          <div><strong>{row.email}</strong><small>好友 · 不会自动获得旅行访问权</small></div>
          <div className="social-actions"><button className="secondary" disabled={busy} onClick={() => void handleFriend(row, "remove")}>移除</button><button className="social-danger" disabled={busy} onClick={() => void handleFriend(row, "block")}>屏蔽</button></div>
        </li>)}</ul>}
      </section>
    </div>
    {blocked.length ? <section className="social-card"><h2>已屏蔽</h2><ul className="social-list">{blocked.map((row) => <li key={row.id}><div><strong>{row.email}</strong><small>屏蔽期间无法互相分享旅行</small></div><button className="secondary" disabled={busy} onClick={() => void handleUnblock(row)}>取消屏蔽</button></li>)}</ul></section> : null}
    <section className="social-card">
      <h2>好友分享给我的旅行 <span className="social-count">{sharedTrips.length}</span></h2>
      {loading ? <p className="status">正在加载……</p> : sharedTrips.length === 0 ? <p className="social-empty">收到旅行分享后会显示在这里。</p> : <ul className="social-list">{sharedTrips.map((trip) => <li key={trip.id}>
        <div><strong>{trip.destination}</strong><small>{trip.owner_email} · {trip.start_date} 至 {trip.end_date} · {trip.scope === "TRIP_AND_PLAN" ? "含只读计划" : "仅旅行摘要"}</small></div>
        <button className="secondary" onClick={() => void openSharedTrip(trip)}>查看</button>
      </li>)}</ul>}
      {selectedTrip && <div className="shared-trip-detail"><div><h3>{selectedTrip.destination}</h3><p>{selectedTrip.start_date} 至 {selectedTrip.end_date} · 分享者 {selectedTrip.owner_email}</p></div>
        {selectedTrip.scope === "TRIP_AND_PLAN" ? <div><h4>只读计划 {selectedTrip.plan_version ? `· v${selectedTrip.plan_version}` : ""}</h4>{selectedTrip.plan_nodes.length ? <ol>{selectedTrip.plan_nodes.map((node, index) => <li key={`${node.day}-${node.start_time}-${index}`}><strong>{node.day} · {node.start_time}–{node.end_time}</strong><span>{node.name}</span></li>)}</ol> : <p className="social-empty">当前还没有已保存的计划。</p>}</div> : <p className="social-empty">所有者仅分享了旅行摘要，没有开放行程详情。</p>}
      </div>}
    </section>
    <p className="social-footnote"><Link href="/trips">返回我的旅行</Link></p>
  </main>;
}
