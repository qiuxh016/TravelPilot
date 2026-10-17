"use client";

import Link from "next/link";
import { FormEvent, useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";

import {
  addWishlistItem,
  deleteTrip,
  deleteWishlistItem,
  getTrip,
  listWishlist,
  updateTrip,
  updateWishlistItem,
  type Trip,
  type WishlistItem,
} from "../../../lib/api";
import { RouteMap } from "../../../components/route-map";

const paceLabels = ["轻松慢游", "悠闲逛逛", "张弛有度", "高效游览", "行程充实"];
const categoryLabels: Record<string, string> = { ATTRACTION: "景点", RESTAURANT: "餐厅", CAFE: "咖啡", SHOPPING: "购物", CUSTOM: "其它" };

export default function TripDetailPage() {
  const params = useParams<{ tripId: string }>();
  const router = useRouter();
  const tripId = params.tripId;

  const [trip, setTrip] = useState<Trip | null>(null);
  const [wishlist, setWishlist] = useState<WishlistItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [editingTrip, setEditingTrip] = useState(false);
  const [tripSaving, setTripSaving] = useState(false);
  const [tripDeleting, setTripDeleting] = useState(false);
  const [tripDestination, setTripDestination] = useState("");
  const [tripStartDate, setTripStartDate] = useState("");
  const [tripEndDate, setTripEndDate] = useState("");
  const [tripBudget, setTripBudget] = useState("");
  const [tripPace, setTripPace] = useState("3");
  const [editingWishlistId, setEditingWishlistId] = useState<string | null>(null);
  const [wishlistPriority, setWishlistPriority] = useState("3");
  const [wishlistInterest, setWishlistInterest] = useState("");
  const [wishlistNotes, setWishlistNotes] = useState("");
  const [wishlistExposure, setWishlistExposure] = useState<"UNKNOWN" | "INDOOR" | "OUTDOOR" | "MIXED">("UNKNOWN");
  const [wishlistRainSensitivity, setWishlistRainSensitivity] = useState<"UNKNOWN" | "LOW" | "MODERATE" | "HIGH">("UNKNOWN");
  const [wishlistSavingId, setWishlistSavingId] = useState<string | null>(null);

  const [name, setName] = useState("");
  const [category, setCategory] = useState("ATTRACTION");
  const [address, setAddress] = useState("");
  const [latitude, setLatitude] = useState("");
  const [longitude, setLongitude] = useState("");
  const [duration, setDuration] = useState("60");
  const [priority, setPriority] = useState("3");
  const [notes, setNotes] = useState("");
  const [weatherExposure, setWeatherExposure] = useState<"UNKNOWN" | "INDOOR" | "OUTDOOR" | "MIXED">("UNKNOWN");
  const [rainSensitivity, setRainSensitivity] = useState<"UNKNOWN" | "LOW" | "MODERATE" | "HIGH">("UNKNOWN");

  async function loadData() {
    try {
      setLoading(true);
      setError("");

      const [tripResult, wishlistResult] = await Promise.all([
        getTrip(tripId),
        listWishlist(tripId),
      ]);

      setTrip(tripResult);
      setWishlist(wishlistResult);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "加载旅行失败",
      );
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (tripId) {
      void loadData();
    }
  }, [tripId]);

  async function handleAddWishlist(
    event: FormEvent<HTMLFormElement>,
  ) {
    event.preventDefault();

    try {
      setError("");

      await addWishlistItem(tripId, {
        name,
        category,
        address,
        latitude: Number(latitude),
        longitude: Number(longitude),
        suggested_duration_min: Number(duration),
        priority: Number(priority),
        notes,
        environment_profile: weatherExposure === "UNKNOWN" && rainSensitivity === "UNKNOWN" ? undefined : {
          exposure: weatherExposure,
          sensitivity: rainSensitivity === "UNKNOWN" ? {} : { RAIN: rainSensitivity },
        },
      });

      setName("");
      setAddress("");
      setLatitude("");
      setLongitude("");
      setDuration("60");
      setPriority("3");
      setNotes("");
      setWeatherExposure("UNKNOWN");
      setRainSensitivity("UNKNOWN");

      await loadData();
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "添加地点失败",
      );
    }
  }

  function beginTripEdit() {
    if (!trip) return;
    setTripDestination(trip.destination);
    setTripStartDate(trip.start_date);
    setTripEndDate(trip.end_date);
    setTripBudget(trip.budget == null ? "" : String(trip.budget));
    setTripPace(String(trip.pace_level));
    setError("");
    setNotice("");
    setEditingTrip(true);
  }

  async function handleTripUpdate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (tripEndDate < tripStartDate) {
      setError("结束日期不能早于开始日期。");
      return;
    }
    try {
      setTripSaving(true);
      setError("");
      const updated = await updateTrip(tripId, {
        destination: tripDestination.trim(),
        start_date: tripStartDate,
        end_date: tripEndDate,
        budget: tripBudget === "" ? null : Number(tripBudget),
        pace_level: Number(tripPace),
      });
      setTrip(updated);
      setEditingTrip(false);
      setNotice("旅行信息已保存。");
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存旅行信息失败");
    } finally {
      setTripSaving(false);
    }
  }

  async function handleTripDelete() {
    const confirmed = window.confirm("删除这次旅行？关联的 Wishlist、计划、旅行记忆和反馈也会一并删除，且无法恢复。");
    if (!confirmed) return;
    try {
      setTripDeleting(true);
      setError("");
      await deleteTrip(tripId);
      router.replace("/trips");
    } catch (err) {
      setError(err instanceof Error ? err.message : "删除旅行失败");
      setTripDeleting(false);
    }
  }

  function beginWishlistEdit(item: WishlistItem) {
    setEditingWishlistId(item.id);
    setWishlistPriority(String(item.priority));
    setWishlistInterest(item.interest_score == null ? "" : String(item.interest_score));
    setWishlistNotes(item.notes ?? "");
    setWishlistExposure(item.environment_profile?.exposure ?? "UNKNOWN");
    setWishlistRainSensitivity(item.environment_profile?.sensitivity.RAIN ?? "UNKNOWN");
    setError("");
    setNotice("");
  }

  async function handleWishlistUpdate(event: FormEvent<HTMLFormElement>, itemId: string) {
    event.preventDefault();
    try {
      setWishlistSavingId(itemId);
      setError("");
      await updateWishlistItem(itemId, {
        priority: Number(wishlistPriority),
        interest_score: wishlistInterest === "" ? null : Number(wishlistInterest),
        notes: wishlistNotes,
        environment_profile: wishlistExposure === "UNKNOWN" && wishlistRainSensitivity === "UNKNOWN" ? null : {
          exposure: wishlistExposure,
          sensitivity: wishlistRainSensitivity === "UNKNOWN" ? {} : { RAIN: wishlistRainSensitivity },
        },
      });
      setEditingWishlistId(null);
      setNotice("收藏信息已保存。");
      await loadData();
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存收藏信息失败");
    } finally {
      setWishlistSavingId(null);
    }
  }

  async function handleWishlistDelete(item: WishlistItem) {
    if (!window.confirm(`删除收藏地点“${item.name}”？`)) return;
    try {
      setWishlistSavingId(item.id);
      setError("");
      await deleteWishlistItem(item.id);
      setWishlist((current) => current.filter((entry) => entry.id !== item.id));
      setNotice("收藏地点已删除。");
    } catch (err) {
      setError(err instanceof Error ? err.message : "删除收藏地点失败");
    } finally {
      setWishlistSavingId(null);
    }
  }

  if (loading) {
    return <main className="shell">正在加载……</main>;
  }

  if (!trip) {
    return (
      <main className="shell">
        <p role="alert">旅行不存在。</p>
      </main>
    );
  }

  const tripDays = Math.max(1, Math.round((Date.parse(`${trip.end_date}T00:00:00`) - Date.parse(`${trip.start_date}T00:00:00`)) / 86_400_000) + 1);

  return (
    <main className="shell trip-overview-page">
      <Link className="trip-overview-back" href="/trips">← 我的旅行</Link>
      <header className="trip-overview-hero">
        <div className="trip-overview-title">
          <span className="trip-status-mark"><i />{trip.status === "DRAFT" ? "筹备中" : trip.status}</span>
          <h1>{trip.destination}</h1>
          <div className="trip-overview-facts"><span>{trip.start_date} — {trip.end_date}</span><i /><span>{tripDays} 天 {tripDays - 1} 晚</span><i /><span>{paceLabels[trip.pace_level - 1] ?? "自定节奏"}</span></div>
        </div>
        <p className="trip-overview-note">把想去的地方收在一起，再慢慢排成适合自己的旅程。</p>
        <div className="trip-overview-actions" aria-label="旅行操作">
          <Link className="primary" href={`/trips/${tripId}/plan`}>规划行程 <span aria-hidden="true">→</span></Link>
          <Link className="secondary" href={`/trips/${tripId}/import`}>导入攻略</Link>
          <Link className="secondary" href={`/trips/${tripId}/sharing`}>分享设置</Link>
          <button className="trip-overview-edit" type="button" onClick={beginTripEdit}>编辑旅行</button>
          <button className="trip-overview-delete" type="button" onClick={() => void handleTripDelete()} disabled={tripDeleting}>{tripDeleting ? "正在删除…" : "删除旅行"}</button>
        </div>
      </header>

      {error && <p className="trip-overview-message" role="alert">{error}</p>}
      {notice && <p className="trip-overview-message is-success" role="status">{notice}</p>}

      {editingTrip && <form className="trip-edit-panel" onSubmit={handleTripUpdate}>
        <div className="trip-overview-section-head"><div><h2>调整旅行信息</h2><p>日期或节奏变化后，后续行程可以再重新规划。</p></div></div>
        <div className="trip-edit-grid">
          <label>目的地<input required maxLength={120} value={tripDestination} onChange={(event) => setTripDestination(event.target.value)} /></label>
          <label>开始日期<input required type="date" value={tripStartDate} onChange={(event) => setTripStartDate(event.target.value)} /></label>
          <label>结束日期<input required type="date" min={tripStartDate} value={tripEndDate} onChange={(event) => setTripEndDate(event.target.value)} /></label>
          <label>预算（元）<input type="number" min="0" step="0.01" value={tripBudget} onChange={(event) => setTripBudget(event.target.value)} placeholder="选填" /></label>
          <label className="trip-edit-pace">旅行节奏：{paceLabels[Number(tripPace) - 1]}<input type="range" min="1" max="5" value={tripPace} onChange={(event) => setTripPace(event.target.value)} /></label>
        </div>
        <div className="trip-edit-actions"><button className="primary" type="submit" disabled={tripSaving}>{tripSaving ? "正在保存…" : "保存修改"}</button><button className="secondary" type="button" onClick={() => setEditingTrip(false)} disabled={tripSaving}>取消</button></div>
      </form>}

      <div className="trip-overview-section-head trip-places-heading"><div><span className="dashboard-kicker">这段旅程的地图</span><h2>已经收藏的地点</h2><p>在地图上查看位置，再从清单继续整理旅行计划。</p></div><span className="trip-places-count">{wishlist.length} 个地点</span></div>
      <div className="trip-overview-places">
        <div className="trip-overview-map"><RouteMap title="地点分布" points={wishlist.map((item) => ({ id: item.id, name: item.name, detail: item.address, latitude: item.latitude, longitude: item.longitude }))} disclaimer="标记来自这次旅行的收藏地点；分布图不代表导航路线。" /></div>
        <section className="trip-place-list" aria-label="旅行收藏地点">
          {wishlist.length ? wishlist.map((item, index) => <article className="trip-place-row" key={item.id}>
            <span className="trip-place-index">{String(index + 1).padStart(2, "0")}</span>
            <div className="trip-place-copy"><div><h3>{item.name}</h3><span>{categoryLabels[item.category] ?? item.category}</span></div><p>{item.address || `建议游玩 ${item.suggested_duration_min} 分钟`}</p>{item.notes && <small>{item.notes}</small>}</div>
            {editingWishlistId === item.id ? <form className="trip-place-edit" onSubmit={(event) => void handleWishlistUpdate(event, item.id)}>
              <label>优先级：{wishlistPriority}<input type="range" min="1" max="5" value={wishlistPriority} onChange={(event) => setWishlistPriority(event.target.value)} /></label>
              <label>兴趣分<input type="number" min="0" max="1" step="0.1" value={wishlistInterest} onChange={(event) => setWishlistInterest(event.target.value)} placeholder="选填" /></label>
              <label>室内外<select value={wishlistExposure} onChange={(event) => setWishlistExposure(event.target.value as typeof wishlistExposure)}><option value="UNKNOWN">未知</option><option value="INDOOR">室内</option><option value="OUTDOOR">室外</option><option value="MIXED">混合</option></select></label>
              <label>雨天敏感度<select value={wishlistRainSensitivity} onChange={(event) => setWishlistRainSensitivity(event.target.value as typeof wishlistRainSensitivity)}><option value="UNKNOWN">未知</option><option value="LOW">低</option><option value="MODERATE">中</option><option value="HIGH">高</option></select></label>
              <label>备注<textarea value={wishlistNotes} onChange={(event) => setWishlistNotes(event.target.value)} /></label>
              <div><button className="primary" type="submit" disabled={wishlistSavingId === item.id}>{wishlistSavingId === item.id ? "保存中…" : "保存"}</button><button className="secondary" type="button" onClick={() => setEditingWishlistId(null)} disabled={wishlistSavingId === item.id}>取消</button></div>
            </form> : <div className="trip-place-actions"><button type="button" onClick={() => beginWishlistEdit(item)}>编辑</button><button type="button" onClick={() => void handleWishlistDelete(item)} disabled={wishlistSavingId === item.id}>{wishlistSavingId === item.id ? "处理中…" : "移除"}</button></div>}
          </article>) : <div className="trip-place-empty"><span aria-hidden="true">⌖</span><h3>这段旅程还没有收藏地点</h3><p>先从真实地点开始，喜欢的景点、餐厅和街区都可以收进来。</p><Link className="primary" href={`/places?tripId=${tripId}`}>探索目的地 <span aria-hidden="true">→</span></Link></div>}
        </section>
      </div>

      <details className="trip-manual-add">
        <summary><span><strong>手动添加地点</strong><small>已有坐标或自定义地点时使用</small></span><i aria-hidden="true">＋</i></summary>
        <form className="trip-manual-form" onSubmit={handleAddWishlist}>
          <label>地点名称<input required value={name} onChange={(event) => setName(event.target.value)} placeholder="例如：宽窄巷子" /></label>
          <label>分类<select value={category} onChange={(event) => setCategory(event.target.value)}><option value="ATTRACTION">景点</option><option value="RESTAURANT">餐厅</option><option value="CAFE">咖啡</option><option value="SHOPPING">购物</option><option value="CUSTOM">其他</option></select></label>
          <label>地址<input value={address} onChange={(event) => setAddress(event.target.value)} placeholder="选填" /></label>
          <label>纬度<input required type="number" step="any" value={latitude} onChange={(event) => setLatitude(event.target.value)} placeholder="30.7300" /></label>
          <label>经度<input required type="number" step="any" value={longitude} onChange={(event) => setLongitude(event.target.value)} placeholder="104.1400" /></label>
          <label>建议游玩分钟数<input required type="number" min="1" value={duration} onChange={(event) => setDuration(event.target.value)} /></label>
          <label>室内外属性<select value={weatherExposure} onChange={(event) => setWeatherExposure(event.target.value as typeof weatherExposure)}><option value="UNKNOWN">未知</option><option value="INDOOR">室内</option><option value="OUTDOOR">室外</option><option value="MIXED">室内外混合</option></select></label>
          <label>雨天敏感程度<select value={rainSensitivity} onChange={(event) => setRainSensitivity(event.target.value as typeof rainSensitivity)}><option value="UNKNOWN">未知</option><option value="LOW">低</option><option value="MODERATE">中</option><option value="HIGH">高</option></select></label>
          <label>优先级：{priority}<input type="range" min="1" max="5" value={priority} onChange={(event) => setPriority(event.target.value)} /></label>
          <label className="trip-manual-notes">备注<textarea value={notes} onChange={(event) => setNotes(event.target.value)} placeholder="例如：上午去，避开人流" /></label>
          <button className="primary" type="submit">添加到旅行</button>
        </form>
      </details>
    </main>
  );
}
