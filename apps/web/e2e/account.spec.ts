import { expect, test } from "@playwright/test";

test("账户隐私页支持管理偏好、导出和删除账户", async ({ page, request }, testInfo) => {
  test.setTimeout(60_000);
  const email = `account-${Date.now()}@example.invalid`;
  const password = "travelpilot-test-2026";
  const registered = await request.post("http://localhost:8000/api/v1/auth/register", {
    data: { email, password },
  });
  expect(registered.ok()).toBeTruthy();
  const mailbox = await request.get(`http://localhost:8000/api/v1/auth/dev-mailbox/${email}`);
  const token = new URL((await mailbox.json()).link).searchParams.get("token")!;
  expect((await request.post("http://localhost:8000/api/v1/auth/verify-email", { data: { token } })).ok()).toBeTruthy();
  const login = await request.post("http://localhost:8000/api/v1/auth/login", { data: { email, password } });
  expect(login.ok()).toBeTruthy();
  const cookies = (await request.storageState()).cookies;
  const csrf = cookies.find((cookie) => cookie.name === "travelpilot_csrf")?.value ?? "";
  await page.context().addCookies(cookies);

  let accountDeleted = false;
  try {
    await page.goto("/account");
    await expect(page.getByRole("heading", { name: "账户与隐私" })).toBeVisible();
    await expect(page.getByText("这里还没有偏好记忆")).toBeVisible();

    const memoryToggle = page.getByLabel("在后续规划中使用我的偏好");
    await expect(memoryToggle).toBeEnabled();
    if (await memoryToggle.isChecked()) {
      await memoryToggle.click();
      await expect(page.getByText("记忆已关闭", { exact: true })).toBeVisible();
    } else {
      await memoryToggle.click();
    }
    await expect(memoryToggle).toBeChecked();
    await expect(page.getByText("记忆已开启", { exact: true })).toBeVisible();
    await page.getByLabel("这条内容属于").selectOption("HARD_LIMIT");
    await page.getByLabel("你的说明").fill("每天最多安排三个地点");
    await page.getByRole("button", { name: "确认并保存" }).click();
    await expect(page.getByText("每天最多安排三个地点")).toBeVisible();

    await page.getByRole("button", { name: "编辑" }).click();
    await page.getByLabel("编辑偏好记忆").fill("每天最多安排两个地点");
    await page.getByRole("button", { name: "保存", exact: true }).click();
    await expect(page.getByText("每天最多安排两个地点")).toBeVisible();
    await page.getByRole("button", { name: "删除", exact: true }).click();
    await expect(page.getByText("这里还没有偏好记忆")).toBeVisible();

    const downloadPromise = page.waitForEvent("download");
    await page.getByRole("button", { name: "下载账户数据" }).click();
    const download = await downloadPromise;
    expect(download.suggestedFilename()).toBe("travelpilot-account-export.json");

    await page.screenshot({ path: testInfo.outputPath("account-desktop.png"), fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    await expect(page.getByRole("heading", { name: "账户与隐私" })).toBeVisible();
    const hasHorizontalOverflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
    expect(hasHorizontalOverflow).toBeFalsy();
    await page.screenshot({ path: testInfo.outputPath("account-mobile.png"), fullPage: true });

    page.once("dialog", (dialog) => void dialog.accept());
    await page.getByRole("button", { name: "永久删除" }).click();
    await expect(page).toHaveURL(/\/login$/);
    accountDeleted = true;
  } finally {
    if (!accountDeleted) {
      const cleanup = await request.delete("http://localhost:8000/api/v1/account", {
        headers: { "X-CSRF-Token": decodeURIComponent(csrf) },
        timeout: 5000,
      }).catch(() => null);
      if (!cleanup || cleanup.status() !== 204) throw new Error(`测试账户清理失败：${cleanup?.status() ?? "请求失败"}`);
    }
  }
});
