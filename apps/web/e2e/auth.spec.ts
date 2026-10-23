import { expect, test } from "@playwright/test";

const api = process.env.TRAVELPILOT_E2E_API_URL ?? "http://localhost:8000";

test("用户可以通过登录页进入自己的旅行空间", async ({ page, request }) => {
  const email = `login-${Date.now()}@example.invalid`;
  const password = "travelpilot-test-2026";
  const created = await request.post(`${api}/api/v1/auth/register`, {
    data: { email, password },
  });
  expect(created.ok()).toBeTruthy();
  const mailbox = await request.get(`${api}/api/v1/auth/dev-mailbox/${email}`);
  const verificationToken = new URL((await mailbox.json()).link).searchParams.get("token")!;
  expect((await request.post(`${api}/api/v1/auth/verify-email`, { data: { token: verificationToken } })).ok()).toBeTruthy();

  await page.goto("/login");
  await page.getByLabel("邮箱或用户名").fill(email);
  await page.getByLabel(/密码/).fill(password);
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page).toHaveURL(/\/trips$/);
  await expect(page.getByRole("heading", { name: /下一段旅行/ })).toBeVisible();
  await page.getByRole("main").getByRole("link", { name: /我已经有计划/ }).click();
  await page.getByLabel("目的地").fill("CSRF 验收城市");
  await page.getByLabel(/出发日期/).fill("2026-11-01");
  await page.getByLabel("结束日期").fill("2026-11-03");
  await page.getByRole("button", { name: "创建这段旅行" }).click();
  await expect(page).toHaveURL(/\/trips\/[0-9a-f-]+$/);

  const session = await request.post(`${api}/api/v1/auth/login`, {
    data: { email, password },
  });
  expect(session.ok()).toBeTruthy();
  await request.delete(`${api}/api/v1/account`, {
    headers: { "X-CSRF-Token": await getCsrf(request) },
  });
});

test("注册邮箱验证与密码重置链接可用", async ({ page, request }) => {
  const email = `verify-reset-${Date.now()}@example.invalid`;
  const password = "travelpilot-test-2026";
  await page.goto("/login");
  await page.getByRole("button", { name: "创建新账户" }).click();
  await page.getByLabel("邮箱").fill(email);
  await page.getByLabel("用户名").fill(`verify_${Date.now()}`);
  await page.getByLabel("昵称").fill("验证用户");
  await page.getByLabel(/密码/).fill(password);
  await page.getByRole("button", { name: "注册" }).click();
  await expect(page.getByRole("status")).toContainText("检查邮箱");

  const mailbox = await request.get(`${api}/api/v1/auth/dev-mailbox/${email}`);
  const verificationUrl = (await mailbox.json()).link as string;
  const resend = await request.post(`${api}/api/v1/auth/verify-email/resend`, {
    data: { email },
  });
  expect(resend.ok()).toBeTruthy();
  const mailboxAfterResend = await request.get(`${api}/api/v1/auth/dev-mailbox/${email}`);
  expect((await mailboxAfterResend.json()).link).toBe(verificationUrl);
  await page.goto(verificationUrl);
  await expect(page.getByRole("status")).toContainText("验证成功");

  await page.goto("/forgot-password");
  await page.getByLabel("邮箱").fill(email);
  await page.getByRole("button", { name: "发送重置链接" }).click();
  await expect(page.getByRole("status")).toContainText("已发送");
  const resetMailbox = await request.get(`${api}/api/v1/auth/dev-mailbox/${email}`);
  const resetLink = (await resetMailbox.json()).link as string;
  const repeatedReset = await request.post(`${api}/api/v1/auth/password-reset/request`, {
    data: { email },
  });
  expect(repeatedReset.ok()).toBeTruthy();
  const resetMailboxAfterRepeat = await request.get(`${api}/api/v1/auth/dev-mailbox/${email}`);
  expect((await resetMailboxAfterRepeat.json()).link).toBe(resetLink);
  await page.goto(resetLink);
  await page.getByLabel(/新密码/).fill("travelpilot-reset-2026");
  await page.getByRole("button", { name: "更新密码" }).click();
  await expect(page.getByRole("status")).toContainText("密码已更新");

  const login = await request.post(`${api}/api/v1/auth/login`, {
    data: { email, password: "travelpilot-reset-2026" },
  });
  expect(login.ok()).toBeTruthy();
  await request.delete(`${api}/api/v1/account`, {
    headers: { "X-CSRF-Token": await getCsrf(request) },
  });
});

async function getCsrf(request: import("@playwright/test").APIRequestContext) {
  const state = await request.storageState();
  return state.cookies.find((cookie) => cookie.name === "travelpilot_csrf")?.value ?? "";
}
