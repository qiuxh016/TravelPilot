import { expect, test } from "@playwright/test";

test("计划页收到共享版本更新后自动刷新", async ({ page }, testInfo) => {
  const tripId = "live-sync-trip";
  let planReads = 0;
  const plan = (version: number) => ({
    id: `plan-v${version}`, trip_id: tripId, version, status: "DRAFT", warnings: [],
    created_at: "2026-10-20T08:00:00Z", base_version: version - 1,
    created_by_user_id: "member", created_by_display_name: "旅行伙伴",
    change_reason: null,
    lodging: [{ poi_id: "hotel-1", name: "确认入住的客家酒店", address: "梅州市梅江区", verification_status: "SIMULATED" }],
    nodes: [{ id: "stop-1", poi_id: "poi-1", name: "客家博物馆", day: "2026-10-22", start_time: version === 1 ? "09:00" : "10:00", end_time: version === 1 ? "10:00" : "11:00", priority: 3, status: "PLANNED", note: null }],
  });

  await page.route("**/api/v1/**", (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/v1/auth/me") return route.fulfill({ json: {
      id: "member", email: "member@example.invalid", username: "member",
      created_at: "2026-10-20T08:00:00Z", display_name: "旅行伙伴", home_city: "", bio: "", has_avatar: false,
    } });
    if (path === `/api/v1/trips/${tripId}`) return route.fulfill({ json: {
      id: tripId, destination: "梅州", start_date: "2026-10-22", end_date: "2026-10-22",
      budget: null, pace_level: 3, status: "ACTIVE", created_at: "2026-10-20T08:00:00Z",
      updated_at: "2026-10-20T08:00:00Z", has_plan: true, access_role: "EDITOR",
    } });
    if (path === `/api/v1/trips/${tripId}/plans`) {
      planReads += 1;
      return route.fulfill({ json: [plan(planReads <= 2 ? 1 : 2)] });
    }
    if (path === `/api/v1/trips/${tripId}/wishlist`) return route.fulfill({ json: [
      { id: "wish-1", trip_id: tripId, poi_id: "poi-1", name: "客家博物馆", category: "ATTRACTION", latitude: 24.29, longitude: 116.12, priority: 3, suggested_duration_min: 60, notes: null },
    ] });
    if (path === `/api/v1/trips/${tripId}/weather`) return route.fulfill({ status: 404, json: { detail: "天气不可用" } });
    return route.fulfill({ status: 404, json: { detail: "Not Found" } });
  });

  await page.addInitScript(({ id }) => {
    class MockEventSource extends EventTarget {
      onopen: ((this: EventSource, event: Event) => unknown) | null = null;
      onerror: ((this: EventSource, event: Event) => unknown) | null = null;
      closed = false;
      constructor() {
        super();
        setTimeout(() => {
          if (this.closed) return;
          this.onopen?.call(this as unknown as EventSource, new Event("open"));
          this.dispatchEvent(new MessageEvent("snapshot", { data: JSON.stringify({ trip_id: id, plan_version: 1 }) }));
          this.dispatchEvent(new MessageEvent("plan_updated", { data: JSON.stringify({ trip_id: id, plan_version: 2, updated_by: "旅行伙伴" }) }));
        }, 50);
      }
      close() { this.closed = true; }
    }
    window.EventSource = MockEventSource as unknown as typeof EventSource;
  }, { id: tripId });

  await page.goto(`/trips/${tripId}/plan`);
  await expect(page.getByText("计划 v2")).toBeVisible();
  await expect(page.getByRole("heading", { name: "住宿安排" })).toBeVisible();
  await expect(page.locator(".plan-lodging")).toContainText("确认入住的客家酒店");
  await expect(page.locator(".plan-stop-time")).toContainText("10:00");
  await expect(page.getByRole("status").filter({ hasText: "旅行伙伴更新了计划，已同步到 v2" })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("lodging-plan-sync.png"), fullPage: true });
});
