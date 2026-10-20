"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import { listTripActivity, listTripDiscussion, sendTripDiscussionMessage, type TripActivity, type TripDiscussionMessage } from "../lib/api";
import { productConfig } from "../lib/product-config";

function mergeMessages(...groups: TripDiscussionMessage[][]) {
  const byId = new Map(groups.flat().map((message) => [message.id, message]));
  return Array.from(byId.values()).sort((left, right) => left.created_at.localeCompare(right.created_at));
}

export function TripDiscussion({ tripId }: { tripId: string }) {
  const [messages, setMessages] = useState<TripDiscussionMessage[]>([]);
  const [activities, setActivities] = useState<TripActivity[]>([]);
  const [body, setBody] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    setError("");
    try {
      const [nextMessages, nextActivities] = await Promise.all([
        listTripDiscussion(tripId), listTripActivity(tripId),
      ]);
      setMessages((current) => mergeMessages(current, nextMessages));
      setActivities(nextActivities);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "旅行讨论暂时无法加载");
    } finally { setLoading(false); }
  }, [tripId]);

  useEffect(() => { void refresh(); }, [refresh]);

  useEffect(() => {
    const source = new EventSource(`${productConfig.apiBaseUrl}/api/v1/group-trips/${tripId}/events`, { withCredentials: true });
    source.addEventListener("discussion_message", (event) => {
      const message = JSON.parse((event as MessageEvent<string>).data) as TripDiscussionMessage;
      setMessages((current) => mergeMessages(current, [message]));
      void listTripActivity(tripId).then(setActivities).catch(() => undefined);
    });
    source.addEventListener("access_revoked", () => {
      source.close();
      setError("你已无法访问这段旅行讨论。");
    });
    return () => source.close();
  }, [tripId]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const content = body.trim();
    if (!content) return;
    setBusy(true); setError("");
    try {
      const message = await sendTripDiscussionMessage(tripId, content);
      setMessages((current) => mergeMessages(current, [message]));
      setBody("");
      const nextActivities = await listTripActivity(tripId);
      setActivities(nextActivities);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "消息发送失败"); }
    finally { setBusy(false); }
  }

  return <section className="trip-collaboration-panel" aria-label="旅行讨论和动态">
    <div className="trip-collaboration-section-head">
      <div><span className="places-overline">TRIP ROOM</span><h4>一起讨论</h4><p>这段旅行的成员都可以查看和发言。</p></div>
      <button className="secondary" type="button" disabled={loading || busy} onClick={() => void refresh()}>刷新</button>
    </div>
    {error && <p className="trip-collaboration-error" role="alert">{error}</p>}
    <div className="trip-discussion-messages" aria-live="polite">
      {loading ? <p className="social-empty">正在加载旅行讨论…</p> : messages.length ? messages.map((message) => <article key={message.id} className="trip-discussion-message">
        <div><strong>{message.author_display_name}</strong><time dateTime={message.created_at}>{new Date(message.created_at).toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" })}</time></div>
        <p>{message.body}</p>
      </article>) : <p className="social-empty">还没有讨论。说说想去的地方或需要一起决定的事。</p>}
    </div>
    <form className="trip-discussion-form" onSubmit={(event) => void submit(event)}>
      <label className="sr-only" htmlFor={`trip-message-${tripId}`}>发送旅行消息</label>
      <textarea id={`trip-message-${tripId}`} maxLength={5000} rows={2} value={body} onChange={(event) => setBody(event.target.value)} placeholder="例如：第二天的午餐，我们要不要安排在博物馆附近？" />
      <button className="primary" disabled={busy || !body.trim()}>{busy ? "发送中…" : "发送"}</button>
    </form>
    <details className="trip-activity-details">
      <summary>旅行活动记录 <span>{activities.length}</span></summary>
      {activities.length ? <ol>{activities.map((activity) => <li key={activity.id}><span className="trip-activity-dot" /><div><p><strong>{activity.actor_display_name}</strong> {activity.summary}</p><time dateTime={activity.created_at}>{new Date(activity.created_at).toLocaleString("zh-CN")}</time></div></li>)}</ol> : <p className="social-empty">行程、成员和地点发生变化后，会记录在这里。</p>}
    </details>
  </section>;
}
