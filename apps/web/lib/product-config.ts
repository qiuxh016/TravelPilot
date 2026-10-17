const configuredApiUrl = process.env.NEXT_PUBLIC_API_URL?.trim();

export const productConfig = {
  name: "TravelPilot",
  locale: "zh-CN",
  timeZone: "Asia/Shanghai",
  description: "整理旅行灵感，生成并调整自己的行程计划。",
  // Production is served behind one HTTPS origin; Caddy forwards /api to FastAPI.
  // Keep local development pointed at the separately running API.
  apiBaseUrl: configuredApiUrl || (process.env.NODE_ENV === "production" ? "" : "http://localhost:8000"),
} as const;
