import { expect, test } from "@playwright/test";

const api = "http://localhost:8000/api/v1";

test("收藏地点、计划重排和旅行记忆可在页面完成", async ({ page, request }) => {
  const email = `e2e-flow-${Date.now()}@example.invalid`;
  const password = "travelpilot-test-2026";
  const auth = await request.post("http://localhost:8000/api/v1/auth/register", {
    data: { email, password },
  });
  expect(auth.ok()).toBeTruthy();
  const mailbox = await request.get(`http://localhost:8000/api/v1/auth/dev-mailbox/${email}`);
  const verifyToken = new URL((await mailbox.json()).link).searchParams.get("token")!;
  expect((await request.post("http://localhost:8000/api/v1/auth/verify-email", { data: { token: verifyToken } })).ok()).toBeTruthy();
  const login = await request.post("http://localhost:8000/api/v1/auth/login", { data: { email, password } });
  expect(login.ok()).toBeTruthy();
  const cookieState = await request.storageState();
  const csrf = cookieState.cookies.find((cookie) => cookie.name === "travelpilot_csrf")?.value ?? "";
  const headers = { "X-CSRF-Token": csrf };
  await page.context().addCookies(cookieState.cookies);
  const created = await request.post(`${api}/trips`, {
    headers,
    data: { destination: "成都", start_date: "2026-10-20", end_date: "2026-10-21", budget: 6000, pace_level: 2 },
  });
  expect(created.ok()).toBeTruthy();
  const trip = await created.json() as { id: string };
  try {
    const saved = await request.post(`${api}/trips/${trip.id}/wishlist`, {
      headers,
      data: { name: "宽窄巷子", category: "ATTRACTION", latitude: 30.6633,
        longitude: 104.0647, suggested_duration_min: 90 },
    });
    expect(saved.ok()).toBeTruthy();
    const poi = await saved.json() as { poi_id: string };

    await page.goto(`/trips/${trip.id}/plan`);
    await page.getByRole("button", { name: "生成我的第一版计划" }).click();
    await expect(page.getByText("计划 v1 · READY")).toBeVisible();
    await page.goto(`/trips/${trip.id}/events`);
    await page.getByLabel("事件类型").selectOption("POI_CANCELLED");
    await page.getByLabel("POI ID").selectOption(poi.poi_id);
    await page.getByLabel("生效时间").fill("2026-10-20T09:00");
    await page.getByRole("button", { name: "看看怎么调整" }).click();
    await expect(page.getByRole("heading", { name: "新的安排" })).toBeVisible();
    await expect(page.getByText(/调整成本/)).toBeVisible();
    if (process.env.TRAVELPILOT_EVENT_SCREENSHOT_PATH) {
      await page.screenshot({ path: process.env.TRAVELPILOT_EVENT_SCREENSHOT_PATH });
    }

    await page.goto(`/trips/${trip.id}/memory`);
    await page.getByLabel("反馈").fill("今天很累，希望下午少安排景点");
    await page.getByRole("button", { name: "保存这段感受" }).click();
    await page.getByRole("button", { name: "确认记忆" }).click();
    await expect(page.getByText("SEMANTIC_PREFERENCE · 已确认")).toBeVisible();
    if (process.env.TRAVELPILOT_SCREENSHOT_PATH) {
      await page.screenshot({ path: process.env.TRAVELPILOT_SCREENSHOT_PATH });
    }
  } finally {
    await request.delete("http://localhost:8000/api/v1/account", { headers });
  }
});
