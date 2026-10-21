import { expect, test } from "@playwright/test";
import { tmpdir } from "node:os";
import { join } from "node:path";

test("顾问展示已验证的冲突方案并将选择同步到新草案", async ({ page }) => {
  const sessionId = "negotiation-session";
  const day = "2026-11-03";
  const now = "2026-10-22T08:00:00Z";
  const firstId = "museum-1";
  const secondId = "street-1";
  const firstStop = {
    candidate_id: firstId, name: "梅州学宫", day, start_time: "10:00:00", end_time: "12:00:00",
    latitude: 24.29, longitude: 116.12, category: "博物馆", planning_category: "ATTRACTION", address: "梅州",
  };
  const secondStop = {
    candidate_id: secondId, name: "江北老街", day, start_time: "11:00:00", end_time: "12:00:00",
    latitude: 24.30, longitude: 116.11, category: "街区", planning_category: "ATTRACTION", address: "梅州",
  };
  const candidates = [
    { id: firstId, name: firstStop.name, provider: "fake", provider_id: firstId, address: "梅州", category: "博物馆", planning_category: "ATTRACTION", latitude: firstStop.latitude, longitude: firstStop.longitude, duration_min: 120, confidence: 1, verification_status: "SIMULATED" },
    { id: secondId, name: secondStop.name, provider: "fake", provider_id: secondId, address: "梅州", category: "街区", planning_category: "ATTRACTION", latitude: secondStop.latitude, longitude: secondStop.longitude, duration_min: 60, confidence: 1, verification_status: "SIMULATED" },
  ];
  const session = {
    id: sessionId, mode: "destination", trip_id: null, destination: "梅州", start_date: day, end_date: day,
    budget: null, pace_level: 3, preference_text: "", candidates,
    context: { revision: 1, selected_candidate_ids: [firstId, secondId], current_plan: null, confirmed_memories: [], planning_inputs: { origin: "", companions: [], hard_constraints: [] } },
    draft: { version: 1, context_revision: 1, generated_at: now, selected_candidate_ids: [firstId, secondId], warnings: ["阻断：时间重叠"], changes: [], change_summary: { add: 0, remove: 0, move: 0, keep: 2 }, pace_change: null, stops: [firstStop, secondStop], transitions: [] },
    draft_version: 1, draft_stale: false, status: "ACTIVE", confirmed_trip_id: null, confirmed_plan_id: null,
    created_at: now, updated_at: now, messages: [],
  };
  const draftRequests: Record<string, unknown>[] = [];

  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname === "/api/v1/auth/me") return route.fulfill({ json: {
      id: "user-1", email: "test@example.invalid", username: "tester", created_at: now,
      display_name: "测试用户", home_city: "", bio: "", has_avatar: false,
    } });
    if (url.pathname === "/api/v1/trips") return route.fulfill({ json: [] });
    if (url.pathname === "/api/v1/advisor/status") return route.fulfill({ json: {
      llm_status: "CONFIGURED", model_name: "test-model", output_token_limit: 1200,
      upstream_status: "CHECKED_PER_REQUEST",
    } });
    if (url.pathname === `/api/v1/advisor/sessions/${sessionId}` && request.method() === "GET") return route.fulfill({ json: session });
    if (url.pathname === "/api/v1/advisor/sessions" && request.method() === "GET") return route.fulfill({ json: [session] });
    if (url.pathname.endsWith("/negotiate") && request.method() === "POST") return route.fulfill({ json: {
      draft_version: 1, status: "OPTIONS", conflicts: ["梅州学宫 与 江北老街 时间重叠"], alternatives: [
        { id: "move-street", title: "调整时间或日期", summary: "将江北老街调整到 2026-11-03 12:30，保留其他安排", change_cost: 2,
          changes: [{ candidate_id: secondId, operation: "MOVE", day, start_time: "12:30:00" }] },
      ],
    } });
    if (url.pathname.endsWith("/draft") && request.method() === "POST") {
      const draftRequest = request.postDataJSON() as Record<string, unknown>;
      draftRequests.push(draftRequest);
      const schedules = draftRequest.schedules as { candidate_id: string; day: string; start_time: string }[];
      const moved = schedules.find((item) => item.candidate_id === secondId)!;
      return route.fulfill({ json: {
        ...session, draft_version: 2, context: { ...session.context, revision: 2, selected_candidate_ids: [firstId, secondId] },
        draft: { ...session.draft, version: 2, context_revision: 2, selected_candidate_ids: [firstId, secondId],
        warnings: [],
          stops: [firstStop, { ...secondStop, start_time: `${moved.start_time}:00`, end_time: "13:30:00" }] },
      } });
    }
    if (url.pathname.endsWith("/progress")) return route.fulfill({ json: [] });
    return route.fulfill({ json: {} });
  });

  await page.goto(`/advisor?sessionId=${sessionId}`);
  await expect(page.getByRole("heading", { name: "行程调整预览" })).toBeVisible();
  await page.getByRole("button", { name: "检查并协商" }).click();
  await expect(page.getByText("梅州学宫 与 江北老街 时间重叠")).toBeVisible();
  if (process.env.TRAVELPILOT_QA_SCREENSHOT) {
    await page.locator(".advisor-negotiation-panel").screenshot({ path: join(tmpdir(), "travelpilot-advisor-negotiation-options.png") });
  }
  await page.getByRole("button", { name: "采用并重新核验" }).click();
  await expect.poll(() => draftRequests.length).toBe(1);
  expect(draftRequests[0].candidate_ids).toEqual([firstId, secondId]);
  expect(draftRequests[0].schedules).toEqual([
    { candidate_id: firstId, day, start_time: "10:00" },
    { candidate_id: secondId, day, start_time: "12:30" },
  ]);
  await expect(page.getByText("江北老街", { exact: true }).last()).toBeVisible();
  await expect(page.getByText("已按你选的冲突调整方案重新核验")).toBeVisible();
  await expect(page.locator(".advisor-confirm-bar")).not.toContainText("预览已过期");
  if (process.env.TRAVELPILOT_QA_SCREENSHOT) {
    await page.locator(".advisor-stop-list").screenshot({ path: join(tmpdir(), "travelpilot-advisor-negotiation-after.png") });
  }
});
