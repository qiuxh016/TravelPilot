"use client";

import Link from "next/link";
import { FormEvent, Suspense, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import { addWishlistItem, listTrips, searchPlaces, type Place, type Trip } from "../../lib/api";
import { RouteMap } from "../../components/route-map";

const categories = ["全部", "景点", "餐饮", "咖啡", "购物"];
const categoryNames: Record<string, string> = {
  ATTRACTION: "景点", RESTAURANT: "餐饮", FOOD: "餐饮", CAFE: "咖啡", SHOPPING: "购物",
};

function PlacesContent() {
  const queryTripId = useSearchParams().get("tripId") ?? "";
  const [trips, setTrips] = useState<Trip[]>([]);
  const [tripId, setTripId] = useState(queryTripId);
  const [keyword, setKeyword] = useState("");
  const [city, setCity] = useState("成都");
  const [places, setPlaces] = useState<Place[]>([]);
  const [category, setCategory] = useState("全部");
  const [showAll, setShowAll] = useState(false);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let active = true;
    void listTrips().then((rows) => {
      if (!active) return;
      setTrips(rows);
      setTripId((current) => current || queryTripId || rows[0]?.id || "");
    }).catch((error) => { if (active) setMessage(error instanceof Error ? error.message : "旅行列表暂时无法加载"); });
    return () => { active = false; };
  }, [queryTripId]);

  async function search(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true); setMessage("");
    try {
      const result = await searchPlaces(keyword.trim(), city.trim());
      setPlaces(result);
      setCategory("全部"); setShowAll(false);
      if (!result.length) setMessage("没有找到匹配地点，试试更短的关键词或切换城市。");
    } catch (error) { setMessage(error instanceof Error ? error.message : "搜索失败，请稍后再试"); }
    finally { setBusy(false); }
  }

  async function add(place: Place) {
    if (!tripId) { setMessage("先创建一段旅行，再把地点放进收藏。"); return; }
    setBusy(true); setMessage("");
    try {
      await addWishlistItem(tripId, {
        name: place.name, category: place.category, address: place.address,
        latitude: place.latitude, longitude: place.longitude,
        suggested_duration_min: place.suggested_duration_min, source_type: "MAPS",
      });
      setMessage(`「${place.name}」已加入 ${trips.find((trip) => trip.id === tripId)?.destination ?? "这段旅行"}。`);
    } catch (error) { setMessage(error instanceof Error ? error.message : "加入失败"); }
    finally { setBusy(false); }
  }

  const visiblePlaces = useMemo(() => places.filter((place) => category === "全部" || categoryNames[place.category] === category), [places, category]);
  const displayedPlaces = showAll ? visiblePlaces : visiblePlaces.slice(0, 8);
  const points = useMemo(() => visiblePlaces.map((place) => ({
    id: `${place.provider}:${place.provider_id}`, name: place.name,
    detail: place.address || categoryNames[place.category] || "地图地点",
    latitude: place.latitude, longitude: place.longitude,
  })), [visiblePlaces]);

  return <main className="shell places-page">
    <header className="places-intro">
      <div className="places-intro-copy"><span className="places-overline">目的地采集簿</span><h1>把心动的地方，<br />先放进地图里。</h1><p>找一间想坐坐的咖啡馆，或把听说过的街区先收藏起来。计划可以慢慢长出来。</p></div>
      <div className="places-compass" aria-hidden="true"><span>N</span><i>✦</i><small>{city || "目的地"}</small></div>
    </header>

    <section className="places-search-panel" aria-label="搜索地点">
      <form className="places-search-form" onSubmit={search}>
        <label><span>我想找</span><input aria-label="关键词" required value={keyword} onChange={(event) => setKeyword(event.target.value)} placeholder="例如：适合看日落的地方" /></label>
        <label className="places-city-field"><span>城市</span><input aria-label="城市" value={city} onChange={(event) => setCity(event.target.value)} /></label>
        <button className="primary" disabled={busy}>{busy ? "正在寻找…" : "开始探索"}<span aria-hidden="true">⌕</span></button>
      </form>
      <div className="places-search-foot"><span>地点来自地图搜索结果</span><span>收藏前可先在地图上查看位置</span></div>
    </section>

    <section className="places-results-layout">
      <div className="places-results-column">
        <div className="places-results-heading"><div><span className="places-overline">你的候选地点</span><h2>{places.length ? `${visiblePlaces.length} 个地方，等你挑选` : "从一个念头开始"}</h2></div>{places.length > 0 && <span className="places-result-total">共找到 {places.length} 个</span>}</div>
        {places.length > 0 && <div className="places-filters" role="group" aria-label="地点分类">{categories.map((item) => <button type="button" key={item} aria-pressed={category === item} className={category === item ? "is-active" : ""} onClick={() => { setCategory(item); setShowAll(false); }}>{item}</button>)}</div>}
        {message && <p className="places-feedback" role="status">{message}</p>}
        {places.length === 0 ? <div className="places-empty"><span className="places-empty-doodle" aria-hidden="true">⌖</span><div><h3>输入一个模糊的想法也可以</h3><p>比如“老茶馆”“夜景”“适合带孩子”。地图会帮你找到附近的真实地点。</p></div><span className="places-empty-route" aria-hidden="true">············→</span></div> : visiblePlaces.length ? <ol className="places-result-list">{displayedPlaces.map((place, index) => {
          const id = `${place.provider}:${place.provider_id}`;
          return <li className="places-result-row" key={id}><span className="places-result-index">{String(index + 1).padStart(2, "0")}</span><div className="places-result-copy"><div className="places-result-name"><h3>{place.name}</h3><span>{categoryNames[place.category] ?? place.category}</span></div><p>{place.address || "地址信息暂未提供"}</p><small>约 {place.suggested_duration_min} 分钟 · {place.latitude.toFixed(4)}, {place.longitude.toFixed(4)}</small></div><button type="button" className="places-add-button" onClick={() => void add(place)} disabled={busy || !tripId} aria-label={`收藏${place.name}`}>{!tripId ? "先建旅行" : "＋ 收藏"}</button></li>;
        })}</ol> : <div className="places-empty places-empty-filter"><p>这个分类暂时没有地点，切回“全部”看看。</p></div>}
        {visiblePlaces.length > 8 && <button type="button" className="places-show-more" onClick={() => setShowAll((current) => !current)}>{showAll ? "收起地点" : `再看 ${visiblePlaces.length - 8} 个地点`} <span aria-hidden="true">{showAll ? "↑" : "↓"}</span></button>}
      </div>
      <aside className="places-map-column">
        <label className="places-trip-select"><span>收藏到哪段旅行</span><select aria-label="选择旅行" value={tripId} onChange={(event) => setTripId(event.target.value)}><option value="">暂不选择</option>{trips.map((trip) => <option value={trip.id} key={trip.id}>{trip.destination} · {trip.start_date}</option>)}</select></label>
        {trips.length === 0 && <div className="places-create-trip"><p>还没有旅行可以收藏。</p><Link href="/trips/new">创建一段旅行 <span aria-hidden="true">↗</span></Link></div>}
        <RouteMap title="地点分布" points={points} disclaimer="地图展示当前筛选结果的位置，不代表游览路线。" />
        <small className="places-map-note">地图数据：高德地图。点击地点卡片旁的收藏后，它会出现在所选旅行中。</small>
      </aside>
    </section>
  </main>;
}

export default function PlacesPage() {
  return <Suspense fallback={<main className="shell"><p>正在打开地点采集簿…</p></main>}><PlacesContent /></Suspense>;
}
