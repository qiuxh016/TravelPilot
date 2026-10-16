import Link from "next/link";
import { LiveMapWeatherDemo } from "../../components/live-map-weather-demo";

export default function DemoPage() {
  return <main className="shell">
    <section className="live-data-page-heading">
      <div><p className="eyebrow">REAL DATA</p><h1>出发前，先看看目的地。</h1><p>搜索真实地点、查看高德地图与 Open-Meteo 预报。数据来源和更新时间会一并显示。</p></div>
      <Link className="secondary" href="/trips">返回我的旅行 <span aria-hidden="true">↗</span></Link>
    </section>
    <LiveMapWeatherDemo />
  </main>;
}
