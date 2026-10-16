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
import { SchematicMap } from "../../../components/schematic-map";

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
  const [wishlistSavingId, setWishlistSavingId] = useState<string | null>(null);

  const [name, setName] = useState("");
  const [category, setCategory] = useState("ATTRACTION");
  const [address, setAddress] = useState("");
  const [latitude, setLatitude] = useState("");
  const [longitude, setLongitude] = useState("");
  const [duration, setDuration] = useState("60");
  const [priority, setPriority] = useState("3");
  const [notes, setNotes] = useState("");

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
      });

      setName("");
      setAddress("");
      setLatitude("");
      setLongitude("");
      setDuration("60");
      setPriority("3");
      setNotes("");

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

  return (
    <main className="shell">
      <nav className="nav">
        <Link className="brand" href="/trips">
          TravelPilot
        </Link>
        <span className="status">{trip.status}</span>
      </nav>

      <section className="hero">
        <p className="eyebrow">Trip workspace</p>
        <h1>{trip.destination}</h1>
        <p className="lede">
          {trip.start_date} 至 {trip.end_date}
          <br />
          旅行节奏：{trip.pace_level} / 5
        </p>
      </section>

      <div className="actions" aria-label="旅行管理">
        <button className="secondary" type="button" onClick={beginTripEdit}>编辑旅行</button>
        <button className="secondary" type="button" onClick={() => void handleTripDelete()} disabled={tripDeleting}>
          {tripDeleting ? "正在删除…" : "删除旅行"}
        </button>
      </div>
      {editingTrip && (
        <form className="trip-form layer" onSubmit={handleTripUpdate}>
          <h2>编辑旅行信息</h2>
          <label>目的地<input required maxLength={120} value={tripDestination} onChange={(event) => setTripDestination(event.target.value)} /></label>
          <label>开始日期<input required type="date" value={tripStartDate} onChange={(event) => setTripStartDate(event.target.value)} /></label>
          <label>结束日期<input required type="date" min={tripStartDate} value={tripEndDate} onChange={(event) => setTripEndDate(event.target.value)} /></label>
          <label>预算<input type="number" min="0" step="0.01" value={tripBudget} onChange={(event) => setTripBudget(event.target.value)} placeholder="可选" /></label>
          <label>旅行节奏：{tripPace}<input type="range" min="1" max="5" value={tripPace} onChange={(event) => setTripPace(event.target.value)} /></label>
          <div className="actions">
            <button className="primary" type="submit" disabled={tripSaving}>{tripSaving ? "正在保存…" : "保存修改"}</button>
            <button className="secondary" type="button" onClick={() => setEditingTrip(false)} disabled={tripSaving}>取消</button>
          </div>
        </form>
      )}

      <div className="actions">
        <Link className="secondary" href={`/places?tripId=${tripId}`}>搜索 POI</Link>
        <Link className="secondary" href={`/trips/${tripId}/plan`}>查看计划</Link>
        <Link className="secondary" href={`/trips/${tripId}/import`}>导入攻略</Link>
        <Link className="secondary" href={`/trips/${tripId}/memory`}>旅行记忆</Link>
        <Link className="secondary" href={`/trips/${tripId}/sharing`}>分享设置</Link>
      </div>
      {error && <p role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}

      <SchematicMap
        title="Wishlist 地点分布"
        points={wishlist.map((item) => ({ id: item.id, name: item.name, detail: item.address }))}
      />

      <section className="workspace">
        <div>
          <h2>Wishlist</h2>

          {wishlist.length === 0 && (
            <p>还没有收藏地点。</p>
          )}

          <div className="wishlist-list">
            {wishlist.map((item) => (
              <article className="layer" key={item.id}>
                <span className="index">
                  优先级 {item.priority}
                </span>
                <h2>{item.name}</h2>
                <p>{item.category}</p>
                <p>{item.address}</p>
                <p>
                  建议游玩：{item.suggested_duration_min} 分钟
                </p>
                <p>{item.notes}</p>
                {editingWishlistId === item.id ? (
                  <form className="trip-form" onSubmit={(event) => void handleWishlistUpdate(event, item.id)}>
                    <label>优先级：{wishlistPriority}<input type="range" min="1" max="5" value={wishlistPriority} onChange={(event) => setWishlistPriority(event.target.value)} /></label>
                    <label>兴趣分（0–1）<input type="number" min="0" max="1" step="0.1" value={wishlistInterest} onChange={(event) => setWishlistInterest(event.target.value)} placeholder="可选" /></label>
                    <label>备注<textarea value={wishlistNotes} onChange={(event) => setWishlistNotes(event.target.value)} /></label>
                    <div className="actions">
                      <button className="primary" type="submit" disabled={wishlistSavingId === item.id}>{wishlistSavingId === item.id ? "正在保存…" : "保存"}</button>
                      <button className="secondary" type="button" onClick={() => setEditingWishlistId(null)} disabled={wishlistSavingId === item.id}>取消</button>
                    </div>
                  </form>
                ) : (
                  <div className="actions">
                    <button className="secondary" type="button" onClick={() => beginWishlistEdit(item)}>编辑收藏</button>
                    <button className="secondary" type="button" onClick={() => void handleWishlistDelete(item)} disabled={wishlistSavingId === item.id}>{wishlistSavingId === item.id ? "处理中…" : "删除收藏"}</button>
                  </div>
                )}
              </article>
            ))}
          </div>
        </div>

        <div>
          <h2>添加地点</h2>

          <form
            className="trip-form"
            onSubmit={handleAddWishlist}
          >
            <label>
              地点名称
              <input
                required
                value={name}
                onChange={(event) =>
                  setName(event.target.value)
                }
                placeholder="例如：宽窄巷子"
              />
            </label>

            <label>
              分类
              <select
                value={category}
                onChange={(event) =>
                  setCategory(event.target.value)
                }
              >
                <option value="ATTRACTION">景点</option>
                <option value="RESTAURANT">餐厅</option>
                <option value="CAFE">咖啡</option>
                <option value="SHOPPING">购物</option>
                <option value="CUSTOM">其他</option>
              </select>
            </label>

            <label>
              地址
              <input
                value={address}
                onChange={(event) =>
                  setAddress(event.target.value)
                }
                placeholder="可选"
              />
            </label>

            <label>
              纬度
              <input
                required
                type="number"
                step="any"
                value={latitude}
                onChange={(event) =>
                  setLatitude(event.target.value)
                }
                placeholder="30.7300"
              />
            </label>

            <label>
              经度
              <input
                required
                type="number"
                step="any"
                value={longitude}
                onChange={(event) =>
                  setLongitude(event.target.value)
                }
                placeholder="104.1400"
              />
            </label>

            <label>
              建议游玩分钟数
              <input
                required
                type="number"
                min="1"
                value={duration}
                onChange={(event) =>
                  setDuration(event.target.value)
                }
              />
            </label>

            <label>
              优先级：{priority}
              <input
                type="range"
                min="1"
                max="5"
                value={priority}
                onChange={(event) =>
                  setPriority(event.target.value)
                }
              />
            </label>

            <label>
              备注
              <textarea
                value={notes}
                onChange={(event) =>
                  setNotes(event.target.value)
                }
                placeholder="例如：早上去，避开人流"
              />
            </label>

            <button className="primary" type="submit">
              添加到 Wishlist
            </button>
          </form>
        </div>
      </section>
    </main>
  );
}
