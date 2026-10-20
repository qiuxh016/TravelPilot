import { expect, test } from "@playwright/test";

const api = process.env.TRAVELPILOT_TEST_API_URL ?? "http://localhost:8000";

test("可重试服务错误保留原消息并复用幂等键", async ({ page, request }, testInfo) => {
  const pageErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error" && !message.text().startsWith("Failed to load resource:")) {
      pageErrors.push(message.text());
    }
  });
  const email = `advisor-retry-${Date.now()}@example.invalid`;
  const password = "travelpilot-test-2026";
  let registered = false;
  let csrf = "";
  try {
  const registration = await request.post(`${api}/api/v1/auth/register`, {
    data: { email, password },
  });
  expect(registration.status()).toBe(202);
  registered = true;

  const mailbox = await request.get(`${api}/api/v1/auth/dev-mailbox/${email}`);
  const token = new URL((await mailbox.json()).link).searchParams.get("token")!;
  expect((await request.post(`${api}/api/v1/auth/verify-email`, { data: { token } })).ok()).toBeTruthy();
  expect((await request.post(`${api}/api/v1/auth/login`, { data: { email, password } })).ok()).toBeTruthy();

  const cookies = (await request.storageState()).cookies;
  csrf = cookies.find((cookie) => cookie.name === "travelpilot_csrf")?.value ?? "";
  await page.context().addCookies(cookies);
  const created = await request.post(`${api}/api/v1/advisor/sessions`, {
    headers: { "X-CSRF-Token": decodeURIComponent(csrf) },
    data: { mode: "discover", preference_text: "", pace_level: 3 },
  });
  expect(created.status()).toBe(201);
  const session = await created.json() as {
    id: string;
    messages: Array<Record<string, unknown>>;
  };
  const sessionListResponse = await request.get(`${api}/api/v1/advisor/sessions`);
  expect(sessionListResponse.ok()).toBeTruthy();
  const listedSessions = await sessionListResponse.json() as Array<Record<string, unknown>>;
  const listedSession = listedSessions.find((item) => item.id === session.id);
  expect(listedSession).toBeTruthy();
  let persistedMessages = session.messages;
  await page.route(`${api}/api/v1/advisor/sessions`, async (route) => {
    await route.fulfill({ json: [{ ...listedSession, messages: persistedMessages }] });
  });
  const keys: string[] = [];
  let attempts = 0;
  await page.route(`${api}/api/v1/advisor/sessions/${session.id}/messages`, async (route) => {
    attempts += 1;
    keys.push(route.request().headers()["idempotency-key"] ?? "");
    if (attempts === 1) {
      const content = (route.request().postDataJSON() as { content: string }).content;
      persistedMessages = [...session.messages, {
        id: crypto.randomUUID(), role: "user", content, payload: null,
        created_at: new Date().toISOString(),
      }];
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ detail: "模拟服务暂时不可用" }),
      });
      return;
    }
    if (attempts === 3) {
      await route.fulfill({
        status: 409,
        contentType: "application/json",
        body: JSON.stringify({ detail: "会话已有后续消息，请刷新后重试" }),
      });
      return;
    }
    const content = (route.request().postDataJSON() as { content: string }).content;
    const now = new Date().toISOString();
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        ...session,
        messages: [
          ...session.messages,
          { id: crypto.randomUUID(), role: "user", content, payload: null, created_at: now },
          { id: crypto.randomUUID(), role: "assistant", content: "请求已通过安全重试完成。", payload: {}, created_at: now },
        ],
      }),
    });
  });

  await page.goto(`/advisor?sessionId=${session.id}`);
  await expect(page).toHaveURL(new RegExp(`/advisor\\?sessionId=${session.id}`));
  await expect(page).toHaveTitle("TravelPilot");
  const composer = page.getByPlaceholder("说说你想去哪里、喜欢什么，或想调整的安排…");
  await composer.fill("帮我介绍一下旅行规划");
  await page.getByRole("button", { name: "发送" }).click();
  await expect(page.locator(".advisor-live-error")).toContainText("模拟服务暂时不可用");
  await page.reload();
  await expect(page.locator(".advisor-live-error")).toContainText("上次顾问请求尚未收到回复");
  const screenshotPrefix = process.env.TRAVELPILOT_QA_SCREENSHOT;
  if (screenshotPrefix) {
    await page.screenshot({ path: `${screenshotPrefix}-${testInfo.project.name}-retry-prompt.png` });
  }
  await page.getByRole("button", { name: "重试刚才请求" }).click();
  await expect(page.getByText("请求已通过安全重试完成。")).toBeVisible();
  expect(attempts).toBe(2);
  expect(keys[0]).toBeTruthy();
  expect(keys[1]).toBe(keys[0]);
  await composer.fill("我有另一条新问题");
  await page.getByRole("button", { name: "发送" }).click();
  await expect(page.locator(".advisor-live-error")).toContainText("会话已有后续消息");
  await expect(page.getByRole("button", { name: "重试刚才请求" })).toHaveCount(0);
  expect(attempts).toBe(3);
  expect(keys[2]).not.toBe(keys[1]);
  expect(pageErrors).toEqual([]);
  } finally {
    if (registered && csrf) {
      await request.delete(`${api}/api/v1/account`, {
        headers: { "X-CSRF-Token": decodeURIComponent(csrf) },
      });
    }
  }
});
