"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import {
  ApiError,
  listTrips,
  type Trip,
} from "../../lib/api";

export default function TripsPage() {
  const [trips, setTrips] = useState<Trip[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [errorCode, setErrorCode] = useState("");

  async function loadTrips() {
    try {
      setLoading(true);
      setError("");
      setErrorCode("");

      const result = await listTrips();
      setTrips(result);
    } catch (error) {
      if (error instanceof ApiError) {
        setError(error.message);
        setErrorCode(error.code);
      } else if (error instanceof Error) {
        setError(error.message);
      } else {
        setError("加载旅行失败");
      }
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadTrips();
  }, []);

  return (
    <main className="shell">
      <nav className="nav">
        <Link className="brand" href="/">
          TravelPilot
        </Link>

        <Link className="primary" href="/trips/new">
          创建旅行
        </Link>
      </nav>

      <section className="hero">
        <p className="eyebrow">Your trips</p>
        <h1>我的旅行</h1>
        <p className="lede">
          管理你的旅行和收藏地点。
        </p>
      </section>

      {loading && (
        <section className="layer">
          <p>正在加载旅行……</p>
        </section>
      )}

      {!loading && error && (
        <section className="layer" role="alert">
          <h2>加载失败</h2>
          <p>{error}</p>

          {errorCode && (
            <p className="status">
              错误代码：{errorCode}
            </p>
          )}

          <button
            className="primary"
            type="button"
            onClick={() => void loadTrips()}
          >
            重试
          </button>
        </section>
      )}

      {!loading && !error && trips.length === 0 && (
        <section className="layer">
          <h2>还没有旅行</h2>
          <p>
            创建一次旅行，然后添加 Wishlist 地点。
          </p>

          <Link className="primary" href="/trips/new">
            创建第一条旅行
          </Link>
        </section>
      )}

      {!loading && !error && trips.length > 0 && (
        <section className="layers">
          {trips.map((trip) => (
            <Link
              className="layer"
              href={`/trips/${trip.id}`}
              key={trip.id}
            >
              <span className="index">
                {trip.status}
              </span>

              <h2>{trip.destination}</h2>

              <p>
                {trip.start_date} 至 {trip.end_date}
              </p>

              <p>
                旅行节奏：{trip.pace_level} / 5
              </p>
            </Link>
          ))}
        </section>
      )}
    </main>
  );
}