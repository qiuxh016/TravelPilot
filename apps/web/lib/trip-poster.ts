import type { Plan, Trip, WishlistItem } from "./api";

type PosterStop = Plan["nodes"][number] & { category: string };

const categoryLabels: Record<string, string> = {
  ATTRACTION: "游览", RESTAURANT: "餐饮", CAFE: "咖啡", SHOPPING: "街区", CUSTOM: "安排",
};

function roundedRect(ctx: CanvasRenderingContext2D, x: number, y: number, width: number, height: number, radius: number) {
  ctx.beginPath();
  ctx.roundRect(x, y, width, height, radius);
}

function wrappedText(ctx: CanvasRenderingContext2D, value: string, x: number, y: number, maxWidth: number, lineHeight: number) {
  let line = "";
  let lineY = y;
  for (const char of value) {
    const next = line + char;
    if (ctx.measureText(next).width > maxWidth && line) {
      ctx.fillText(line, x, lineY);
      line = char;
      lineY += lineHeight;
    } else line = next;
  }
  if (line) ctx.fillText(line, x, lineY);
  return lineY;
}

export async function downloadTripPoster(trip: Trip, plan: Plan, wishlist: WishlistItem[]) {
  const width = 1200;
  const stopsByDay = new Map<string, PosterStop[]>();
  const categories = new Map(wishlist.map((item) => [item.poi_id, item.category]));
  for (const stop of [...plan.nodes].sort((a, b) => a.day.localeCompare(b.day) || a.start_time.localeCompare(b.start_time))) {
    const items = stopsByDay.get(stop.day) ?? [];
    items.push({ ...stop, category: categories.get(stop.poi_id) ?? "CUSTOM" });
    stopsByDay.set(stop.day, items);
  }
  const days = [...stopsByDay.entries()];
  if (!days.length) throw new Error("当前计划还没有可导出的安排。");
  const dayHeights = days.map(([, stops]) => Math.max(190, 92 + stops.length * 54));
  const height = 1160 + dayHeights.reduce((sum, value) => sum + value, 0);
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("无法生成行程图片，请换用最新版浏览器重试。");

  ctx.fillStyle = "#f7f5ed";
  ctx.fillRect(0, 0, width, height);
  // Fine paper grain and quiet map contour lines create a hand-drawn travel-journal feel.
  ctx.strokeStyle = "rgba(48, 91, 71, .075)";
  ctx.lineWidth = 2;
  for (let index = 0; index < 8; index++) {
    const y = 230 + index * 84;
    ctx.beginPath();
    ctx.moveTo(45, y + 70);
    ctx.bezierCurveTo(280, y - 65, 510, y + 160, 720, y + 30);
    ctx.bezierCurveTo(870, y - 55, 1000, y + 75, 1155, y - 14);
    ctx.stroke();
  }
  ctx.fillStyle = "#2c5947";
  ctx.font = "600 23px Arial, 'Microsoft YaHei', sans-serif";
  ctx.fillText("TRAVELPILOT   ·   MY JOURNEY", 82, 90);
  ctx.fillStyle = "#20392f";
  ctx.font = "700 66px Georgia, 'Songti SC', 'Microsoft YaHei', serif";
  wrappedText(ctx, trip.destination, 78, 190, 1040, 76);
  ctx.fillStyle = "#69766e";
  ctx.font = "28px Arial, 'Microsoft YaHei', sans-serif";
  ctx.fillText(`${trip.start_date}  —  ${trip.end_date}     ·     ${days.length} 天行程`, 82, 252);

  // Draw an illustrated itinerary line with numbered stops on a map-like field.
  const mapTop = 310;
  const mapBottom = 930;
  const allStops = days.flatMap(([, stops]) => stops);
  const geo = allStops.map((stop) => wishlist.find((item) => item.poi_id === stop.poi_id)).filter((item): item is WishlistItem => Boolean(item && Number.isFinite(item.latitude) && Number.isFinite(item.longitude)));
  const minLat = geo.length ? Math.min(...geo.map((item) => item.latitude)) : 0;
  const maxLat = geo.length ? Math.max(...geo.map((item) => item.latitude)) : 1;
  const minLon = geo.length ? Math.min(...geo.map((item) => item.longitude)) : 0;
  const maxLon = geo.length ? Math.max(...geo.map((item) => item.longitude)) : 1;
  const routePoints = allStops.map((stop, index) => {
    const place = wishlist.find((item) => item.poi_id === stop.poi_id);
    const hasGeo = place && Number.isFinite(place.latitude) && Number.isFinite(place.longitude);
    const spread = Math.max(1, allStops.length - 1);
    const x = hasGeo && maxLon !== minLon ? 140 + ((place.longitude - minLon) / (maxLon - minLon)) * 900 : 150 + (index / spread) * 880;
    const y = hasGeo && maxLat !== minLat ? mapBottom - 95 - ((place.latitude - minLat) / (maxLat - minLat)) * 440 : mapTop + 105 + ((index * 137) % 420);
    return { x, y, stop };
  });
  if (routePoints.length > 1) {
    ctx.beginPath();
    routePoints.forEach((point, index) => index ? ctx.lineTo(point.x, point.y) : ctx.moveTo(point.x, point.y));
    ctx.strokeStyle = "#b95736";
    ctx.lineWidth = 5;
    ctx.setLineDash([11, 12]);
    ctx.lineCap = "round";
    ctx.stroke();
    ctx.setLineDash([]);
  }
  routePoints.forEach(({ x, y, stop }, index) => {
    ctx.beginPath();
    ctx.arc(x, y, 28, 0, Math.PI * 2);
    ctx.fillStyle = "#2c5947";
    ctx.fill();
    ctx.lineWidth = 6;
    ctx.strokeStyle = "#f7f5ed";
    ctx.stroke();
    ctx.fillStyle = "#fffdf7";
    ctx.font = "700 22px Arial, sans-serif";
    ctx.textAlign = "center";
    ctx.fillText(String(index + 1), x, y + 8);
    ctx.textAlign = "left";
    if (index < 7) {
      ctx.fillStyle = "#294335";
      ctx.font = "600 21px Arial, 'Microsoft YaHei', sans-serif";
      ctx.fillText(stop.name.slice(0, 12), x + 37, y - 10);
    }
  });
  ctx.fillStyle = "#8a958d";
  ctx.font = "20px Arial, 'Microsoft YaHei', sans-serif";
  ctx.fillText("路线示意 · 按行程顺序标注", 82, 1000);

  let cursorY = 1060;
  days.forEach(([day, stops], dayIndex) => {
    const dayHeight = dayHeights[dayIndex];
    roundedRect(ctx, 70, cursorY, width - 140, dayHeight - 18, 24);
    ctx.fillStyle = "rgba(255,255,255,.78)";
    ctx.fill();
    ctx.strokeStyle = "rgba(44,89,71,.16)";
    ctx.lineWidth = 2;
    ctx.stroke();
    ctx.fillStyle = "#b95736";
    ctx.font = "700 22px Arial, 'Microsoft YaHei', sans-serif";
    ctx.fillText(`DAY ${String(dayIndex + 1).padStart(2, "0")}`, 100, cursorY + 43);
    ctx.fillStyle = "#20392f";
    ctx.font = "600 26px Arial, 'Microsoft YaHei', sans-serif";
    ctx.fillText(day, 250, cursorY + 43);
    stops.forEach((stop, stopIndex) => {
      const lineY = cursorY + 91 + stopIndex * 54;
      ctx.fillStyle = "#567361";
      ctx.font = "600 21px Arial, 'Microsoft YaHei', sans-serif";
      ctx.fillText(`${stop.start_time.slice(0, 5)}  ${categoryLabels[stop.category] ?? "安排"}`, 100, lineY);
      ctx.fillStyle = "#263b31";
      ctx.font = "24px Arial, 'Microsoft YaHei', sans-serif";
      ctx.fillText(stop.name.slice(0, 26), 390, lineY);
      if (stopIndex < stops.length - 1) {
        ctx.strokeStyle = "rgba(44,89,71,.12)";
        ctx.beginPath(); ctx.moveTo(100, lineY + 22); ctx.lineTo(width - 100, lineY + 22); ctx.stroke();
      }
    });
    cursorY += dayHeight;
  });
  ctx.fillStyle = "#718078";
  ctx.font = "19px Arial, 'Microsoft YaHei', sans-serif";
  ctx.fillText("由 TravelPilot 整理 · 路线为行程示意，出行前请核对开放时间与实时交通", 82, height - 46);

  const blob = await new Promise<Blob>((resolve, reject) => canvas.toBlob((value) => value ? resolve(value) : reject(new Error("图片生成失败，请重试。")), "image/png"));
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  const safeDestination = trip.destination.replace(/[\\/:*?"<>|\s]+/g, "-").slice(0, 40) || "旅行";
  anchor.download = `${safeDestination}-${trip.start_date}-行程手账.png`;
  anchor.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}
