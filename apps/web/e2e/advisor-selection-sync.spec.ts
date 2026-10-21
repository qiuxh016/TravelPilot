import { expect, test } from "@playwright/test";

test("用户选择餐饮和住宿后右侧缺项即时同步", async ({ page }, testInfo) => {
  const sessionId = "selection-sync-session";
  const tripId = "selection-sync-trip";
  const now = "2026-10-21T08:00:00Z";
  const day = "2026-11-12";
  const candidates = [
    {
      id: "lunch-1", provider: "fake", provider_id: "restaurant-1", name: "模拟川味餐馆",
      address: "成都市青羊区", category: "川菜餐厅", planning_category: "FOOD", meal_type: "LUNCH",
      latitude: 30.66, longitude: 104.05, duration_min: 90, confidence: 1,
      verification_status: "SIMULATED", verified_at: now,
    },
    {
      id: "hotel-1", provider: "fake", provider_id: "hotel-1", name: "模拟旅行酒店",
      address: "成都市锦江区", category: "住宿", planning_category: "LODGING",
      latitude: 30.65, longitude: 104.07, duration_min: 60, confidence: 1,
      verification_status: "SIMULATED", verified_at: now,
    },
  ];
  const session = {
    id: sessionId, mode: "existing", trip_id: tripId, destination: "成都",
    start_date: day, end_date: day, budget: null, pace_level: 3, preference_text: "",
    candidates,
    context: {
      revision: 1, selected_candidate_ids: [], current_plan: null, confirmed_memories: [],
      planning_inputs: { origin: "", companions: [], hard_constraints: [] },
    },
    draft: {
      version: 1, context_revision: 1, generated_at: now, selected_candidate_ids: [],
      attention_items: [
        { kind: "MEAL_MISSING", day, meal_type: "LUNCH" },
        { kind: "LODGING_MISSING", day },
      ],
      lodging: [], warnings: [], changes: [], change_summary: { add: 0, remove: 0, move: 0, keep: 0 },
      pace_change: null, stops: [],
    },
    draft_version: 1, draft_stale: false, status: "ACTIVE", confirmed_trip_id: null,
    confirmed_plan_id: null, created_at: now, updated_at: now,
    messages: [{
      id: "assistant-1", role: "assistant", content: "你可以选择餐饮和住宿候选。",
      payload: {
        planning_category: "FOOD",
        verified_candidates: candidates,
        trip_action_items: [
          { key: "MEAL:午餐", title: "有 1 天午餐还没安排", detail: `日期：${day}`, action: "CHAT", action_label: "找午餐", prompt: `请为${day}推荐午餐地点` },
          { key: "LODGING", title: "还没有住宿备选", detail: "选择住宿候选。", action: "CHAT", action_label: "找住宿", prompt: "找住宿" },
        ],
      },
      created_at: now,
    }],
  };
  let persistedSession: Record<string, unknown> = session;
  let confirmation: unknown = null;

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
      return route.fulfill({ json: persistedSession });
    }
    if (url.pathname === "/api/v1/advisor/sessions" && request.method() === "GET") {
      return route.fulfill({ json: [persistedSession] });
    }
    if (url.pathname.endsWith("/progress")) return route.fulfill({ json: [] });
    if (url.pathname.endsWith("/draft") && request.method() === "POST") {
      const body = request.postDataJSON() as {
        candidate_ids: string[];
        schedules: { candidate_id: string; day: string; start_time: string }[];
      };
      expect(body.candidate_ids).toEqual(["lunch-1", "hotel-1"]);
      expect(body.schedules).toEqual([{ candidate_id: "lunch-1", day, start_time: "10:30" }]);
      persistedSession = {
        ...session,
        context: { ...session.context, revision: 2, selected_candidate_ids: body.candidate_ids },
        draft: {
          version: 2, context_revision: 2, generated_at: now,
          selected_candidate_ids: body.candidate_ids,
          attention_items: [],
          lodging: [{ candidate_id: "hotel-1", name: "模拟旅行酒店", address: "成都市锦江区",
            provider: "fake", verification_status: "SIMULATED" }],
          warnings: [],
          changes: [{ stable_key: "lunch-1", operation: "ADD", name: "模拟川味餐馆",
            reason: "用户选择", change_cost: 1, before: null,
            after: { day, start_time: "10:30", end_time: "12:00" } }],
          change_summary: { add: 1, remove: 0, move: 0, keep: 0 },
          pace_change: null,
          stops: [{ candidate_id: "lunch-1", name: "模拟川味餐馆", day, start_time: "10:30",
            end_time: "12:00", latitude: 30.66, longitude: 104.05, category: "川菜餐厅",
            planning_category: "FOOD", meal_type: "LUNCH", address: "成都市青羊区" }],
        },
        draft_version: 2,
      };
      return route.fulfill({ json: persistedSession });
    }
    if (url.pathname.endsWith("/confirm") && request.method() === "POST") {
      confirmation = request.postDataJSON();
      persistedSession = {
        ...persistedSession,
        status: "CONFIRMED",
        trip_id: tripId,
        confirmed_trip_id: tripId,
        confirmed_plan_id: "selection-sync-plan",
      };
      return route.fulfill({ json: {
        trip_id: tripId, plan_id: "selection-sync-plan", plan_version: 4, already_confirmed: false,
      } });
    }
    return route.fulfill({ json: {} });
  });

  await page.goto(`/advisor?sessionId=${sessionId}`);
  const lunch = page.locator(".advisor-live-candidate").filter({ hasText: "模拟川味餐馆" });
  await expect(lunch).toBeVisible();
  await lunch.getByRole("button", { name: "加入午餐" }).click();
  const attentionPanel = page.getByRole("region", { name: "需要你决定的行程空档" });
  await expect(attentionPanel).toContainText("已选餐饮地点：模拟川味餐馆");
  await expect(attentionPanel).toContainText("还需选择用餐日期和时间");
  await attentionPanel.getByRole("button", { name: "去设置" }).click();
  await lunch.getByLabel("用餐日期").selectOption(day);
  await lunch.getByLabel("用餐时间").fill("10:30");

  await page.getByRole("button", { name: /^住宿 \d+$/ }).click();
  const hotel = page.locator(".advisor-live-candidate").filter({ hasText: "模拟旅行酒店" });
  await expect(hotel).toBeVisible();
  await hotel.getByRole("button", { name: "加入住宿" }).click();

  const preview = page.getByRole("region", { name: "需要你决定的行程空档" });
  await expect(preview).toContainText("当前缺项已补齐");
  await expect(preview).toContainText("已按你刚才的选择补上餐饮和住宿候选");
  await expect(preview).not.toContainText("还没有安排午餐");
  await expect(preview).not.toContainText("住宿还没有安排");

  const itineraryPreview = page.locator(".advisor-itinerary");
  await expect(page.locator(".advisor-selected-candidate-live").filter({ hasText: "已选午餐" }))
    .toContainText("模拟川味餐馆");
  await expect(page.locator(".advisor-selected-candidate-live").filter({ hasText: "已选住宿" }))
    .toContainText("模拟旅行酒店");
  await expect(page.getByRole("button", { name: "确认并应用到行程" })).toBeDisabled();
  await expect(itineraryPreview).toBeVisible();

  await page.getByRole("button", { name: /更新预览（2）/ }).click();
  await expect(itineraryPreview).toBeInViewport();
  await expect(itineraryPreview).toContainText("模拟川味餐馆");
  await expect(itineraryPreview).toContainText("2026-11-12");
  await expect(itineraryPreview).toContainText("模拟旅行酒店");
  await expect(page.getByRole("button", { name: "确认并应用到行程" })).toBeEnabled();

  // The committed server snapshot is the source used when the user returns.
  await page.reload();
  await expect(page.locator(".advisor-itinerary")).toContainText("模拟川味餐馆");
  await expect(page.locator(".advisor-itinerary")).toContainText("模拟旅行酒店");
  await page.getByRole("button", { name: "确认并应用到行程" }).click();
  await expect.poll(() => confirmation).toEqual({ draft_version: 2 });
  await expect(page).toHaveURL(new RegExp(`/trips/${tripId}/plan$`));
  await page.goto(`/advisor?sessionId=${sessionId}`);
  await expect(page.locator(".advisor-draft-state")).toHaveText("已写入正式行程");
  const confirmedChecklist = page.getByRole("region", { name: "行程保存检查结果" });
  await expect(confirmedChecklist)
    .toContainText("行程已保存，检查结果已同步");
  await expect(page.getByRole("link", { name: "查看已更新行程" })).toBeVisible();
  await confirmedChecklist.scrollIntoViewIfNeeded();
  await confirmedChecklist.screenshot({ path: testInfo.outputPath("confirmed-advisor-sync.png") });
});

test("确认写入待办直接创建并提交已选餐饮安排", async ({ page }) => {
  const sessionId = "task-write-session";
  const tripId = "task-write-trip";
  const now = "2026-10-21T08:00:00Z";
  const day = "2026-11-12";
  const candidate = {
    id: "lunch-1", provider: "fake", provider_id: "restaurant-1", name: "模拟午餐餐馆",
    address: "成都市青羊区", category: "川菜餐厅", planning_category: "FOOD", meal_type: "LUNCH",
    latitude: 30.66, longitude: 104.05, duration_min: 90, confidence: 1,
    verification_status: "SIMULATED", verified_at: now,
    explicit_day: day, explicit_start_time: "12:00",
  };
  let persistedSession: Record<string, unknown> = {
    id: sessionId, mode: "existing", trip_id: tripId, destination: "成都",
    start_date: day, end_date: day, budget: null, pace_level: 3, preference_text: "",
    candidates: [candidate],
    context: {
      revision: 2, selected_candidate_ids: [candidate.id], current_plan: null,
      confirmed_memories: [], planning_inputs: { origin: "", companions: [], hard_constraints: [] },
    },
    draft: null, draft_version: 0, draft_stale: false, status: "ACTIVE",
    confirmed_trip_id: null, confirmed_plan_id: null, created_at: now, updated_at: now,
    messages: [{
      id: "assistant-1", role: "assistant",
      content: "现在要确认把午餐12:00写入行程吗？",
      payload: { agent_task: {
        task_id: "write-task", task_type: "MEAL", status: "WAITING_USER",
        goal: "为这天安排午餐", context_revision: 2, turn_number: 2, answers: ["选这家餐馆"],
        question: "现在要确认把午餐12:00写入行程吗？",
        confirmation_options: [
          { label: "确认写入", answer: "确认写入当前安排" },
          { label: "先不写入", answer: "先不写入，暂不处理这项安排" },
        ],
      } }, created_at: now,
    }],
  };
  let draftRequest: unknown = null;
  let confirmRequest: unknown = null;

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
    if (url.pathname === "/api/v1/advisor/sessions" && request.method() === "GET") {
      return route.fulfill({ json: [persistedSession] });
    }
    if (url.pathname === `/api/v1/advisor/sessions/${sessionId}` && request.method() === "GET") {
      return route.fulfill({ json: persistedSession });
    }
    if (url.pathname.endsWith("/draft") && request.method() === "POST") {
      draftRequest = request.postDataJSON();
      const body = draftRequest as { candidate_ids: string[]; schedules: { candidate_id: string; day: string; start_time: string }[] };
      expect(body.candidate_ids).toEqual([candidate.id]);
      expect(body.schedules).toEqual([{ candidate_id: candidate.id, day, start_time: "12:00" }]);
      persistedSession = {
        ...persistedSession,
        context: { ...(persistedSession.context as object), revision: 3, selected_candidate_ids: [candidate.id] },
        draft_version: 3,
        draft: {
          version: 3, context_revision: 3, generated_at: now, selected_candidate_ids: [candidate.id],
          attention_items: [], lodging: [], warnings: [],
          changes: [{ stable_key: candidate.id, operation: "ADD", name: candidate.name,
            reason: "用户确认", change_cost: 1, before: null,
            after: { day, start_time: "12:00", end_time: "13:30" } }],
          change_summary: { add: 1, remove: 0, move: 0, keep: 0 }, pace_change: null,
          stops: [{ candidate_id: candidate.id, name: candidate.name, day, start_time: "12:00",
            end_time: "13:30", latitude: candidate.latitude, longitude: candidate.longitude,
            category: candidate.category, planning_category: "FOOD", meal_type: "LUNCH", address: candidate.address }],
        },
      };
      return route.fulfill({ json: persistedSession });
    }
    if (url.pathname.endsWith("/confirm") && request.method() === "POST") {
      confirmRequest = request.postDataJSON();
      return route.fulfill({ json: {
        trip_id: tripId, plan_id: "task-write-plan", plan_version: 5, already_confirmed: false,
      } });
    }
    return route.fulfill({ json: {} });
  });

  await page.goto(`/advisor?sessionId=${sessionId}`);
  await page.getByRole("button", { name: "确认写入" }).click();
  await expect.poll(() => draftRequest).not.toBeNull();
  await expect.poll(() => confirmRequest).toEqual({ draft_version: 3 });
  await expect(page).toHaveURL(new RegExp(`/trips/${tripId}/plan$`));
});
