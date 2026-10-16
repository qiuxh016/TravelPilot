const configuredApiUrl = process.env.NEXT_PUBLIC_API_URL?.trim();

export const productConfig = {
  name: "TravelPilot",
  locale: "zh-CN",
  timeZone: "Asia/Shanghai",
  description: "整理旅行灵感，生成并调整自己的行程计划。",
  apiBaseUrl: configuredApiUrl || "http://localhost:8000",
} as const;
