import { expect, test } from "@playwright/test";

const api = process.env.TRAVELPILOT_TEST_API_URL ?? "http://localhost:8000";

test("行程计划页显示 Open-Meteo 实时预报及来源时间", async ({ page, request }) => {
  test.setTimeout(60_000);
  const email = `weather-live-${Date.now()}@example.invalid`;
  const password = "travelpilot-test-2026";
  const registered = await request.post(`${api}/api/v1/auth/register`, {
    data: { email, password },
  });
  expect(registered.ok()).toBeTruthy();
  const mailbox = await request.get(`${api}/api/v1/auth/dev-mailbox/${email}`);
  expect(mailbox.ok()).toBeTruthy();
  const mailboxData = await mailbox.json() as { link?: string };
  expect(mailboxData.link).toBeTruthy();
  const token = new URL(mailboxData.link!, api).searchParams.get("token")!;
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

  const start = new Date(Date.now() + 2 * 86_400_000).toISOString().slice(0, 10);
  const end = new Date(Date.now() + 3 * 86_400_000).toISOString().slice(0, 10);
  const futureStart = new Date(Date.now() + 25 * 86_400_000).toISOString().slice(0, 10);
  const futureEnd = new Date(Date.now() + 26 * 86_400_000).toISOString().slice(0, 10);
  let tripId: string | null = null;
  let futureTripId: string | null = null;
  try {
    const created = await request.post(`${api}/api/v1/trips`, {
      headers: { "X-CSRF-Token": decodeURIComponent(csrf) },
      data: { destination: "成都", start_date: start, end_date: end, pace_level: 2 },
    });
    expect(created.ok()).toBeTruthy();
    tripId = (await created.json()).id as string;

    const weatherResponse = await request.get(`${api}/api/v1/trips/${tripId}/weather`);
    expect(weatherResponse.status()).toBe(200);
    const weather = await weatherResponse.json() as {
      source: string; status: string; fetched_at: string; attribution: string;
      days: { date: string }[];
    };
    expect(weather.source).toBe("Open-Meteo");
    expect(weather.status).toBe("AVAILABLE");
    expect(weather.days.map((day) => day.date)).toEqual([start, end]);
    expect(weather.fetched_at).toBeTruthy();
    expect(weather.attribution).toContain("Open-Meteo");

    await page.goto(`/trips/${tripId}/plan`);
    await expect(page.getByRole("heading", { name: "成都", level: 2 })).toBeVisible();
    await expect(page.locator(".demo-badge")).toHaveText("Open-Meteo");
    await expect(page.getByText(start, { exact: true })).toBeVisible();
    await expect(page.getByText(end, { exact: true })).toBeVisible();
    await expect(page.getByRole("link", { name: "Open-Meteo" })).toBeVisible();
    await expect(page.getByText(/预报获取于/)).toBeVisible();

    const futureTripResponse = await request.post(`${api}/api/v1/trips`, {
      headers: { "X-CSRF-Token": decodeURIComponent(csrf) },
      data: { destination: "成都", start_date: futureStart, end_date: futureEnd, pace_level: 2 },
    });
    expect(futureTripResponse.ok()).toBeTruthy();
    futureTripId = (await futureTripResponse.json()).id as string;
    const outOfRangeResponse = await request.get(`${api}/api/v1/trips/${futureTripId}/weather`);
    expect(outOfRangeResponse.status()).toBe(200);
    const outOfRange = await outOfRangeResponse.json() as { status: string; days: unknown[]; message: string };
    expect(outOfRange.status).toBe("OUT_OF_RANGE");
    expect(outOfRange.days).toHaveLength(0);
    expect(outOfRange.message).toContain("暂未覆盖");
    await page.goto(`/trips/${futureTripId}/plan`);
    await expect(page.getByText("当前预报暂未覆盖这段行程日期。", { exact: true })).toBeVisible();
  } finally {
    if (tripId || futureTripId) {
      await request.delete(`${api}/api/v1/account`, {
        headers: { "X-CSRF-Token": decodeURIComponent(csrf) },
      });
    }
  }
});
