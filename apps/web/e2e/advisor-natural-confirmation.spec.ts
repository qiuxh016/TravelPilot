import { expect, test } from "@playwright/test";

test("自然语言明确确认保存时调用正式草案确认接口", async ({ page }) => {
  const sessionId = "natural-confirmation-session";
  const now = "2026-10-20T08:00:00Z";
  const candidateId = "candidate-1";
  const stop = {
    candidate_id: candidateId, name: "人民公园", day: "2026-10-23",
    start_time: "09:00", end_time: "10:30", latitude: 30.66, longitude: 104.05,
    category: "公园", planning_category: "ATTRACTION", address: "成都市中心",
  };
  const session = {
    id: sessionId, mode: "existing", trip_id: "trip-1", destination: "成都",
    start_date: "2026-10-23", end_date: "2026-10-23", budget: null, pace_level: 3,
    preference_text: "", candidates: [{
      id: candidateId, provider: "fake", provider_id: "place-1", name: "人民公园",
      address: "成都市中心", category: "公园", planning_category: "ATTRACTION",
      latitude: 30.66, longitude: 104.05, duration_min: 90, confidence: 1,
      verification_status: "VERIFIED", verified_at: now,
    }],
    context: { revision: 1, selected_candidate_ids: [candidateId], current_plan: null,
      confirmed_memories: [], planning_inputs: { origin: "", companions: [], hard_constraints: [] } },
    draft: {
      version: 1, context_revision: 1, generated_at: now, selected_candidate_ids: [candidateId],
      warnings: [], changes: [{ stable_key: candidateId, operation: "ADD", name: "人民公园",
        reason: "用户选择", change_cost: 1, before: null, after: stop }],
      change_summary: { add: 1, remove: 0, move: 0, keep: 0 }, pace_change: null, stops: [stop],
      transitions: [],
    },
    draft_version: 1, draft_stale: false, status: "ACTIVE", confirmed_trip_id: null,
    confirmed_plan_id: null, created_at: now, updated_at: now,
    messages: [{ id: "assistant-1", role: "assistant", content: "草案已生成，请检查。", payload: null, created_at: now }],
  };
  let confirmRequest: Record<string, unknown> | null = null;

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
    if (url.pathname === `/api/v1/advisor/sessions/${sessionId}` && request.method() === "GET") {
      return route.fulfill({ json: session });
    }
    if (url.pathname === "/api/v1/advisor/sessions" && request.method() === "GET") {
      return route.fulfill({ json: [session] });
    }
    if (url.pathname.endsWith("/progress")) return route.fulfill({ json: [] });
    if (url.pathname.endsWith("/confirm") && request.method() === "POST") {
      confirmRequest = request.postDataJSON() as Record<string, unknown>;
      return route.fulfill({ json: {
        trip_id: "trip-1", plan_id: "plan-2", plan_version: 2, already_confirmed: false,
      } });
    }
    return route.fulfill({ json: {} });
  });

  await page.goto(`/advisor?sessionId=${sessionId}`);
  await expect(page.getByRole("heading", { name: "行程调整预览" })).toBeVisible();
  await expect(page.getByText("人民公园", { exact: true }).first()).toBeVisible();
  await page.getByLabel("告诉顾问你的想法").fill("我确认，帮我直接保存");
  await page.getByRole("button", { name: "发送" }).click();
  await expect.poll(() => confirmRequest).toEqual({ draft_version: 1 });
  await expect(page).toHaveURL(/\/trips\/trip-1\/plan$/);
});
