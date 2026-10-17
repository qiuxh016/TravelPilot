import { expect, test } from "@playwright/test";

test("用户可以创建旅行并进入详情页", async ({ page, request }) => {
  const email = `e2e-${Date.now()}@example.invalid`;
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
  expect(await login.json()).not.toHaveProperty("access_token");
  const state = await request.storageState();
  expect(state.cookies.find((cookie) => cookie.name === "travelpilot_session")?.httpOnly).toBeTruthy();
  expect(state.cookies.find((cookie) => cookie.name === "travelpilot_csrf")?.httpOnly).toBeFalsy();
  await page.context().addCookies(state.cookies);
  await page.goto("/trips");

  await expect(
    page.getByRole("heading", { name: /下一段旅行/ }),
  ).toBeVisible();
  await expect(page.locator('main a[href="/advisor"]')).toHaveCount(1);
  await expect(page.locator('main a[href="/moments"]')).toHaveCount(1);

  await page.getByRole("main").getByRole("link", { name: /我已经有计划/ }).click();

  await expect(
    page.getByRole("heading", { name: /先把旅程定下来/ }),
  ).toBeVisible();

  await page.getByLabel("目的地").fill("成都");
  await page.getByLabel(/出发日期/).fill("2026-10-20");
  await page.getByLabel("结束日期").fill("2026-10-23");
  await page.getByLabel("预算").fill("6000");

  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole("heading", { name: /先把旅程定下来/ })).toBeVisible();
  const createPageOverflows = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
  expect(createPageOverflows).toBeFalsy();

  await page.getByRole("button", { name: "创建这段旅行" }).click();

  await expect(page).toHaveURL(/\/trips\/.+/);
  await expect(
    page.getByRole("heading", { name: "成都" }),
  ).toBeVisible();
  const csrf = state.cookies.find((cookie) => cookie.name === "travelpilot_csrf")?.value ?? "";
  await request.delete("http://localhost:8000/api/v1/account", { headers: { "X-CSRF-Token": csrf } });
});
