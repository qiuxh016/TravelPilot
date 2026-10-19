import { expect, test } from "@playwright/test";

const api = process.env.TRAVELPILOT_TEST_API_URL ?? "http://localhost:8000";
const stub = process.env.TRAVELPILOT_TEST_STUB_URL ?? "http://127.0.0.1:8002";

test("天气证据卡清楚标注本地模拟预报，不伪装成真实天气", async ({ page, request, context }, testInfo) => {
  test.setTimeout(60_000);
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error" && !message.text().startsWith("Failed to load resource:")) {
      errors.push(message.text());
    }
  });

  const email = `advisor-weather-ui-${Date.now()}@example.invalid`;
  const password = "travelpilot-test-2026";
  let registered = false;
  let authenticated = false;
  let csrf = "";
  try {
    expect((await request.post(`${stub}/reset`)).status()).toBe(204);
    const registration = await request.post(`${api}/api/v1/auth/register`, {
      data: {
        email,
        username: `weather${Date.now()}`,
        display_name: "天气卡片验收",
        password,
      },
    });
    expect(registration.status(), await registration.text()).toBe(202);
    registered = true;

    const mailbox = await request.get(`${api}/api/v1/auth/dev-mailbox/${email}`);
    const link = (await mailbox.json()).link as string;
    const token = new URL(link, api).searchParams.get("token");
    expect(token).toBeTruthy();
    expect((await request.post(`${api}/api/v1/auth/verify-email`, { data: { token } })).ok()).toBeTruthy();
    const login = await request.post(`${api}/api/v1/auth/login`, {
      data: { account: email, password },
    });
    expect(login.ok(), await login.text()).toBeTruthy();
    authenticated = true;

    const cookies = (await request.storageState()).cookies;
    csrf = decodeURIComponent(cookies.find((cookie) => cookie.name === "travelpilot_csrf")?.value ?? "");
    expect(csrf).not.toBe("");
    await context.addCookies(cookies);
    const headers = { "X-CSRF-Token": csrf };

    const start = new Date();
    start.setUTCDate(start.getUTCDate() + 30);
    const end = new Date(start);
    end.setUTCDate(end.getUTCDate() + 1);
    const format = (date: Date) => date.toISOString().slice(0, 10);
    const tripResponse = await request.post(`${api}/api/v1/trips`, {
      headers,
      data: { destination: "成都", start_date: format(start), end_date: format(end), pace_level: 2 },
    });
    expect(tripResponse.ok(), await tripResponse.text()).toBeTruthy();
    const trip = await tripResponse.json() as { id: string };
    const sessionResponse = await request.post(`${api}/api/v1/advisor/sessions`, {
      headers,
      data: { mode: "existing", trip_id: trip.id },
    });
    expect(sessionResponse.ok(), await sessionResponse.text()).toBeTruthy();
    const session = await sessionResponse.json() as { id: string };

    await page.goto(`/advisor?sessionId=${session.id}`);
    await expect(page.getByRole("region", { name: "顾问对话" })).toBeVisible();
    await page.locator("#advisor-message").fill(
      "结合这段旅行的天气看看是否下雨；如果有雨，帮我找成都室内备选地点，先推荐看看，不要加入行程。",
    );
    await page.getByRole("button", { name: "发送" }).click();

    const weatherCard = page.getByRole("region", { name: "本轮旅行天气证据" }).last();
    await expect(weatherCard).toBeVisible({ timeout: 45_000 });
    await expect(weatherCard).toContainText("本地模拟 · 非真实预报");
    await expect(weatherCard).toContainText("小雨（模拟）");
    await expect(page.locator(".advisor-live-candidate")
      .filter({ hasText: "模拟室内展馆" })).toBeVisible();
    await expect(page.getByText(/本地模拟.*非真实预报/).first()).toBeVisible();
    expect(errors).toEqual([]);

    const screenshotPath = process.env.TRAVELPILOT_QA_SCREENSHOT;
    if (screenshotPath) {
      const suffix = testInfo.project.name === "chromium" ? "desktop" : "mobile";
      await page.screenshot({ path: screenshotPath.replace("{viewport}", suffix), fullPage: true });
    }
  } finally {
    if (registered && !authenticated) {
      const retryLogin = await request.post(`${api}/api/v1/auth/login`, {
        data: { account: email, password },
      });
      authenticated = retryLogin.ok();
      if (authenticated) {
        const cookies = (await request.storageState()).cookies;
        csrf = decodeURIComponent(cookies.find((cookie) => cookie.name === "travelpilot_csrf")?.value ?? "");
      }
    }
    if (registered && authenticated && csrf) {
      const deletion = await request.delete(`${api}/api/v1/account`, {
        headers: { "X-CSRF-Token": csrf },
      });
      expect(deletion.status()).toBe(204);
    }
  }
});
