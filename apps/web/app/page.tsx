const layers = [
  ["Collect", "导入链接、截图和文字，形成统一 Wishlist"],
  ["Plan", "使用地图、天气和约束生成可执行行程"],
  ["Adapt", "现实事件发生后，只重排受影响的局部计划"],
];

export default function HomePage() {
  return (
    <main className="shell">
      <nav className="nav">
        <span className="brand">TravelPilot</span>
        <span className="status">Project foundation · v0.1</span>
      </nav>
      <section className="hero">
        <p className="eyebrow">Context-aware travel copilot</p>
        <h1>把收藏，变成能随现实变化的行程。</h1>
        <p className="lede">基础工程框架已就绪。下一步将从 Trip、Wishlist 和确定性规划内核开始，逐步接入 Agent 与外部数据。</p>
        <div className="actions">
          <a className="primary" href="http://localhost:8000/docs">打开 API 文档</a>
          <a className="secondary" href="https://github.com/qiuxh016/TravelPilot">查看 GitHub</a>
        </div>
      </section>
      <section className="layers" aria-label="product layers">
        {layers.map(([title, description], index) => (
          <article className="layer" key={title}>
            <span className="index">0{index + 1}</span>
            <h2>{title}</h2>
            <p>{description}</p>
          </article>
        ))}
      </section>
    </main>
  );
}

