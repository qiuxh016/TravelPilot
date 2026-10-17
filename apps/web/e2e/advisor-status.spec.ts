import { expect, test } from "@playwright/test";

const api = process.env.TRAVELPILOT_TEST_API_URL ?? "http://localhost:8000";

test("旅行顾问显示服务端模型配置状态且不暴露密钥", async ({ page, request }) => {
  const email = `advisor-status-${Date.now()}@example.invalid`;
  const password = "travelpilot-test-2026";
  const registered = await request.post(`${api}/api/v1/auth/register`, { data: { email, password } });
  expect(registered.ok()).toBeTruthy();
  const mailbox = await request.get(`${api}/api/v1/auth/dev-mailbox/${email}`);
  const token = new URL((await mailbox.json()).link).searchParams.get("token")!;
  expect((await request.post(`${api}/api/v1/auth/verify-email`, { data: { token } })).ok()).toBeTruthy();
  const login = await request.post(`${api}/api/v1/auth/login`, { data: { email, password } });
  expect(login.ok()).toBeTruthy();
  const cookies = (await request.storageState()).cookies;
  const csrf = cookies.find((cookie) => cookie.name === "travelpilot_csrf")?.value ?? "";
  await page.context().addCookies(cookies);

  try {
    await page.goto("/advisor");
    const status = page.getByRole("status").filter({ hasText: "模型已配置" });
    await expect(status).toBeVisible();
    await expect(status).not.toContainText(/sk-|api[_-]?key|密钥值/i);
  } finally {
    await request.delete(`${api}/api/v1/account`, {
      headers: { "X-CSRF-Token": decodeURIComponent(csrf) },
    });
  }
});
