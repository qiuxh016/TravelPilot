import Link from "next/link";
import { SchematicMap } from "../../components/schematic-map";
import { WeatherDemo } from "../../components/weather-demo";

const samplePoints = [
  { id: "demo-1", name: "人民公园", detail: "成都 · 示例地点" },
  { id: "demo-2", name: "宽窄巷子", detail: "成都 · 示例地点" },
  { id: "demo-3", name: "武侯祠", detail: "成都 · 示例地点" },
  { id: "demo-4", name: "锦里", detail: "成都 · 示例地点" },
];

export default function DemoPage() {
  return <main className="shell">
    <nav className="nav">
      <Link className="brand" href="/trips">TravelPilot</Link>
      <Link className="secondary" href="/trips">返回我的旅行</Link>
    </nav>
    <section className="hero">
      <p className="eyebrow">Local preview</p>
      <h1>地图与天气演示</h1>
      <p className="lede">预览旅行地点顺序图和天气状态交互。所有地点与天气均为本地样例，没有调用真实地图或天气服务。</p>
    </section>
    <SchematicMap title="示例行程地点顺序" points={samplePoints} showRoute />
    <WeatherDemo days={["第 1 天", "第 2 天", "第 3 天"]} />
  </main>;
}
