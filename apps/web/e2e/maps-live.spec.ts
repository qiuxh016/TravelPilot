import { expect, test } from "@playwright/test";

const api = process.env.TRAVELPILOT_TEST_API_URL ?? "http://localhost:8000";

test("地点探索使用高德结果并在页面绘制真实地图", async ({ page, request }) => {
  test.setTimeout(60_000);
  const email = `maps-live-${Date.now()}@example.invalid`;
  const password = "travelpilot-test-2026";
  const registered = await request.post(`${api}/api/v1/auth/register`, {
    data: { email, password },
  });
  expect(registered.ok()).toBeTruthy();
  const mailbox = await request.get(`${api}/api/v1/auth/dev-mailbox/${email}`);
  const token = new URL((await mailbox.json()).link).searchParams.get("token")!;
  expect((await request.post(`${api}/api/v1/auth/verify-email`, { data: { token } })).ok()).toBeTruthy();
  const login = await request.post(`${api}/api/v1/auth/login`, { data: { email, password } });
  expect(login.ok()).toBeTruthy();
  const cookies = (await request.storageState()).cookies;
  const csrf = cookies.find((cookie) => cookie.name === "travelpilot_csrf")?.value ?? "";
  await page.context().addCookies(cookies);
  await page.route("http://localhost:8000/**", (route) => {
    const target = new URL(route.request().url());
    target.port = new URL(api).port;
    return route.continue({ url: target.toString() });
  });

  let tripId: string | null = null;
  try {
    const created = await request.post(`${api}/api/v1/trips`, {
      headers: { "X-CSRF-Token": decodeURIComponent(csrf) },
      data: { destination: "成都", start_date: "2026-10-20", end_date: "2026-10-20", pace_level: 2 },
    });
    expect(created.ok()).toBeTruthy();
    tripId = (await created.json()).id as string;

    const search = await request.get(`${api}/api/v1/places/search`, {
      params: { keyword: "宽窄巷子", city: "成都" },
    });
    expect(search.status()).toBe(200);
    const places = await search.json() as { provider: string; provider_id: string; name: string; latitude: number; longitude: number }[];
    expect(places.length).toBeGreaterThan(0);
    expect(new Set(places.map((place) => place.provider))).toEqual(new Set(["amap"]));
    expect(places.length).toBeGreaterThan(1);
    const first = places[0];
    const detail = await request.get(`${api}/api/v1/places/${encodeURIComponent(first.provider_id)}`);
    expect(detail.status()).toBe(200);
    expect((await detail.json()).provider).toBe("amap");
    const second = places.find((place) => place.provider_id !== first.provider_id) ?? first;
    const route = await request.post(`${api}/api/v1/routes/estimate`, {
      headers: { "X-CSRF-Token": decodeURIComponent(csrf) },
      data: { origin: [first.latitude, first.longitude], destination: [second.latitude, second.longitude] },
    });
    expect(route.status()).toBe(200);
    const routeData = await route.json() as { provider: string; distance_m: number; duration_min: number; polyline: [number, number][] };
    expect(routeData.provider).toBe("amap");
    expect(routeData.distance_m).toBeGreaterThanOrEqual(0);
    expect(routeData.duration_min).toBeGreaterThan(0);
    expect(routeData.polyline.length).toBeGreaterThan(1);
    const matrix = await request.post(`${api}/api/v1/routes/matrix`, {
      headers: { "X-CSRF-Token": decodeURIComponent(csrf) },
      data: { points: [[first.latitude, first.longitude], [second.latitude, second.longitude]] },
    });
    expect(matrix.status()).toBe(200);
    const matrixData = await matrix.json();
    expect(matrixData.provider).toBe("AmapProvider");
    expect(matrixData.durations_min).toHaveLength(2);
    expect(matrixData.durations_min.every((row: number[]) => row.length === 2 && row.every((minutes) => minutes >= 0))).toBeTruthy();

    await page.goto(`/places?tripId=${tripId}`);
    await page.getByLabel("关键词").fill("宽窄巷子");
    await page.getByRole("button", { name: "开始探索" }).click();
    await expect(page.locator(".places-result-row").first()).toBeVisible();
    const mapImage = page.locator('img[alt*="高德地图地点分布"]');
    await expect(mapImage).toBeVisible();
    await expect.poll(() => mapImage.evaluate((image: HTMLImageElement) => image.naturalWidth)).toBeGreaterThan(0);

    const firstName = (await page.locator(".places-result-name h3").first().textContent())?.trim();
    expect(firstName).toBeTruthy();
    await page.getByRole("button", { name: `收藏${firstName}` }).click();
    await expect(page.getByRole("status")).toContainText("已加入 成都");

    const secondPlace = places.find((place) => place.provider_id !== first.provider_id)!;
    const addedSecond = await request.post(`${api}/api/v1/trips/${tripId}/wishlist`, {
      headers: { "X-CSRF-Token": decodeURIComponent(csrf) },
      data: { name: secondPlace.name, category: "ATTRACTION", address: "", latitude: secondPlace.latitude,
        longitude: secondPlace.longitude, suggested_duration_min: 60, source_type: "MAPS" },
    });
    expect(addedSecond.status()).toBe(201);

    await page.getByRole("link", { name: "行程计划" }).click();
    await expect(page.getByRole("heading", { name: "把想去的地方，排成一天" })).toBeVisible();
    await page.getByRole("button", { name: "生成我的第一版计划" }).click();
    await expect(page.getByText(/计划 v1 · READY/)).toBeVisible();
    await expect(page.getByText(/道路轨迹按高德驾车规划绘制/)).toBeVisible();
    const plannedMap = page.locator('img[alt*="高德地图地点分布"]');
    await expect(plannedMap).toBeVisible();
    await expect.poll(() => plannedMap.evaluate((image: HTMLImageElement) => image.naturalWidth)).toBeGreaterThan(0);

    await page.route("http://localhost:8000/api/v1/maps/static-map", (route) => route.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify({ detail: "模拟地图故障" }),
    }));
    await page.goto(`/trips/${tripId}`);
    await expect(page.getByText("模拟地图故障")).toBeVisible();
    await expect(page.getByRole("heading", { name: first.name })).toBeVisible();

    await page.route(`http://localhost:8000/api/v1/trips/${tripId}/weather`, (route) => route.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify({ detail: "模拟天气故障" }),
    }));
    await page.goto(`/trips/${tripId}/plan`);
    await expect(page.getByText("模拟天气故障")).toBeVisible();
    await expect(page.getByText(first.name, { exact: true }).last()).toBeVisible();
  } finally {
    if (tripId) {
      await request.delete(`${api}/api/v1/account`, {
        headers: { "X-CSRF-Token": decodeURIComponent(csrf) },
      });
    }
  }
});
