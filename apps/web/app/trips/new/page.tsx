"use client";

import Image from "next/image";
import Link from "next/link";
import { FormEvent, useMemo, useState } from "react";
import { useRouter } from "next/navigation";

import { ApiError, createTrip } from "../../../lib/api";

const paceLabels = ["轻松慢游", "悠闲逛逛", "张弛有度", "高效游览", "行程充实"];

export default function NewTripPage() {
  const router = useRouter();
  const [destination, setDestination] = useState("");
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [budget, setBudget] = useState("");
  const [paceLevel, setPaceLevel] = useState("3");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const tripDuration = useMemo(() => {
    if (!startDate || !endDate || endDate < startDate) return null;
    const days = Math.round((Date.parse(`${endDate}T00:00:00`) - Date.parse(`${startDate}T00:00:00`)) / 86_400_000) + 1;
    return `${days} 天 ${Math.max(0, days - 1)} 晚`;
  }, [startDate, endDate]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    if (endDate < startDate) {
      setError("结束日期不能早于开始日期，请检查行程时间。");
      return;
    }

    try {
      setLoading(true);
      const trip = await createTrip({
        destination: destination.trim(),
        start_date: startDate,
        end_date: endDate,
        budget: budget ? Number(budget) : null,
        pace_level: Number(paceLevel),
      });
      router.push(`/trips/${trip.id}`);
    } catch (cause) {
      if (cause instanceof ApiError) setError(cause.message);
      else setError(cause instanceof Error ? cause.message : "创建旅行失败，请稍后重试。");
    } finally {
      setLoading(false);
    }
  }

  return <main className="shell trip-create-page">
    <header className="trip-create-heading">
      <Link className="trip-create-back" href="/trips">← 返回我的旅行</Link>
      <div className="trip-create-step"><span>01</span><i /><span>创建旅行</span></div>
      <h1>先把旅程定下来。</h1>
      <p>从目的地和出发时间开始，之后还可以慢慢收藏地点、整理计划。</p>
    </header>

    <div className="trip-create-layout">
      <form className="trip-create-form" onSubmit={handleSubmit}>
        <div className="trip-create-section-title"><span>这趟旅行去哪里？</span><small>带 * 的信息需要填写</small></div>
        <label className="trip-create-field trip-create-destination">
          <span>目的地 <b>*</b></span>
          <input required maxLength={120} value={destination} onChange={(event) => setDestination(event.target.value)} placeholder="输入城市或地区，例如：成都" autoComplete="address-level2" />
        </label>

        <div className="trip-create-dates">
          <label className="trip-create-field">
            <span>出发日期 <b>*</b></span>
            <input required type="date" value={startDate} onChange={(event) => setStartDate(event.target.value)} />
          </label>
          <label className="trip-create-field">
            <span>结束日期 <b>*</b></span>
            <input required type="date" min={startDate || undefined} value={endDate} onChange={(event) => setEndDate(event.target.value)} />
          </label>
        </div>
        <div className={`trip-create-duration${endDate && startDate && endDate < startDate ? " is-invalid" : ""}`} aria-live="polite">
          {endDate && startDate && endDate < startDate ? "结束日期需要在出发日期当天或之后" : tripDuration ? `这是一段 ${tripDuration} 的旅程` : "选择日期后，这里会显示旅程时长"}
        </div>

        <div className="trip-create-options">
          <label className="trip-create-field">
            <span>预算 <small>选填</small></span>
            <div className="trip-create-budget"><span aria-hidden="true">¥</span><input min="0" step="0.01" type="number" value={budget} onChange={(event) => setBudget(event.target.value)} placeholder="例如 5000" /><span>元</span></div>
            <small>预算会作为后续规划的参考。</small>
          </label>

          <fieldset className="trip-create-pace">
            <legend>你喜欢怎样的旅行节奏？ <small>选填</small></legend>
            <div className="trip-create-range"><input aria-label="旅行节奏" type="range" min="1" max="5" value={paceLevel} onChange={(event) => setPaceLevel(event.target.value)} /><div className="trip-create-range-labels"><span>慢一点</span><strong>{paceLabels[Number(paceLevel) - 1]}</strong><span>多看一些</span></div></div>
            <small>之后可以随时调整；旅行顾问会参考这个节奏安排每天的行程。</small>
          </fieldset>
        </div>

        {error && <p className="trip-create-error" role="alert">{error}</p>}
        <footer className="trip-create-submit-row"><p>创建后可以继续添加地点、导入攻略或咨询旅行顾问。</p><button className="primary" type="submit" disabled={loading}>{loading ? "正在创建…" : "创建这段旅行"}<span aria-hidden="true">→</span></button></footer>
      </form>

      <aside className="trip-create-aside">
        <Image className="trip-create-image" src="/images/create-trip-evening.webp" alt="成都老街夜色与远处的山林" width={900} height={1125} priority sizes="(max-width: 900px) 100vw, 36vw" />
        <div className="trip-create-image-caption"><span>给旅程留一点想象</span><p>先定下目的地，沿途值得停留的地方，我们可以一起慢慢发现。</p></div>
      </aside>
    </div>
  </main>;
}
