import { expect, test } from "@playwright/test";

const api = "http://127.0.0.1:8000/api/v1";

test("内容确认、计划、重规划和记忆可在页面完成", async ({ page, request }) => {
  const created = await request.post(`${api}/trips`, {
    data: { destination: "成都", start_date: "2026-10-01", end_date: "2026-10-04", budget: 6000, pace_level: 2 },
  });
  expect(created.ok()).toBeTruthy();
  const trip = await created.json() as { id: string };
  try {
    await page.goto(`/trips/${trip.id}/import`);
    await expect(page.getByRole("heading", { name: "粘贴旅行攻略" })).toBeVisible();
    await page.getByLabel("攻略原文").fill("想去宽窄巷子和熊猫基地，安排轻松一点");
    await page.getByRole("button", { name: "提取地点" }).click();
    await expect(page.getByText("抽取结果 · NEEDS_CONFIRMATION")).toBeVisible();
    const boxes = page.getByRole("checkbox");
    expect(await boxes.count()).toBeGreaterThanOrEqual(2);
    for (let index = 0; index < await boxes.count(); index++) await boxes.nth(index).check();
    await page.getByRole("button", { name: "确认并加入 Wishlist" }).click();
    await expect(page.getByText("抽取结果 · IMPORTED")).toBeVisible();

    await page.goto(`/trips/${trip.id}/plan`);
    await page.getByRole("button", { name: "生成新版本" }).click();
    await expect(page.getByText("Plan v1 · READY")).toBeVisible();
    await page.getByRole("link", { name: "模拟事件并查看 Diff" }).click();
    await page.getByLabel("生效时间").fill("2026-10-01T09:00");
    await page.getByRole("button", { name: "触发重规划" }).click();
    await expect(page.getByRole("heading", { name: /Plan v2/ })).toBeVisible();
    await expect(page.getByText(/REMOVE · 成本/)).toBeVisible();

    await page.goto(`/trips/${trip.id}/memory`);
    await page.getByLabel("反馈").fill("今天很累，希望下午少安排景点");
    await page.getByRole("button", { name: "提交反馈" }).click();
    await page.getByRole("button", { name: "确认记忆" }).click();
    await expect(page.getByText("SEMANTIC_PREFERENCE · 已确认")).toBeVisible();
    if (process.env.TRAVELPILOT_SCREENSHOT_PATH) {
      await page.screenshot({ path: process.env.TRAVELPILOT_SCREENSHOT_PATH });
    }
  } finally {
    await request.delete(`${api}/trips/${trip.id}`);
  }
});
