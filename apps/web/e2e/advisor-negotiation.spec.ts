import { expect, test } from "@playwright/test";
import { tmpdir } from "node:os";
import { join } from "node:path";

test("顾问展示已验证的冲突方案并将选择同步到新草案", async ({ page }) => {
  const sessionId = "negotiation-session";
  const day = "2026-11-03";
  const now = "2026-10-22T08:00:00Z";
  const firstId = "museum-1";
  const secondId = "street-1";
  const lunchId = "lunch-1";
  const lodgingId = "lodging-1";
  const firstStop = {
    candidate_id: firstId, name: "梅州学宫", day, start_time: "10:00:00", end_time: "12:00:00",
    latitude: 24.29, longitude: 116.12, category: "博物馆", planning_category: "ATTRACTION", address: "梅州",
  };
  const secondStop = {
    candidate_id: secondId, name: "江北老街", day, start_time: "11:00:00", end_time: "12:00:00",
    latitude: 24.30, longitude: 116.11, category: "街区", planning_category: "ATTRACTION", address: "梅州",
  };
  const lunchStop = {
    candidate_id: lunchId, name: "鑫丰特色餐饮", day, start_time: "12:15:00", end_time: "13:00:00",
    latitude: 24.31, longitude: 116.10, category: "餐饮", planning_category: "FOOD", meal_type: "LUNCH", address: "梅州",
  };
  const candidates = [
    { id: firstId, name: firstStop.name, provider: "fake", provider_id: firstId, address: "梅州", category: "博物馆", planning_category: "ATTRACTION", latitude: firstStop.latitude, longitude: firstStop.longitude, duration_min: 120, confidence: 1, verification_status: "SIMULATED" },
    { id: secondId, name: secondStop.name, provider: "fake", provider_id: secondId, address: "梅州", category: "街区", planning_category: "ATTRACTION", latitude: secondStop.latitude, longitude: secondStop.longitude, duration_min: 60, confidence: 1, verification_status: "SIMULATED" },
    { id: lunchId, name: lunchStop.name, provider: "fake", provider_id: lunchId, address: "梅州", category: "餐饮", planning_category: "FOOD", latitude: lunchStop.latitude, longitude: lunchStop.longitude, duration_min: 45, confidence: 1, verification_status: "SIMULATED", meal_type: "LUNCH" },
    { id: lodgingId, name: "梅州客家酒店", provider: "fake", provider_id: lodgingId, address: "梅州", category: "住宿", planning_category: "LODGING", latitude: 24.32, longitude: 116.09, duration_min: 60, confidence: 1, verification_status: "SIMULATED" },
  ];
  const session = {
    id: sessionId, mode: "destination", trip_id: null, destination: "梅州", start_date: day, end_date: day,
    budget: null, pace_level: 3, preference_text: "", candidates,
    context: { revision: 1, selected_candidate_ids: [firstId, secondId, lunchId, lodgingId], current_plan: null, confirmed_memories: [], planning_inputs: { origin: "", companions: [], hard_constraints: [] } },
    draft: { version: 1, context_revision: 1, generated_at: now, selected_candidate_ids: [firstId, secondId, lunchId, lodgingId], warnings: ["阻断：时间重叠"], changes: [], change_summary: { add: 0, remove: 0, move: 0, keep: 4 }, pace_change: null, lodging: [{ candidate_id: lodgingId, name: "梅州客家酒店", address: "梅州", provider: "fake", verification_status: "SIMULATED" }], stops: [firstStop, secondStop, lunchStop], transitions: [] },
    draft_version: 1, draft_stale: false, status: "ACTIVE", confirmed_trip_id: null, confirmed_plan_id: null,
    created_at: now, updated_at: now, messages: [],
  };
  const draftRequests: Record<string, unknown>[] = [];
  let latestSession = session;
  let negotiationCount = 0;
  let confirmationCount = 0;

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
    if (url.pathname === `/api/v1/advisor/sessions/${sessionId}` && request.method() === "GET") return route.fulfill({ json: latestSession });
    if (url.pathname === "/api/v1/advisor/sessions" && request.method() === "GET") return route.fulfill({ json: [latestSession] });
    if (url.pathname.endsWith("/negotiate") && request.method() === "POST") {
      negotiationCount += 1;
      if (negotiationCount === 2) {
        latestSession = {
          ...latestSession,
          draft_version: 3,
          context: { ...latestSession.context, revision: 3 },
          draft: { ...latestSession.draft, version: 3, context_revision: 3,
            stops: [firstStop, { ...secondStop, start_time: "14:00:00" }, lunchStop] },
        };
      }
      return route.fulfill({ json: {
      draft_version: negotiationCount === 1 ? 1 : 2, status: "OPTIONS", conflicts: ["梅州学宫 与 江北老街 时间重叠"], notes: [], alternatives: [
        { id: "move-street", title: "调整时间或日期", summary: "将江北老街调整到 2026-11-03 12:30，保留其他安排", change_cost: 2,
          changes: [{ candidate_id: secondId, operation: "MOVE", day, start_time: "12:30:00" }] },
      ],
      } });
    }
    if (url.pathname.endsWith("/draft") && request.method() === "POST") {
      const draftRequest = request.postDataJSON() as Record<string, unknown>;
      draftRequests.push(draftRequest);
      if (draftRequest.expected_draft_version !== latestSession.draft?.version) {
        return route.fulfill({ status: 409, json: { detail: "行程草案已被更新，请刷新当前安排后再试" } });
      }
      const schedules = draftRequest.schedules as { candidate_id: string; day: string; start_time: string }[];
      const moved = schedules.find((item) => item.candidate_id === secondId)!;
      const lunch = schedules.find((item) => item.candidate_id === lunchId)!;
      latestSession = {
        ...latestSession, draft_version: 2, context: { ...latestSession.context, revision: 2, selected_candidate_ids: [firstId, secondId, lunchId, lodgingId] },
        draft: { ...latestSession.draft, version: 2, context_revision: 2, selected_candidate_ids: [firstId, secondId, lunchId, lodgingId],
        warnings: [],
          stops: [firstStop, { ...secondStop, start_time: `${moved.start_time}:00`, end_time: "13:30:00" }, { ...lunchStop, start_time: `${lunch.start_time}:00` }] },
      };
      return route.fulfill({ json: latestSession });
    }
    if (url.pathname.endsWith("/confirm") && request.method() === "POST") {
      confirmationCount += 1;
      return route.fulfill({ json: { trip_id: "trip-1", plan_id: "plan-1", plan_version: 2, already_confirmed: false } });
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
  expect(draftRequests[0].candidate_ids).toEqual([firstId, secondId, lunchId, lodgingId]);
  expect(draftRequests[0].schedules).toEqual([
    { candidate_id: firstId, day, start_time: "10:00" },
    { candidate_id: secondId, day, start_time: "12:30" },
    { candidate_id: lunchId, day, start_time: "12:15" },
  ]);
  await expect(page.getByText("江北老街", { exact: true }).last()).toBeVisible();
  await expect(page.getByText("鑫丰特色餐饮", { exact: true }).last()).toBeVisible();
  await expect(page.getByText("已按你选的冲突调整方案重新核验")).toBeVisible();
  await expect(page.locator(".advisor-confirm-bar")).not.toContainText("预览已过期");
  await page.getByRole("button", { name: "检查并协商" }).click();
  await expect.poll(() => negotiationCount).toBe(2);
  await page.getByRole("button", { name: "采用并重新核验" }).click();
  await expect(page.getByText("行程或旅行条件刚刚更新，已同步最新安排；请检查后再重新协商。未覆盖新版本。")).toBeVisible();
  await expect(page.locator(".advisor-stop-list")).toContainText("14:00");
  expect(draftRequests[1].expected_draft_version).toBe(2);
  expect(confirmationCount).toBe(0);
  await expect(page.locator(".advisor-confirm-bar")).not.toContainText("预览已过期");
  if (process.env.TRAVELPILOT_QA_SCREENSHOT) {
    await page.locator(".advisor-stop-list").screenshot({ path: join(tmpdir(), "travelpilot-advisor-negotiation-after.png") });
  }
});
