"use client";

import { useEffect, useMemo, useState } from "react";
import { estimateMapRoute, getStaticMapImage } from "../lib/api";

export type RouteMapPoint = {
  id: string;
  name: string;
  detail?: string | null;
  latitude: number;
  longitude: number;
};

export function RouteMap({ title, points, disclaimer = "地点来自当前旅行收藏；地图显示地点分布。", selectedPointId, onSelect, showDrivingRoute = false }: {
  title: string;
  points: RouteMapPoint[];
  disclaimer?: string;
  selectedPointId?: string;
  onSelect?: (pointId: string) => void;
  showDrivingRoute?: boolean;
}) {
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [imageUrl, setImageUrl] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [routeMessage, setRouteMessage] = useState("");
  const signature = useMemo(() => JSON.stringify(points), [points]);
  const safePoints = useMemo<RouteMapPoint[]>(() => {
    try { return (JSON.parse(signature) as RouteMapPoint[]).slice(0, 10); } catch { return []; }
  }, [signature]);
  const selectedIndexForMap = selectedPointId ? Math.max(0, safePoints.findIndex((point) => point.id === selectedPointId)) : selectedIndex;

  useEffect(() => { setSelectedIndex(0); }, [signature]);

  useEffect(() => {
    let active = true;
    let objectUrl = "";
    if (!safePoints.length) return () => { active = false; };
    setLoading(true);
    setError("");
    setRouteMessage("");
    setImageUrl("");
    async function loadMap() {
      let routePath: [number, number][] | undefined;
      if (showDrivingRoute && safePoints.length > 1) {
        try {
          const legs = await Promise.all(safePoints.slice(1).map((point, index) => {
            const origin = safePoints[index];
            return estimateMapRoute([origin.latitude, origin.longitude], [point.latitude, point.longitude]);
          }));
          if (legs.some((leg) => leg.provider !== "amap" || leg.polyline.length < 2)) throw new Error("没有完整的道路轨迹");
          routePath = legs.flatMap((leg, index) => leg.polyline.filter((_, pointIndex) => index === 0 || pointIndex > 0));
          if (routePath.length < 2) throw new Error("没有完整的道路轨迹");
          if (routePath.length > 300) {
            const source = routePath;
            routePath = Array.from({ length: 300 }, (_, index) => source[Math.round(index * (source.length - 1) / 299)]);
          }
        } catch {
          if (active) setRouteMessage("道路轨迹暂不可用，地图仅显示地点位置。");
        }
      } else if (showDrivingRoute) {
        setRouteMessage("至少需要两个地点才能绘制道路轨迹。");
      }
      try {
        const url = await getStaticMapImage(safePoints.map(({ latitude, longitude }) => ({ latitude, longitude })), selectedIndexForMap, routePath);
        objectUrl = url;
        if (active) setImageUrl(url);
        else URL.revokeObjectURL(url);
      } catch (cause) {
        if (active) setError(cause instanceof Error ? cause.message : "地图暂时无法加载");
      } finally {
        if (active) setLoading(false);
      }
    }
    void loadMap();
    return () => { active = false; if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [safePoints, selectedIndexForMap, showDrivingRoute]);

  function select(index: number) {
    setSelectedIndex(index);
    onSelect?.(safePoints[index].id);
  }

  return <section className="route-map-card" aria-label={title}>
    <div className="route-map-heading"><div><p className="eyebrow">REAL MAP</p><h2>{title}</h2></div>{safePoints.length > 0 && <span className="route-map-count">{points.length > 10 ? `前 10 / ${points.length}` : `${safePoints.length} 个地点`}</span>}</div>
    {safePoints.length ? <>
      <div className="route-map-frame" aria-live="polite">
        {imageUrl ? <img src={imageUrl} alt={`${title}，高德地图地点分布`} /> : <div className="route-map-placeholder">{loading ? "正在加载真实地图…" : error ? <span role="alert">{error}</span> : "地图准备中…"}</div>}
      </div>
      <ol className="route-map-stops">{safePoints.map((point, index) => <li key={point.id}><button type="button" className={index === selectedIndexForMap ? "is-selected" : ""} onClick={() => select(index)} aria-pressed={index === selectedIndexForMap}>
        <span className="route-map-number">{index + 1}</span><span className="route-map-stop-copy"><strong>{point.name}</strong>{point.detail && <small>{point.detail}</small>}</span><span className="route-map-arrow" aria-hidden="true">↗</span>
      </button></li>)}</ol>
      {routeMessage && <p className="route-map-disclaimer" role="status">{routeMessage}</p>}
      <p className="route-map-disclaimer">{showDrivingRoute && !routeMessage ? "道路轨迹按高德驾车规划绘制；实际路线和耗时可能随路况变化。" : disclaimer} {imageUrl ? "地图数据：高德地图。" : error ? "地图服务当前不可用。" : ""}{points.length > 10 ? "地图最多展示前 10 个地点。" : ""}</p>
    </> : <div className="route-map-empty"><span aria-hidden="true">⌖</span><strong>地点添加后，这里会出现你的旅行地图</strong><small>搜索地点或和旅行顾问聊聊，逐步整理想去的地方。</small></div>}
  </section>;
}
