import { expect, test } from "@playwright/test";

test("旅行列表按日期筛选，并允许稍后处理旅后提醒", async ({ page }) => {
  const trips = [
    { id: "future-trip", destination: "即将出发城市", start_date: "2026-10-27", end_date: "2026-10-28", budget: null, pace_level: 3, status: "DRAFT", created_at: "2026-10-01T00:00:00Z", updated_at: "2026-10-01T00:00:00Z", has_plan: false, access_role: "OWNER", lifecycle_status: "UPCOMING" },
    { id: "active-trip", destination: "正在旅行城市", start_date: "2026-10-19", end_date: "2026-10-21", budget: null, pace_level: 3, status: "ACTIVE", created_at: "2026-10-01T00:00:00Z", updated_at: "2026-10-01T00:00:00Z", has_plan: true, access_role: "OWNER", lifecycle_status: "ONGOING" },
    { id: "past-trip", destination: "已经完成城市", start_date: "2026-10-15", end_date: "2026-10-16", budget: null, pace_level: 3, status: "ACTIVE", created_at: "2026-10-01T00:00:00Z", updated_at: "2026-10-01T00:00:00Z", has_plan: true, access_role: "OWNER", lifecycle_status: "COMPLETED" },
  ];
  let reminderAction = "";
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/v1/auth/me") return route.fulfill({ json: { id: "user", email: "test@example.invalid", username: "tester", created_at: "2026-10-01T00:00:00Z", display_name: "测试用户", home_city: "", bio: "", timezone: "Asia/Shanghai", trip_reminders_enabled: true, has_avatar: false } });
    if (path === "/api/v1/trips") return route.fulfill({ json: trips });
    if (path === "/api/v1/trips/lifecycle/prompts") return route.fulfill({ json: [{ id: "prompt-1", trip_id: "past-trip", kind: "AFTER_TRIP", destination: "已经完成城市", start_date: "2026-10-15", end_date: "2026-10-16", scheduled_for: "2026-10-17", status: "PENDING" }] });
    if (path === "/api/v1/trips/lifecycle/prompts/prompt-1" && route.request().method() === "POST") {
      reminderAction = (route.request().postDataJSON() as { action: string }).action;
      return route.fulfill({ json: { id: "prompt-1", trip_id: "past-trip", kind: "AFTER_TRIP", destination: "已经完成城市", start_date: "2026-10-15", end_date: "2026-10-16", scheduled_for: "2026-10-17", status: reminderAction === "LATER" ? "SNOOZED" : "DONE" } });
    }
    if (path.includes("/wishlist") || path.endsWith("/plans")) return route.fulfill({ json: [] });
    return route.fulfill({ status: 404, json: { detail: "Not Found" } });
  });

  await page.goto("/trips");
  await expect(page.getByRole("heading", { name: /下一段旅程/ })).toBeVisible();
  await expect(page.getByText("已经完成城市 · 旅程刚刚结束")).toBeVisible();
  await page.getByRole("button", { name: /已完成 1/ }).click();
  await expect(page.getByText("已经完成城市", { exact: true })).toBeVisible();
  await expect(page.getByText("即将出发城市", { exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "明天提醒" }).click();
  await expect(page.getByRole("status")).toContainText("明天再提醒你");
  expect(reminderAction).toBe("LATER");
});
