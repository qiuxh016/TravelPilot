"use client";

import Link from "next/link";
import { FormEvent, useEffect, useState } from "react";
import { useParams } from "next/navigation";

import {
  addWishlistItem,
  getTrip,
  listWishlist,
  type Trip,
  type WishlistItem,
} from "../../../lib/api";

export default function TripDetailPage() {
  const params = useParams<{ tripId: string }>();
  const tripId = params.tripId;

  const [trip, setTrip] = useState<Trip | null>(null);
  const [wishlist, setWishlist] = useState<WishlistItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

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

      {error && <p role="alert">{error}</p>}

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