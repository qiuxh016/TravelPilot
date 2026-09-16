import { expect, test } from "@playwright/test";

test("用户可以创建旅行并进入详情页", async ({ page }) => {
  await page.goto("/trips");

  await expect(
    page.getByRole("heading", { name: "我的旅行" }),
  ).toBeVisible();

  await page.getByRole("link", { name: "创建旅行" }).click();

  await expect(
    page.getByRole("heading", { name: "创建旅行" }),
  ).toBeVisible();

  await page.getByLabel("目的地").fill("成都");
  await page.getByLabel("开始日期").fill("2026-10-01");
  await page.getByLabel("结束日期").fill("2026-10-04");
  await page.getByLabel("预算").fill("6000");

  await page.getByRole("button", { name: "创建旅行" }).click();

  await expect(page).toHaveURL(/\/trips\/.+/);
  await expect(
    page.getByRole("heading", { name: "成都" }),
  ).toBeVisible();
});