import { expect, test } from "@playwright/test";

test("途中事件在顾问弹窗内预览，放弃不写正式计划，确认后更新并刷新上下文", async ({ page }) => {
  const now = "2026-10-20T08:00:00Z";
  const tripId = "what-if-trip";
  const sessionId = "what-if-session";
  const day1 = "2026-10-23";
  const originalPlans = [{ id: "plan-1", trip_id: tripId, version: 1, status: "CONFIRMED", warnings: [], created_at: now, base_version: null, created_by_user_id: null, created_by_display_name: null, change_reason: null, nodes: [] }];
  let plans: Array<{ id: string; trip_id: string; version: number; status: string; warnings: string[]; created_at: string; base_version: number | null; created_by_user_id: string | null; created_by_display_name: string | null; change_reason: string | null; nodes: unknown[] }> = originalPlans;
  let confirmCalls = 0;
  let replanCalls = 0;
  const requests: Array<{ path: string; body: unknown }> = [];
  const session = {
    id: sessionId, mode: "existing", trip_id: tripId, destination: "厦门", start_date: day1, end_date: "2026-10-24",
    budget: null, pace_level: 3, preference_text: "", candidates: [],
    context: { revision: 0, selected_candidate_ids: [], current_plan: null, confirmed_memories: [], planning_inputs: { origin: "", companions: [], hard_constraints: [] } },
    draft: null, draft_version: 0, draft_stale: false, status: "ACTIVE", confirmed_trip_id: null, confirmed_plan_id: null,
    created_at: now, updated_at: now,
    messages: [{ id: "assistant-handoff", role: "assistant", content: "可以先试算途中调整。", payload: {
      event_replan_handoff: { event_type: "WEATHER_CHANGE", action_label: "设置天气并查看途中调整预览", prefill: { condition: "RAIN" } },
    }, created_at: now }],
  };
  const preview = {
    run_id: "run-1", event_id: "event-1", based_on_plan_id: "plan-1", based_on_plan_version: 1,
    proposed_version: 2, status: "PREVIEWED", proposed_plan_status: "READY", warnings: [],
    nodes: [
      { id: "node-1", poi_id: "poi-1", name: "室内博物馆", day: day1, start_time: "10:00:00", end_time: "11:30:00", priority: 1, status: "PLANNED", note: null },
      { id: "node-2", poi_id: "poi-2", name: "海边公园", day: "2026-10-24", start_time: "09:00:00", end_time: "10:00:00", priority: 2, status: "PLANNED", note: null },
    ],
    diff: [{ stable_key: "poi-1", operation: "MOVE", reason_code: "WEATHER_CHANGE", before: { name: "室内博物馆", start_time: "09:00" }, after: { name: "室内博物馆", start_time: "10:00" }, change_cost: 1 }],
    created_at: now, expires_at: "2026-10-20T09:00:00Z",
  };

  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const body = request.method() === "POST" ? request.postDataJSON() : null;
    requests.push({ path: url.pathname, body });
    if (url.pathname === "/api/v1/auth/me") return route.fulfill({ json: { id: "user-1", email: "test@example.invalid", username: "tester", created_at: now, display_name: "测试用户", home_city: "", bio: "", has_avatar: false } });
    if (url.pathname === "/api/v1/trips") return route.fulfill({ json: [{ id: tripId, destination: "厦门", start_date: day1, end_date: "2026-10-24", budget: null, pace_level: 3, status: "ACTIVE", created_at: now, updated_at: now, has_plan: true, access_role: "OWNER", lifecycle_status: "UPCOMING" }] });
    if (url.pathname === "/api/v1/advisor/status") return route.fulfill({ json: { llm_status: "CONFIGURED", model_name: "mock", output_token_limit: 512, upstream_status: "CHECKED_PER_REQUEST" } });
    if (url.pathname === `/api/v1/advisor/sessions/${sessionId}`) return route.fulfill({ json: session });
    if (url.pathname === "/api/v1/advisor/sessions") return route.fulfill({ json: [session] });
    if (url.pathname.endsWith("/progress")) return route.fulfill({ json: [] });
    if (url.pathname === `/api/v1/trips/${tripId}`) return route.fulfill({ json: { id: tripId, destination: "厦门", start_date: day1, end_date: "2026-10-24", budget: null, pace_level: 3, status: "ACTIVE", created_at: now, updated_at: now, has_plan: true, access_role: "OWNER", lifecycle_status: "UPCOMING" } });
    if (url.pathname === `/api/v1/trips/${tripId}/plans`) return route.fulfill({ json: plans });
    if (url.pathname === `/api/v1/trips/${tripId}/wishlist`) return route.fulfill({ json: [] });
    if (url.pathname === `/api/v1/trips/${tripId}/events` && request.method() === "POST") return route.fulfill({ json: { id: "event-1", trip_id: tripId, event_type: "WEATHER_CHANGE", effective_at: `${day1}T09:00:00+08:00`, effective_timezone_offset_minutes: 480, payload: body?.payload ?? {}, created_at: now } });
    if (url.pathname === `/api/v1/trips/${tripId}/replan-runs` && request.method() === "POST") { replanCalls += 1; return route.fulfill({ json: preview }); }
    if (url.pathname.endsWith("/replan-runs/run-1/confirm")) {
      confirmCalls += 1;
      plans = [{ ...originalPlans[0], id: "plan-2", version: 2, base_version: 1, change_reason: "WEATHER_CHANGE", nodes: preview.nodes }];
      return route.fulfill({ json: { run_id: "run-1", plan: plans[0], diff: preview.diff } });
    }
    return route.fulfill({ json: [] });
  });

  await page.goto(`/advisor?sessionId=${sessionId}`);
  await expect(page.getByRole("region", { name: "顾问对话" })).toBeVisible();
  const handoff = page.locator(".advisor-event-replan-handoff");
  await handoff.getByRole("button", { name: /设置天气并查看途中调整预览/ }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  await expect(dialog.getByLabel("发生了什么？")).toHaveValue("WEATHER_CHANGE");
  await expect(dialog.getByLabel("天气情况")).toHaveValue("RAIN");
  await dialog.getByLabel("生效时间").fill(`${day1}T09:00`);
  await dialog.getByRole("button", { name: "生成预览" }).click();
  await expect(dialog.getByText("基于正式计划 v1 · 预览 v2")).toBeVisible();
  await expect(dialog.getByRole("heading", { name: day1 })).toBeVisible();
  await expect(dialog.getByRole("heading", { name: "2026-10-24" })).toBeVisible();
  expect(replanCalls).toBe(1);
  expect(confirmCalls).toBe(0);
  expect(plans).toHaveLength(1);
  expect(requests.find((item) => item.path.endsWith("/events"))?.body).toMatchObject({ effective_at: `${day1}T09:00:00+08:00`, payload: { condition: "RAIN" } });
  if (process.env.TRAVELPILOT_UI_EVIDENCE_PATH) {
    await page.screenshot({ path: process.env.TRAVELPILOT_UI_EVIDENCE_PATH, fullPage: false });
  }

  await dialog.getByRole("button", { name: "放弃预览" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  expect(confirmCalls).toBe(0);
  expect(plans).toHaveLength(1);

  await handoff.getByRole("button", { name: /设置天气并查看途中调整预览/ }).click();
  const reopened = page.getByRole("dialog");
  await reopened.getByLabel("生效时间").fill(`${day1}T09:00`);
  await reopened.getByRole("button", { name: "生成预览" }).click();
  await reopened.getByRole("button", { name: "确认并更新正式行程" }).click();
  await expect(reopened.getByText("调整已保存", { exact: true })).toBeVisible();
  await expect(page.getByText("途中调整已写入正式行程，并已刷新顾问上下文。")).toBeVisible();
  expect(confirmCalls).toBe(1);
  expect(plans).toHaveLength(1);
  expect(plans[0].version).toBe(2);
});
