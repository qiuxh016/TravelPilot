"use client";

import Link from "next/link";
import { FormEvent, useEffect, useRef, useState } from "react";
import {
  acceptFriendRequest,
  acceptTripInvitation,
  analyzeFriendConversation,
  blockFriend,
  deletePlanNode,
  createFriendRequestForUser,
  declineFriendRequest,
  getFriendAvatarBlob,
  getFriendMessageImageBlob,
  getOrCreateFriendConversation,
  getSharedTrip,
  getCollaborativeTrip,
  updatePlanNode,
  listFriendRequests,
  listBlockedFriends,
  listFriends,
  listSharedTrips,
  listTripInvitations,
  listCollaborativeTrips,
  listFriendMessages,
  removeFriend,
  searchFriends,
  sendFriendMessage,
  sendFriendMessageImage,
  updateFriendAnalysisConsent,
  unblockFriend,
  declineTripInvitation,
  type FriendSearchResult,
  type FriendMessage,
  type FriendConversation,
  type FriendChatAnalysis,
  type Friendship,
  type SharedTrip,
  type SharedTripDetail,
  type TripInvitation,
  type CollaborativeTrip,
  type CollaborativeTripDetail,
} from "../../lib/api";

function SearchAvatar({ profile }: { profile: FriendSearchResult }) {
  const [url, setUrl] = useState("");
  useEffect(() => {
    let active = true;
    let objectUrl = "";
    if (profile.has_avatar) void getFriendAvatarBlob(profile.user_id).then((next) => {
      objectUrl = next;
      if (active) setUrl(next); else URL.revokeObjectURL(next);
    }).catch(() => undefined);
    return () => { active = false; if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [profile.user_id, profile.has_avatar]);
  return <span className="friend-avatar">{url ? <img src={url} alt="" /> : (profile.display_name || profile.username).slice(0, 1).toUpperCase()}</span>;
}

function ChatImage({ conversationId, message }: { conversationId: string; message: FriendMessage }) {
  const [url, setUrl] = useState("");
  useEffect(() => {
    let active = true;
    let objectUrl = "";
    void getFriendMessageImageBlob(conversationId, message.id).then((value) => {
      objectUrl = value;
      if (active) setUrl(value); else URL.revokeObjectURL(value);
    }).catch(() => undefined);
    return () => { active = false; if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [conversationId, message.id]);
  return url ? <img className="friend-chat-image" src={url} alt="好友发送的图片" /> : <span className="friend-chat-image-loading">图片加载中…</span>;
}

function FriendChatDialog({ friend, onClose }: { friend: Friendship; onClose: () => void }) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const [conversation, setConversation] = useState<FriendConversation | null>(null);
  const [messages, setMessages] = useState<FriendMessage[]>([]);
  const [body, setBody] = useState("");
  const [image, setImage] = useState<File | null>(null);
  const [analysis, setAnalysis] = useState<FriendChatAnalysis | null>(null);
  const [includeImages, setIncludeImages] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    dialogRef.current?.showModal();
    return () => dialogRef.current?.close();
  }, []);
  useEffect(() => {
    let active = true;
    void getOrCreateFriendConversation(friend.friend_user_id).then((item) => {
      if (active) setConversation(item);
    }).catch((cause) => { if (active) setError(cause instanceof Error ? cause.message : "无法打开私聊"); });
    return () => { active = false; };
  }, [friend.friend_user_id]);
  useEffect(() => {
    if (!conversation) return;
    let active = true;
    const refreshMessages = () => void listFriendMessages(conversation.id).then((items) => { if (active) setMessages(items); }).catch((cause) => { if (active) setError(cause instanceof Error ? cause.message : "消息暂时无法加载"); });
    refreshMessages();
    const timer = window.setInterval(refreshMessages, 5000);
    return () => { active = false; window.clearInterval(timer); };
  }, [conversation]);

  async function submitMessage(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!conversation || (!body.trim() && !image)) return;
    setBusy(true); setError("");
    try {
      const sent = image ? await sendFriendMessageImage(conversation.id, image, body.trim()) : await sendFriendMessage(conversation.id, body.trim());
      setMessages((items) => [...items, sent]); setBody(""); setImage(null);
      const input = document.getElementById("friend-chat-image-input") as HTMLInputElement | null;
      if (input) input.value = "";
    } catch (cause) { setError(cause instanceof Error ? cause.message : "消息发送失败"); }
    finally { setBusy(false); }
  }

  async function toggleConsent() {
    if (!conversation) return;
    setBusy(true); setError("");
    try { setConversation(await updateFriendAnalysisConsent(conversation.id, !conversation.self_analysis_consent)); setAnalysis(null); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "同意状态更新失败"); }
    finally { setBusy(false); }
  }

  async function runAnalysis() {
    if (!conversation || !window.confirm(`将最近 10 条私聊${includeImages ? "文字、表情和图片" : "文字和表情"}发送给已配置的模型分析。结果仅供参考，不会自动保存为记忆或修改行程。继续吗？`)) return;
    setBusy(true); setError(""); setAnalysis(null);
    try { setAnalysis(await analyzeFriendConversation(conversation.id, includeImages)); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "分析暂时不可用"); }
    finally { setBusy(false); }
  }

  return <dialog className="friend-chat-dialog" ref={dialogRef} onCancel={onClose}>
    <section className="friend-chat-panel">
      <header className="friend-chat-header"><div><p className="eyebrow">好友私聊</p><h2>{friend.display_name || friend.email.split("@")[0]}</h2></div><button className="moment-dialog-close" aria-label="关闭私聊" onClick={onClose}>×</button></header>
      <div className="friend-chat-messages" aria-live="polite">{!messages.length ? <p className="social-empty">发送一条消息，开始你们的旅行交流。表情也可以直接发送。</p> : messages.map((item) => <article className={`friend-chat-message ${item.sender_user_id === conversation?.friend_user_id ? "is-friend" : "is-mine"}`} key={item.id}>
        {item.body && <p>{item.body}</p>}{item.has_media && conversation && <ChatImage conversationId={conversation.id} message={item} />}<time>{new Date(item.created_at).toLocaleString()}</time>
      </article>)}</div>
      <form className="friend-chat-compose" onSubmit={(event) => void submitMessage(event)}>
        <textarea value={body} maxLength={4000} onChange={(event) => setBody(event.target.value)} placeholder="写下消息，或发送表情 🙂" />
        <div className="friend-chat-compose-actions"><label className="secondary friend-chat-file">添加图片<input id="friend-chat-image-input" type="file" accept="image/jpeg,image/png,image/webp" onChange={(event) => setImage(event.target.files?.[0] ?? null)} /></label><span>{image?.name}</span><button className="primary" disabled={busy || (!body.trim() && !image)}>{busy ? "处理中…" : "发送"}</button></div>
      </form>
      <details className="friend-chat-privacy"><summary>AI 对话分析（需双方同意）</summary>
        <p>私聊默认不分析。双方分别开启后，你仍需每次手动确认；分析内容仅用于生成当前摘要，不会自动写入记忆或行程。</p>
        <label className="friend-chat-consent"><input type="checkbox" checked={conversation?.self_analysis_consent ?? false} disabled={!conversation || busy} onChange={() => void toggleConsent()} />我同意分析本私聊</label>
        <p className="friend-chat-consent-state">对方{conversation?.friend_analysis_consent ? "已同意" : "尚未同意"}</p>
        {conversation?.self_analysis_consent && conversation.friend_analysis_consent && <><label className="friend-chat-consent"><input type="checkbox" checked={includeImages} onChange={(event) => setIncludeImages(event.target.checked)} />分析最近消息中的图片</label><button className="secondary" disabled={busy} onClick={() => void runAnalysis()}>{busy ? "正在分析…" : "手动分析最近对话"}</button></>}
        {analysis && <div className="friend-chat-analysis"><strong>交流摘要</strong><p>{analysis.summary}</p><p>文字表现：{analysis.observed_tone}</p>{analysis.travel_preferences.length > 0 && <p>提到的旅行偏好：{analysis.travel_preferences.join("、")}</p>}{analysis.suggested_reply && <p>回复灵感：{analysis.suggested_reply}</p>}{analysis.evidence.length > 0 && <small>依据：{analysis.evidence.join("；")}</small>}</div>}
      </details>
      {error && <p className="social-message is-error" role="alert">{error}</p>}
    </section>
  </dialog>;
}

export default function FriendsPage() {
  const [friends, setFriends] = useState<Friendship[]>([]);
  const [requests, setRequests] = useState<Friendship[]>([]);
  const [blocked, setBlocked] = useState<Friendship[]>([]);
  const [sharedTrips, setSharedTrips] = useState<SharedTrip[]>([]);
  const [tripInvitations, setTripInvitations] = useState<TripInvitation[]>([]);
  const [collaborativeTrips, setCollaborativeTrips] = useState<CollaborativeTrip[]>([]);
  const [selectedTrip, setSelectedTrip] = useState<SharedTripDetail | null>(null);
  const [selectedCollaborativeTrip, setSelectedCollaborativeTrip] = useState<CollaborativeTripDetail | null>(null);
  const [editingSharedNodeId, setEditingSharedNodeId] = useState("");
  const [sharedNodeForm, setSharedNodeForm] = useState({ day: "", start_time: "", end_time: "" });
  const [chatFriend, setChatFriend] = useState<Friendship | null>(null);
  const [query, setQuery] = useState("");
  const [searchResults, setSearchResults] = useState<FriendSearchResult[]>([]);
  const [hasSearched, setHasSearched] = useState(false);
  const [searchBusy, setSearchBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  async function load() {
    try {
      setLoading(true);
      setError("");
      const [friendRows, requestRows, blockedRows, tripRows, invitationRows, groupTripRows] = await Promise.all([
        listFriends(), listFriendRequests(), listBlockedFriends(), listSharedTrips(),
        listTripInvitations(), listCollaborativeTrips(),
      ]);
      setFriends(friendRows);
      setRequests(requestRows);
      setBlocked(blockedRows);
      setSharedTrips(tripRows);
      setTripInvitations(invitationRows);
      setCollaborativeTrips(groupTripRows);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "好友信息暂时无法加载");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { void load(); }, []);

  async function searchUsers(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSearchBusy(true); setNotice(""); setError(""); setHasSearched(true);
    try {
      setSearchResults(await searchFriends(query.trim()));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "搜索暂时不可用");
    } finally { setSearchBusy(false); }
  }

  async function sendRequest(profile: FriendSearchResult) {
    setBusy(true); setNotice(""); setError("");
    try {
      const result = await createFriendRequestForUser(profile.user_id);
      setNotice(result.message);
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "申请未能提交");
    } finally { setBusy(false); }
  }

  function relationshipState(profile: FriendSearchResult) {
    if (profile.is_self) return "这是你自己的账号";
    if (friends.some((row) => row.friend_user_id === profile.user_id)) return "已是好友";
    const request = requests.find((row) => row.friend_user_id === profile.user_id);
    if (request?.direction === "INCOMING") return "对方已发来申请";
    if (request) return "申请已发送";
    return "";
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

  async function openCollaborativeTrip(trip: CollaborativeTrip) {
    setError(""); setSelectedCollaborativeTrip(null);
    try { setSelectedCollaborativeTrip(await getCollaborativeTrip(trip.id)); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "这段小团体旅行当前不可查看"); await load(); }
  }

  function editSharedNode(node: CollaborativeTripDetail["plan_nodes"][number]) {
    setEditingSharedNodeId(node.id);
    setSharedNodeForm({ day: node.day, start_time: node.start_time.slice(0, 5), end_time: node.end_time.slice(0, 5) });
  }

  async function saveSharedNode() {
    if (!selectedCollaborativeTrip?.plan_id || selectedCollaborativeTrip.plan_version === null || !editingSharedNodeId) return;
    setBusy(true); setError("");
    try {
      await updatePlanNode(selectedCollaborativeTrip.id, selectedCollaborativeTrip.plan_id, editingSharedNodeId, {
        ...sharedNodeForm,
        expected_base_version: selectedCollaborativeTrip.plan_version,
        reason: "小团体成员调整行程",
      });
      setSelectedCollaborativeTrip(await getCollaborativeTrip(selectedCollaborativeTrip.id));
      setEditingSharedNodeId("");
    } catch (cause) { setError(cause instanceof Error ? cause.message : "共享行程更新失败，请刷新后重试"); }
    finally { setBusy(false); }
  }

  async function removeSharedNode(node: CollaborativeTripDetail["plan_nodes"][number]) {
    if (!selectedCollaborativeTrip?.plan_id || selectedCollaborativeTrip.plan_version === null) return;
    if (!window.confirm(`从共同计划中移除“${node.name}”？历史版本会保留。`)) return;
    setBusy(true); setError("");
    try {
      await deletePlanNode(selectedCollaborativeTrip.id, selectedCollaborativeTrip.plan_id, node.id, selectedCollaborativeTrip.plan_version);
      setSelectedCollaborativeTrip(await getCollaborativeTrip(selectedCollaborativeTrip.id));
    } catch (cause) { setError(cause instanceof Error ? cause.message : "移除失败，请刷新后重试"); }
    finally { setBusy(false); }
  }

  async function respondToTripInvitation(invitation: TripInvitation, accept: boolean) {
    setBusy(true); setError(""); setNotice("");
    try {
      if (accept) {
        await acceptTripInvitation(invitation.id);
        setNotice("已加入旅行小团体。现在可以查看这段旅行。");
      } else {
        await declineTripInvitation(invitation.id);
        setNotice("已拒绝这条旅行邀请。");
      }
      await load();
    } catch (cause) { setError(cause instanceof Error ? cause.message : "处理旅行邀请失败"); }
    finally { setBusy(false); }
  }

  return <main className="shell social-page">
    <header className="hero">
      <p className="eyebrow">好友与分享</p>
      <h1>好友圈</h1>
      <p className="lede">好友关系不会自动开放旅行。所有者可以单独分享只读摘要，也可以邀请你加入一个共同规划的小团体。</p>
    </header>
    {error && <p className="social-message is-error" role="alert">{error}</p>}
    {notice && <p className="social-message" role="status">{notice}</p>}
    <section className="social-card">
      <h2>添加好友</h2>
      <p>按用户名、昵称搜索，或输入完整邮箱。确认头像和昵称后再发送申请。仅已验证邮箱的账号会出现在结果中。</p>
      <form className="social-form" onSubmit={(event) => void searchUsers(event)}>
        <label>搜索用户<input required minLength={2} maxLength={320} value={query} onChange={(event) => setQuery(event.target.value)} placeholder="输入用户名、昵称或完整邮箱" /></label>
        <button className="primary" disabled={searchBusy || busy}>{searchBusy ? "正在搜索…" : "搜索用户"}</button>
      </form>
      {hasSearched && <div className="friend-search-results" aria-live="polite">
        {searchResults.length === 0 ? <p className="social-empty">没有找到匹配的已验证账号。若对方尚未注册或验证邮箱，请让对方完成后再搜索。</p> : <ul className="social-list">{searchResults.map((profile) => {
          const state = relationshipState(profile);
          return <li key={profile.user_id}>
            <SearchAvatar profile={profile} />
            <div className="friend-search-identity"><strong>{profile.display_name || profile.username}</strong><small>@{profile.username} · {profile.email_hint}</small></div>
            {state ? <span className="friend-search-state">{state}</span> : <button className="secondary" disabled={busy} onClick={() => void sendRequest(profile)}>{busy ? "正在发送…" : "发送申请"}</button>}
          </li>;
        })}</ul>}
      </div>}
    </section>
    <div className="social-grid">
      <section className="social-card">
        <h2>好友申请 <span className="social-count">{requests.length}</span></h2>
        {loading ? <p className="status">正在加载……</p> : requests.length === 0 ? <p className="social-empty">暂无待处理申请。</p> : <ul className="social-list">{requests.map((row) => <li key={row.id}>
          <div><strong>{row.display_name || row.email.split("@")[0]}</strong><small>{row.email} · {row.direction === "INCOMING" ? "对方发来的申请" : "等待对方接受"}</small></div>
          {row.direction === "INCOMING" ? <div className="social-actions"><button className="primary" disabled={busy} onClick={() => void handleRequest(row, "accept")}>接受</button><button className="secondary" disabled={busy} onClick={() => void handleRequest(row, "decline")}>忽略</button><button className="social-danger" disabled={busy} onClick={() => void handleFriend(row, "block")}>屏蔽</button></div> : null}
        </li>)}</ul>}
      </section>
      <section className="social-card">
        <h2>我的好友 <span className="social-count">{friends.length}</span></h2>
        {loading ? <p className="status">正在加载……</p> : friends.length === 0 ? <p className="social-empty">接受好友申请后，可在旅行设置中授予只读分享。</p> : <ul className="social-list">{friends.map((row) => <li key={row.id}>
          <div><strong>{row.display_name || row.email.split("@")[0]}</strong><small>{row.email} · 不会自动获得旅行访问权</small></div>
          <div className="social-actions"><button className="secondary" onClick={() => setChatFriend(row)}>私聊</button><button className="secondary" disabled={busy} onClick={() => void handleFriend(row, "remove")}>移除</button><button className="social-danger" disabled={busy} onClick={() => void handleFriend(row, "block")}>屏蔽</button></div>
        </li>)}</ul>}
      </section>
    </div>
    {blocked.length ? <section className="social-card"><h2>已屏蔽</h2><ul className="social-list">{blocked.map((row) => <li key={row.id}><div><strong>{row.email}</strong><small>屏蔽期间无法互相分享旅行</small></div><button className="secondary" disabled={busy} onClick={() => void handleUnblock(row)}>取消屏蔽</button></li>)}</ul></section> : null}
    <section className="social-card">
      <h2>小团体旅行邀请 <span className="social-count">{tripInvitations.length}</span></h2>
      {loading ? <p className="status">正在加载……</p> : tripInvitations.length === 0 ? <p className="social-empty">收到共同规划邀请后会显示在这里。接受后才会展示旅行内容。</p> : <ul className="social-list">{tripInvitations.map((invitation) => <li key={invitation.id}>
        <div><strong>{invitation.inviter_display_name || "好友"} 邀请你加入一段旅行</strong><small>{new Date(invitation.created_at).toLocaleDateString("zh-CN")} · 你的角色：{invitation.role === "EDITOR" ? "编辑者" : "查看者"} · 接受前不会展示目的地和行程</small></div>
        <div className="social-actions"><button className="primary" disabled={busy} onClick={() => void respondToTripInvitation(invitation, true)}>接受</button><button className="secondary" disabled={busy} onClick={() => void respondToTripInvitation(invitation, false)}>拒绝</button></div>
      </li>)}</ul>}
    </section>
    <section className="social-card">
      <h2>与我共享的小团体旅行 <span className="social-count">{collaborativeTrips.length}</span></h2>
      {loading ? <p className="status">正在加载……</p> : collaborativeTrips.length === 0 ? <p className="social-empty">接受邀请后，旅行会出现在这里。角色为编辑者时可共同修改；查看者只能查看。</p> : <ul className="social-list">{collaborativeTrips.map((trip) => <li key={trip.id}>
        <div><strong>{trip.destination}</strong><small>{trip.owner_display_name} 发起 · {trip.start_date} 至 {trip.end_date} · 你的角色：{trip.role === "EDITOR" ? "编辑者" : "查看者"}</small></div>
        <button className="secondary" onClick={() => void openCollaborativeTrip(trip)}>打开共享旅行</button>
      </li>)}</ul>}
      {selectedCollaborativeTrip && <div className="shared-trip-detail">
        <div className="collaborative-trip-heading"><div><h3>{selectedCollaborativeTrip.destination}</h3><p>{selectedCollaborativeTrip.start_date} 至 {selectedCollaborativeTrip.end_date} · 发起人 {selectedCollaborativeTrip.owner_display_name} · {selectedCollaborativeTrip.role === "EDITOR" ? "编辑者" : "查看者"}</p></div><button className="secondary" disabled={busy} onClick={() => void openCollaborativeTrip(selectedCollaborativeTrip)}>刷新行程</button></div>
        <div><h4>共享行程 {selectedCollaborativeTrip.plan_version ? `· v${selectedCollaborativeTrip.plan_version}` : ""}</h4>{selectedCollaborativeTrip.plan_nodes.length ? <ol className="shared-plan-list">{selectedCollaborativeTrip.plan_nodes.map((node) => <li key={node.id}>
          <div className="shared-plan-node"><strong>{node.day} · {node.start_time.slice(0, 5)}–{node.end_time.slice(0, 5)}</strong><span>{node.name}</span>{node.note && <small>{node.note}</small>}</div>
          {selectedCollaborativeTrip.role === "EDITOR" && selectedCollaborativeTrip.plan_id && selectedCollaborativeTrip.plan_version !== null && <div className="shared-plan-actions">
            {editingSharedNodeId === node.id ? <><label>日期<input type="date" value={sharedNodeForm.day} onChange={(event) => setSharedNodeForm({ ...sharedNodeForm, day: event.target.value })} /></label><label>开始<input type="time" value={sharedNodeForm.start_time} onChange={(event) => setSharedNodeForm({ ...sharedNodeForm, start_time: event.target.value })} /></label><label>结束<input type="time" value={sharedNodeForm.end_time} onChange={(event) => setSharedNodeForm({ ...sharedNodeForm, end_time: event.target.value })} /></label><button className="primary" disabled={busy} onClick={() => void saveSharedNode()}>保存</button><button className="secondary" disabled={busy} onClick={() => setEditingSharedNodeId("")}>取消</button></> : <><button className="secondary" disabled={busy} onClick={() => editSharedNode(node)}>调整</button><button className="social-danger" disabled={busy} onClick={() => void removeSharedNode(node)}>移除</button></>}
          </div>}
        </li>)}</ol> : <p className="social-empty">这段旅行还没有保存的行程计划。</p>}
          <div className="shared-plan-footer"><Link className="secondary" href={`/trips/${selectedCollaborativeTrip.id}/plan`}>打开完整行程</Link>{selectedCollaborativeTrip.role === "EDITOR" && <Link className="secondary" href={`/places?tripId=${selectedCollaborativeTrip.id}`}>添加共同地点</Link>}{selectedCollaborativeTrip.role === "EDITOR" && <p className="shared-plan-hint">编辑者可以添加、调整或移除共同计划地点；保存时会检查版本，避免覆盖其他成员刚提交的修改。</p>}</div>
        </div>
      </div>}
    </section>
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
    {chatFriend && <FriendChatDialog friend={chatFriend} onClose={() => setChatFriend(null)} />}
  </main>;
}
