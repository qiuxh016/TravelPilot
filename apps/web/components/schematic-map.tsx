type MapPoint = {
  id: string;
  name: string;
  detail?: string | null;
};

type SchematicMapProps = {
  points: MapPoint[];
  title?: string;
  showRoute?: boolean;
  emptyMessage?: string;
};

const markerSlots = [
  [16, 62], [29, 39], [45, 56], [59, 31], [75, 50], [88, 27],
];

export function SchematicMap({ points, title = "地点分布", showRoute = false, emptyMessage = "添加地点后将在这里显示顺序示意" }: SchematicMapProps) {
  const visiblePoints = points.slice(0, markerSlots.length);
  const route = visiblePoints.map((_, index) => {
    const [x, y] = markerSlots[index];
    return `${x},${y}`;
  }).join(" ");

  return (
    <section className="map-panel" aria-label={title}>
      <div className="map-panel-heading">
        <div>
          <p className="eyebrow">MAP PREVIEW</p>
          <h2>{title}</h2>
        </div>
        <span className="demo-badge">示意地图</span>
      </div>
      <div className="schematic-map">
        <svg className="schematic-map-art" viewBox="0 0 100 100" role="img" aria-label="仅用于演示地点顺序的地图示意图，不对应真实地理位置">
          <path className="map-river" d="M-4 20 C18 4 26 38 44 25 S70 7 104 21" />
          <path className="map-road map-road-wide" d="M-8 82 C19 70 31 84 48 67 S75 47 108 54" />
          <path className="map-road" d="M18 -5 C27 18 35 30 32 52 S40 78 42 106" />
          <path className="map-road" d="M69 -7 C61 18 77 37 71 58 S65 83 78 106" />
          <path className="map-road map-road-minor" d="M-5 48 C17 53 27 46 48 48 S78 73 104 68" />
          {showRoute && visiblePoints.length > 1 ? <path className="map-route" d={`M${route.split(" ").join(" L")}`} /> : null}
          {visiblePoints.map((point, index) => {
            const [x, y] = markerSlots[index];
            return <g className="map-marker" key={point.id} transform={`translate(${x} ${y})`}>
              <circle r="4.5" />
              <text x="0" y="1.3">{index + 1}</text>
            </g>;
          })}
        </svg>
        {visiblePoints.length === 0 ? <p className="map-empty">{emptyMessage}</p> : null}
      </div>
      <p className="map-disclaimer">演示布局，不按经纬度绘制，也不代表真实道路或导航路线。</p>
      {visiblePoints.length > 0 ? <ol className="map-point-list">
        {visiblePoints.map((point, index) => <li key={point.id}>
          <span className="map-point-number">{index + 1}</span>
          <span><strong>{point.name}</strong>{point.detail ? <small>{point.detail}</small> : null}</span>
        </li>)}
        {points.length > visiblePoints.length ? <li className="map-more">另有 {points.length - visiblePoints.length} 个地点未显示在示意图中</li> : null}
      </ol> : null}
    </section>
  );
}
