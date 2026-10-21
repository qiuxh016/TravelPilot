import { expect, test } from "@playwright/test";

test("住宿轮次提出局部时间建议时保留已确认的午餐和它的时间", async ({ page }) => {
  page.on("pageerror", (error) => console.error("advisor test page error:", error.stack || error.message));
  const now = "2026-10-21T08:00:00Z";
  const day = "2026-11-12";
  const sessionId = "cross-category-session";
  const tripId = "cross-category-trip";
  const candidates = [
    { id: "lunch-1", provider: "fake", provider_id: "restaurant-1", name: "已确认午餐店",
      address: "成都市青羊区", category: "川菜餐厅", planning_category: "FOOD", meal_type: "LUNCH",
      latitude: 30.66, longitude: 104.05, duration_min: 60, confidence: 1, verification_status: "SIMULATED" },
    { id: "hotel-1", provider: "fake", provider_id: "hotel-1", name: "住宿候选酒店",
      address: "成都市锦江区", category: "住宿", planning_category: "LODGING",
      latitude: 30.65, longitude: 104.07, duration_min: 60, confidence: 1, verification_status: "SIMULATED" },
    { id: "park-1", provider: "fake", provider_id: "park-1", name: "当天游览地点",
      address: "成都市青羊区", category: "公园", planning_category: "ATTRACTION",
      latitude: 30.67, longitude: 104.04, duration_min: 90, confidence: 1, verification_status: "SIMULATED" },
  ];
  const initial = {
    id: sessionId, mode: "existing", trip_id: tripId, destination: "成都",
    start_date: day, end_date: day, budget: null, pace_level: 3, preference_text: "",
    candidates,
    context: { revision: 1, selected_candidate_ids: ["lunch-1", "hotel-1"], current_plan: { id: "plan-1", version: 4, stops: [], warnings: [] },
      confirmed_memories: [], planning_inputs: { origin: "成都东站", companions: [], hard_constraints: [] } },
    draft: { version: 1, context_revision: 1, generated_at: now, selected_candidate_ids: ["lunch-1", "hotel-1"],
      stops: [{ candidate_id: "lunch-1", name: "已确认午餐店", day, start_time: "12:00", end_time: "13:00",
        latitude: 30.66, longitude: 104.05, category: "川菜餐厅", planning_category: "FOOD", meal_type: "LUNCH", address: "成都市青羊区" }],
      lodging: [{ candidate_id: "hotel-1", name: "住宿候选酒店", address: "成都市锦江区", provider: "fake", verification_status: "SIMULATED" }],
      warnings: [], attention_items: [{ kind: "MEAL_MISSING", day, meal_type: "DINNER" }],
      changes: [], change_summary: { add: 1, remove: 0, move: 0, keep: 0 }, pace_change: null },
    draft_version: 1, draft_stale: false, status: "ACTIVE", confirmed_trip_id: null, confirmed_plan_id: null,
    created_at: now, updated_at: now,
    messages: [{ id: "assistant-1", role: "assistant", content: "午餐已加入当前计划。", payload: { trip_action_items: [] }, created_at: now }],
  };
  let session: Record<string, unknown> = initial;
  let postedDraft: { candidate_ids: string[]; schedules: { candidate_id: string; day: string; start_time: string }[] } | null = null;

  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname === "/api/v1/auth/me") return route.fulfill({ json: {
      id: "user-1", email: "test@example.invalid", username: "tester", created_at: now,
      display_name: "测试用户", home_city: "", bio: "", has_avatar: false,
    } });
    if (url.pathname === "/api/v1/trips") return route.fulfill({ json: [] });
    if (url.pathname === "/api/v1/advisor/status") return route.fulfill({ json: {
      llm_status: "CONFIGURED", model_name: "test-model", output_token_limit: 1200, upstream_status: "CHECKED_PER_REQUEST",
    } });
    if (url.pathname === `/api/v1/advisor/sessions/${sessionId}` && request.method() === "GET") {
      return route.fulfill({ json: session });
    }
    if (url.pathname === "/api/v1/advisor/sessions" && request.method() === "GET") return route.fulfill({ json: [session] });
    if (url.pathname.endsWith("/progress") || url.pathname.endsWith("/events")) return route.fulfill({ json: [] });
    if (url.pathname.endsWith("/messages") && request.method() === "POST") {
      const body = request.postDataJSON() as { content: string; selected_candidate_ids: string[] };
      expect(body.selected_candidate_ids).toContain("lunch-1");
      session = {
        ...initial,
        context: { ...initial.context, revision: 2, selected_candidate_ids: ["lunch-1", "hotel-1", "park-1"] },
        messages: [...initial.messages, { id: "user-2", role: "user", content: body.content, payload: null, created_at: now }, {
          id: "assistant-2", role: "assistant", content: "住宿建议已整理，也更新了当天游览时间。",
          payload: { action: "SCHEDULE_PROPOSAL", candidate_ids: ["park-1"],
            schedule_suggestions: [{ candidate_id: "park-1", day, start_time: "14:00" }], trip_action_items: [] }, created_at: now,
        }],
      };
      return route.fulfill({ json: session });
    }
    if (url.pathname.endsWith("/draft") && request.method() === "POST") {
      postedDraft = request.postDataJSON() as typeof postedDraft;
      session = {
        ...session,
        context: { ...initial.context, revision: 3, selected_candidate_ids: ["park-1", "lunch-1", "hotel-1"] },
        draft_version: 2,
        draft: { ...initial.draft, version: 2, context_revision: 3,
          selected_candidate_ids: ["park-1", "lunch-1", "hotel-1"],
          stops: [
            { candidate_id: "park-1", name: "当天游览地点", day, start_time: "14:00", end_time: "15:30",
              latitude: 30.67, longitude: 104.04, category: "公园", planning_category: "ATTRACTION", address: "成都市青羊区" },
            initial.draft.stops[0],
          ],
        },
      };
      return route.fulfill({ json: session });
    }
    return route.fulfill({ json: {} });
  });

  await page.goto(`/advisor?sessionId=${sessionId}`);
  await expect(page.locator(".advisor-itinerary")).toContainText("已确认午餐店");
  await page.locator("#advisor-message").fill("继续帮我推荐住宿，并安排当天的游览");
  await page.getByRole("button", { name: "发送" }).click();

  await expect.poll(() => postedDraft).not.toBeNull();
  const capturedDraft = postedDraft as { candidate_ids: string[]; schedules: { candidate_id: string; day: string; start_time: string }[] } | null;
  expect(capturedDraft?.candidate_ids).toEqual(["park-1", "lunch-1", "hotel-1"]);
  expect(capturedDraft?.schedules).toEqual([
    { candidate_id: "park-1", day, start_time: "14:00" },
    { candidate_id: "lunch-1", day, start_time: "12:00" },
  ]);
  await expect(page.locator(".advisor-itinerary")).toContainText("已确认午餐店");
  await expect(page.locator(".advisor-itinerary")).toContainText("住宿候选酒店");
});
