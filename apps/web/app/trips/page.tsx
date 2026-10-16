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
        <Link className="secondary" href="/demo">
          地图与天气演示
        </Link>
        <Link className="secondary" href="/account">账户</Link>
      </nav>

      <section className="hero">
        <h1>我的旅行</h1>
        <p className="lede">
          规划行程、探索目的地，让每一次出发都更从容。
        </p>
        <div className="actions">
          <Link className="primary" href="/trips/new">创建旅行</Link>
          <Link className="secondary" href="/demo">查看地图与天气演示</Link>
        </div>
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
        <section className="empty-state">
          <div className="empty-state-mark" aria-hidden="true">✦</div>
          <div>
            <h2>从一条旅行开始</h2>
            <p>创建旅行后，添加想去的地点，再生成每日计划。</p>
          </div>
          <Link className="primary" href="/trips/new">创建第一条旅行</Link>
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
