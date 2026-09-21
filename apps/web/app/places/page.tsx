"use client";

import Link from "next/link";
import { FormEvent, Suspense, useState } from "react";
import { useSearchParams } from "next/navigation";
import { addWishlistItem, searchPlaces, type Place } from "../../lib/api";

function PlacesContent() {
  const tripId = useSearchParams().get("tripId") ?? "";
  const [keyword, setKeyword] = useState("");
  const [city, setCity] = useState("成都");
  const [places, setPlaces] = useState<Place[]>([]);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  async function search(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setMessage("");
    try { setPlaces(await searchPlaces(keyword.trim(), city.trim())); }
    catch (error) { setMessage(error instanceof Error ? error.message : "搜索失败"); }
    finally { setBusy(false); }
  }

  async function add(place: Place) {
    if (!tripId) { setMessage("请从旅行详情页进入搜索"); return; }
    setBusy(true);
    try {
      await addWishlistItem(tripId, {
        name: place.name, category: place.category, address: place.address,
        latitude: place.latitude, longitude: place.longitude,
        suggested_duration_min: place.suggested_duration_min,
        source_type: "MAPS",
      });
      setMessage(`已加入 Wishlist：${place.name}`);
    } catch (error) { setMessage(error instanceof Error ? error.message : "加入失败"); }
    finally { setBusy(false); }
  }

  return <main className="shell">
    <nav className="nav"><Link className="brand" href={tripId ? `/trips/${tripId}` : "/trips"}>TravelPilot</Link><span className="status">POI 搜索</span></nav>
    <section className="hero"><p className="eyebrow">Explore</p><h1>寻找地点</h1><p className="lede">搜索地图中的 POI，确认后加入旅行收藏。</p></section>
    <form className="trip-form" onSubmit={search}>
      <label>关键词<input required value={keyword} onChange={(event) => setKeyword(event.target.value)} /></label>
      <label>城市<input value={city} onChange={(event) => setCity(event.target.value)} /></label>
      <button className="primary" disabled={busy}>搜索</button>
    </form>
    {message ? <p role="status">{message}</p> : null}
    <div className="wishlist-list">{places.map((place) => <article className="layer" key={`${place.provider}:${place.provider_id}`}>
      <span className="index">{place.category}</span><h2>{place.name}</h2><p>{place.address}</p>
      <p>{place.latitude}, {place.longitude}</p><button className="primary" disabled={busy} onClick={() => void add(place)}>加入 Wishlist</button>
    </article>)}</div>
  </main>;
}

export default function PlacesPage() {
  return <Suspense fallback={<main className="shell">正在加载地点搜索…</main>}><PlacesContent /></Suspense>;
}
