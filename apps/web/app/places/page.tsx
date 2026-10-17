"use client";

import Link from "next/link";
import { FormEvent, Suspense, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import { addWishlistItem, listTrips, searchPlaces, type Place, type Trip } from "../../lib/api";
import { RouteMap } from "../../components/route-map";
import { tripOptionLabel, useTripSelection } from "../../components/trip-selection-context";

const categories = ["全部", "景点", "餐饮", "咖啡", "购物"];
const categoryNames: Record<string, string> = {
  ATTRACTION: "景点", RESTAURANT: "餐饮", FOOD: "餐饮", CAFE: "咖啡", SHOPPING: "购物",
};

function PlacesContent() {
  const queryTripId = useSearchParams().get("tripId") ?? "";
  const { selectedTripId, selectTrip: selectGlobalTrip } = useTripSelection();
  const [trips, setTrips] = useState<Trip[]>([]);
  const [tripId, setTripId] = useState(queryTripId);
  const [exploreMode, setExploreMode] = useState<"trip" | "free">(queryTripId ? "trip" : "free");
  const [keyword, setKeyword] = useState("");
  const [city, setCity] = useState("");
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
      if (queryTripId) {
        const linkedTrip = rows.find((trip) => trip.id === queryTripId);
        if (linkedTrip) { setTripId(linkedTrip.id); setCity(linkedTrip.destination); setExploreMode("trip"); }
      }
    }).catch((error) => { if (active) setMessage(error instanceof Error ? error.message : "旅行列表暂时无法加载"); });
    return () => { active = false; };
  }, [queryTripId]);

  useEffect(() => {
    if (queryTripId) return;
    setTripId(selectedTripId);
    if (selectedTripId) {
      const trip = trips.find((item) => item.id === selectedTripId);
      if (trip) { setCity(trip.destination); setExploreMode("trip"); }
    } else setExploreMode("free");
  }, [selectedTripId, queryTripId, trips]);

  const selectedTrip = trips.find((trip) => trip.id === tripId);

  function selectTrip(value: string) {
    setTripId(value);
    selectGlobalTrip(value);
    const trip = trips.find((item) => item.id === value);
    if (trip) setCity(trip.destination);
  }

  function selectExploreMode(next: "trip" | "free") {
    setExploreMode(next);
    if (next === "trip") {
      const trip = trips.find((item) => item.id === (selectedTripId || tripId));
      if (trip) { setTripId(trip.id); setCity(trip.destination); selectGlobalTrip(trip.id); }
    } else {
      setTripId("");
      selectGlobalTrip("");
    }
  }

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
    if (!tripId) { setMessage("地点可以自由探索；若想收藏，请先在右侧选择一段旅行。"); return; }
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
      <div className="places-mode-heading"><div><span className="places-overline">从哪里开始</span><strong>选择探索方式</strong></div></div>
      <div className="places-explore-modes" role="group" aria-label="选择探索方式">
        <button type="button" className={exploreMode === "trip" ? "is-selected" : ""} aria-pressed={exploreMode === "trip"} onClick={() => selectExploreMode("trip")}>
          <span className="places-mode-icon" aria-hidden="true">↗</span><span><strong>从一段旅行探索</strong><small>使用旅行目的地，地点直接收藏到这段旅行</small></span>
        </button>
        <button type="button" className={exploreMode === "free" ? "is-selected" : ""} aria-pressed={exploreMode === "free"} onClick={() => selectExploreMode("free")}>
          <span className="places-mode-icon" aria-hidden="true">⌖</span><span><strong>自由探索城市</strong><small>输入任意城市搜索，之后可选择是否收藏</small></span>
        </button>
      </div>
      {exploreMode === "trip" ? <div className="places-selected-trip">
        <label><span>选择旅行</span><select aria-label="选择要探索的旅行" value={tripId} onChange={(event) => selectTrip(event.target.value)}><option value="">选择一段旅行</option>{trips.map((trip) => <option value={trip.id} key={trip.id}>{tripOptionLabel(trip, trips)}</option>)}</select></label>
        {selectedTrip ? <p>正在探索 <strong>{selectedTrip.destination}</strong> · {selectedTrip.start_date} 至 {selectedTrip.end_date}。搜索到的地点会收藏到这段旅行。</p> : trips.length ? <p>请选择一段旅行，地点探索会自动使用它的目的地。</p> : <p>还没有旅行。你可以<Link href="/trips/new">创建一段旅行</Link>，也可以切换到自由探索。</p>}
      </div> : <p className="places-free-note">自由输入城市，不需要先创建旅行。想收藏结果时，可以在地图旁选择收藏目标。</p>}
      <form className="places-search-form" onSubmit={search}>
        <label><span>我想找</span><input aria-label="关键词" required value={keyword} onChange={(event) => setKeyword(event.target.value)} placeholder="例如：适合看日落的地方" /></label>
        {exploreMode === "free" ? <label className="places-city-field"><span>探索城市</span><input aria-label="探索城市" required value={city} onChange={(event) => setCity(event.target.value)} placeholder="例如：成都、厦门" /></label> : <label className="places-city-field"><span>旅行目的地</span><input aria-label="旅行目的地" value={selectedTrip?.destination ?? "请先选择旅行"} readOnly /></label>}
        <button className="primary" disabled={busy || !city.trim() || (exploreMode === "trip" && !tripId)}>{busy ? "正在寻找…" : "开始探索"}<span aria-hidden="true">⌕</span></button>
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
        {exploreMode === "free" && <label className="places-trip-select"><span>收藏到哪段旅行（选填）</span><select aria-label="选择收藏目标旅行" value={tripId} onChange={(event) => setTripId(event.target.value)}><option value="">暂不收藏</option>{trips.map((trip) => <option value={trip.id} key={trip.id}>{tripOptionLabel(trip, trips)}</option>)}</select></label>}
        {trips.length === 0 && <div className="places-create-trip"><p>还没有旅行可以收藏。</p><Link href="/trips/new">创建一段旅行 <span aria-hidden="true">↗</span></Link></div>}
        <RouteMap title="地点分布" points={points} disclaimer="地图展示当前筛选结果的位置，不代表游览路线。" />
        <small className="places-map-note">地图数据：高德地图。自由探索无需关联旅行；选择收藏目标后，收藏的地点会加入对应旅行。</small>
      </aside>
    </section>
  </main>;
}

export default function PlacesPage() {
  return <Suspense fallback={<main className="shell"><p>正在打开地点采集簿…</p></main>}><PlacesContent /></Suspense>;
}
