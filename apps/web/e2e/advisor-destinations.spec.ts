import { expect, test } from "@playwright/test";

const api = process.env.TRAVELPILOT_TEST_API_URL ?? "http://localhost:8000";

test("未定目的地候选可查看地图证据并将所选城市写入顾问会话", async ({ page, request }) => {
  const browserErrors: string[] = [];
  page.on("pageerror", (error) => browserErrors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error") browserErrors.push(message.text());
  });
  const email = `advisor-destination-${Date.now()}@example.invalid`;
  const password = "travelpilot-test-2026";
  expect((await request.post(`${api}/api/v1/auth/register`, { data: { email, password } })).ok()).toBeTruthy();
  const mailbox = await request.get(`${api}/api/v1/auth/dev-mailbox/${email}`);
  const token = new URL((await mailbox.json()).link).searchParams.get("token")!;
  expect((await request.post(`${api}/api/v1/auth/verify-email`, { data: { token } })).ok()).toBeTruthy();
  expect((await request.post(`${api}/api/v1/auth/login`, { data: { email, password } })).ok()).toBeTruthy();

  const cookies = (await request.storageState()).cookies;
  const csrf = cookies.find((cookie) => cookie.name === "travelpilot_csrf")?.value ?? "";
  await page.context().addCookies(cookies);
  const today = new Date();
  const start = new Date(today); start.setDate(today.getDate() + 30);
  const end = new Date(start); end.setDate(start.getDate() + 1);
  const toDate = (value: Date) => value.toISOString().slice(0, 10);
  const created = await request.post(`${api}/api/v1/advisor/sessions`, {
    headers: { "X-CSRF-Token": decodeURIComponent(csrf) },
    data: { mode: "discover", origin: "上海", preference_text: "喜欢海边和本地美食",
      start_date: toDate(start), end_date: toDate(end), pace_level: 3 },
  });
  expect(created.status()).toBe(201);
  const session = await created.json();
  const liveMode = process.env.TRAVELPILOT_LIVE_DESTINATION === "1";
  const sampleRecommendation = {
    city: "厦门", matched_interests: ["海滨休闲", "地方美食"], place_count: 4,
    reasons: ["地图检索到与你关注的海滨休闲相关地点"], provider: "amap", retrieved_at: new Date().toISOString(),
    places: [{ name: "环岛路", category: "风景名胜", address: "厦门市思明区", provider: "amap",
      provider_id: "test-xiamen-route", latitude: 24.45, longitude: 118.08 }],
  };
  if (liveMode) {
    await page.route("http://localhost:8000/**", (route) => {
      const target = new URL(route.request().url());
      target.port = new URL(api).port;
      return route.continue({ url: target.toString() });
    });
  } else {
    await page.route(`${api}/api/v1/advisor/sessions/${session.id}/destinations`, async (route) => {
      const updated = { ...session, messages: [...session.messages, {
        id: crypto.randomUUID(), role: "assistant", content: "按地图证据整理候选。",
        payload: { event: "DESTINATION_RECOMMENDATIONS", recommendations: [sampleRecommendation] },
        created_at: new Date().toISOString(),
      }] };
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(updated) });
    });
  }

  try {
    await page.goto(`/advisor?sessionId=${session.id}`);
    await expect(page.getByRole("button", { name: "按偏好比较目的地" })).toBeVisible();
    await page.getByRole("button", { name: "按偏好比较目的地" }).click();
    if (liveMode) {
      await expect(page.getByText("候选来自高德地点检索")).toBeVisible();
      await expect(page.locator(".advisor-destination-grid article")).toHaveCount(3);
    } else {
      await expect(page.getByRole("heading", { name: "厦门", exact: true })).toBeVisible();
      await expect(page.getByText("环岛路")).toBeVisible();
    }
    await page.setViewportSize({ width: 390, height: 844 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
    if (process.env.TRAVELPILOT_QA_SCREENSHOT) {
      await page.screenshot({ path: process.env.TRAVELPILOT_QA_SCREENSHOT, fullPage: true });
    }
    await page.setViewportSize({ width: 1440, height: 1000 });
    const chosenCity = (await page.locator(".advisor-destination-grid article h3").first().textContent())?.trim();
    expect(chosenCity).toBeTruthy();
    await page.locator(".advisor-destination-grid article").first().getByRole("button", { name: "选择这个城市" }).click();
    await expect(page.locator(".advisor-action-notice")).toContainText(new RegExp(`${chosenCity}\\s*已设为当前目的地`));
    const persisted = await request.get(`${api}/api/v1/advisor/sessions/${session.id}`);
    expect((await persisted.json()).destination).toBe(chosenCity);
    expect(browserErrors).toEqual([]);
  } finally {
    await request.delete(`${api}/api/v1/account`, {
      headers: { "X-CSRF-Token": decodeURIComponent(csrf) },
    });
  }
});
