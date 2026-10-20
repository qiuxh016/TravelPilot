import { expect, test } from "@playwright/test";

const api = process.env.TRAVELPILOT_TEST_API_URL ?? "http://localhost:8000";
const stub = process.env.TRAVELPILOT_TEST_STUB_URL ?? "http://127.0.0.1:8002";

test("现有旅行 Agent 追问并恢复任务，用户确认后才创建新版本", async ({ page, request }, testInfo) => {
  test.setTimeout(120_000);
  const browserErrors: string[] = [];
  const unexpectedHttpErrors: string[] = [];
  page.on("pageerror", (error) => browserErrors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error" && !message.text().startsWith("Failed to load resource:")) {
      browserErrors.push(message.text());
    }
  });
  page.on("response", (response) => {
    if (response.status() < 400) return;
    const url = new URL(response.url());
    const expectedFakeMap = response.status() === 503 && url.pathname.endsWith("/maps/static-map");
    const expectedDisabledWeatherFixture = response.status() === 503 && url.pathname.endsWith("/weather");
    const expectedStaleDraft = response.status() === 409
      && url.pathname.startsWith("/api/v1/advisor/sessions/")
      && url.pathname.endsWith("/confirm");
    const expectedProfileMemoryConflict = response.status() === 409
      && url.pathname.startsWith("/api/v1/advisor/sessions/")
      && url.pathname.endsWith("/draft");
    if (!expectedFakeMap && !expectedDisabledWeatherFixture && !expectedStaleDraft && !expectedProfileMemoryConflict) {
      unexpectedHttpErrors.push(`${response.status()} ${url.pathname}`);
    }
  });
  const email = `advisor-existing-${Date.now()}@example.invalid`;
  const password = "travelpilot-test-2026";
  let registered = false;
  let csrf = "";
  let testBodyFailed = false;
  try {
    const resetStub = await request.post(`${stub}/reset`);
    expect(resetStub.status()).toBe(204);
    const registration = await request.post(`${api}/api/v1/auth/register`, {
      data: { email, password },
    });
    expect(registration.status()).toBe(202);
    registered = true;

    const mailbox = await request.get(`${api}/api/v1/auth/dev-mailbox/${email}`);
    const link = (await mailbox.json()).link as string;
    const token = new URL(link, api).searchParams.get("token");
    expect(token).toBeTruthy();
    expect((await request.post(`${api}/api/v1/auth/verify-email`, { data: { token } })).ok()).toBeTruthy();
    expect((await request.post(`${api}/api/v1/auth/login`, { data: { email, password } })).ok()).toBeTruthy();
    const cookies = (await request.storageState()).cookies;
    csrf = cookies.find((cookie) => cookie.name === "travelpilot_csrf")?.value ?? "";
    const writeHeaders = { "X-CSRF-Token": decodeURIComponent(csrf) };
    const keyedWriteHeaders = () => ({
      ...writeHeaders,
      "Idempotency-Key": `e2e-${crypto.randomUUID()}`,
    });
    const memorySetting = await request.put(`${api}/api/v1/account/memory-settings`, {
      headers: writeHeaders, data: { enabled: true },
    });
    expect(memorySetting.ok(), await memorySetting.text()).toBeTruthy();

    const start = new Date();
    start.setUTCDate(start.getUTCDate() + 30);
    const end = new Date(start);
    end.setUTCDate(end.getUTCDate() + 1);
    const format = (date: Date) => date.toISOString().slice(0, 10);
    const tripResponse = await request.post(`${api}/api/v1/trips`, {
      headers: writeHeaders,
      data: { destination: "成都", start_date: format(start), end_date: format(end), pace_level: 3 },
    });
    expect(tripResponse.ok(), await tripResponse.text()).toBeTruthy();
    const trip = await tripResponse.json() as { id: string };
    for (const [name, latitude, longitude] of [
      ["人民公园", 30.663, 104.055],
      ["武侯祠", 30.646, 104.048],
    ] as const) {
      const saved = await request.post(`${api}/api/v1/trips/${trip.id}/wishlist`, {
        headers: writeHeaders,
        data: {
          name, category: "ATTRACTION", latitude, longitude,
          suggested_duration_min: 90, priority: 3,
        },
      });
      expect(saved.ok()).toBeTruthy();
    }
    const generated = await request.post(`${api}/api/v1/trips/${trip.id}/plan-runs`, { headers: writeHeaders });
    expect(generated.ok(), await generated.text()).toBeTruthy();
    const originalPlans = await request.get(`${api}/api/v1/trips/${trip.id}/plans`);
    expect(await originalPlans.json()).toHaveLength(1);

    const restTripStart = new Date(start);
    restTripStart.setUTCDate(restTripStart.getUTCDate() + 10);
    const restTripResponse = await request.post(`${api}/api/v1/trips`, {
      headers: writeHeaders,
      data: {
        destination: "重庆", start_date: format(restTripStart), end_date: format(restTripStart),
        pace_level: 3,
      },
    });
    expect(restTripResponse.ok(), await restTripResponse.text()).toBeTruthy();
    const restTrip = await restTripResponse.json() as { id: string };
    for (const [name, latitude, longitude] of [
      ["山城步道", 29.557, 106.575],
      ["湖广会馆", 29.558, 106.575],
      ["人民大礼堂", 29.564, 106.550],
      ["鹅岭公园", 29.554, 106.536],
    ] as const) {
      const saved = await request.post(`${api}/api/v1/trips/${restTrip.id}/wishlist`, {
        headers: writeHeaders,
        data: {
          name, category: "ATTRACTION", latitude, longitude,
          suggested_duration_min: 180, priority: 3,
        },
      });
      expect(saved.ok(), await saved.text()).toBeTruthy();
    }
    const restSessionResponse = await request.post(`${api}/api/v1/advisor/sessions`, {
      headers: writeHeaders,
      data: { mode: "existing", trip_id: restTrip.id },
    });
    expect(restSessionResponse.ok(), await restSessionResponse.text()).toBeTruthy();
    const restSession = await restSessionResponse.json() as {
      id: string; candidates: Array<{ id: string }>;
    };
    expect(restSession.candidates).toHaveLength(4);
    const restDraftResponse = await request.post(
      `${api}/api/v1/advisor/sessions/${restSession.id}/draft`,
      {
        headers: writeHeaders,
        data: {
          candidate_ids: restSession.candidates.map((candidate) => candidate.id),
          schedules: restSession.candidates.map((candidate, index) => ({
            candidate_id: candidate.id,
            day: format(restTripStart),
            start_time: ["08:00", "11:15", "14:30", "17:45"][index],
          })),
        },
      },
    );
    expect(restDraftResponse.ok(), await restDraftResponse.text()).toBeTruthy();
    const restDraftSession = await restDraftResponse.json() as {
      draft: { attention_items: Array<{ kind: string; day?: string }> };
    };
    expect(restDraftSession.draft.attention_items).toContainEqual({
      kind: "REST_CHECK", day: format(restTripStart),
    });
    expect(await (await request.get(`${api}/api/v1/trips/${restTrip.id}/plans`)).json())
      .toHaveLength(0);

    let staleDraftRaceTriggered = false;
    await page.goto("/login");
    await page.getByLabel("邮箱或用户名").fill(email);
    await page.getByLabel(/密码/).fill(password);
    await page.getByRole("button", { name: "登录", exact: true }).click();
    await expect(page).toHaveURL(/\/trips$/);
    await page.goto("/advisor");
    await page.getByRole("button", { name: /开始新对话/ }).click();
    await page.getByRole("button", { name: "关联已有旅行" }).click();
    await page.getByLabel("选择要咨询的旅行").selectOption(trip.id);
    await expect(page.getByRole("status")).toContainText("模型已配置：");
    await page.getByRole("button", { name: "进入这段旅行的对话" }).click();
    await expect(page.getByRole("region", { name: "顾问对话" })).toBeVisible();
    await expect(page.getByText(/沿用当前行程已有时间/).first()).toBeVisible();
    const initialSessionId = new URL(page.url()).searchParams.get("sessionId");
    expect(initialSessionId).toBeTruthy();
    const currentSessionResponse = await request.get(`${api}/api/v1/advisor/sessions/${initialSessionId}`);
    const currentSession = await currentSessionResponse.json() as {
      context: { current_plan: { stops: Array<{ candidate_id: string; name: string; day: string; start_time: string }> } };
    };
    const firstCurrentStop = currentSession.context.current_plan.stops[0];
    expect(firstCurrentStop).toBeTruthy();
    const firstCurrentCard = page.locator(".advisor-live-candidate")
      .filter({ hasText: firstCurrentStop.name });
    await expect(firstCurrentCard.getByLabel("安排日期")).toHaveValue(firstCurrentStop.day);
    await expect(firstCurrentCard.getByLabel("开始时间")).toHaveValue(firstCurrentStop.start_time.slice(0, 5));
    const explicitPreference = "请记住我不吃辣，以后优先推荐清淡饮食";
    await page.locator("#advisor-message").fill(explicitPreference);
    await page.getByRole("button", { name: "发送" }).click();
    const savePreference = page.getByRole("button", { name: "保存为必须遵守的限制" });
    await expect(savePreference).toBeVisible();
    await savePreference.click();
    await expect(page.getByText(/已保存这条明确表达的偏好/)).toBeVisible();
    const savedMemoriesResponse = await request.get(`${api}/api/v1/account/memories`);
    expect(savedMemoriesResponse.ok()).toBeTruthy();
    expect(await savedMemoriesResponse.json()).toContainEqual(expect.objectContaining({
      text: explicitPreference,
      constraint_type: "HARD_LIMIT",
      source: "USER_EXPLICIT",
      confirmed: true,
    }));
    await expect(page.getByText("已保存到偏好记忆")).toBeVisible();
    await expect(page.getByRole("button", { name: "保存为旅行偏好" })).toHaveCount(0);
    await page.locator("#advisor-message").fill("帮我直接订酒店");
    const bookingResponsePromise = page.waitForResponse((response) =>
      response.url().includes("/api/v1/advisor/sessions/")
      && response.url().endsWith("/messages") && response.request().method() === "POST",
    );
    await page.getByRole("button", { name: "发送" }).click();
    const bookingSession = await (await bookingResponsePromise).json() as {
      messages: Array<{ role: string; content: string; payload: Record<string, unknown> | null }>;
    };
    const bookingAssistant = [...bookingSession.messages].reverse().find((item) => item.role === "assistant");
    expect(bookingAssistant?.content).toContain("当前不能代订酒店或民宿");
    expect(bookingAssistant?.payload?.unsupported_action_code).toBe("LODGING_BOOKING_UNAVAILABLE");
    expect(bookingAssistant?.payload?.tool_calls).toEqual([]);

    await page.locator("#advisor-message").fill("我喜欢轻松一点，也想去博物馆");
    await page.getByRole("button", { name: "发送" }).click();
    await expect(page.getByText("我记下了你提到的旅行偏好，后续建议会结合这段对话和当前旅行信息。"))
      .toBeVisible({ timeout: 30_000 });
    const modelMemorySuggestion = page.locator('[aria-label="顾问提出的偏好记忆建议"]').last();
    await expect(modelMemorySuggestion).toContainText("偏好轻松节奏");
    await expect(modelMemorySuggestion).toContainText("我喜欢轻松一点");
    await modelMemorySuggestion.getByRole("button", { name: "确认保存为偏好" }).click();
    await expect(page.getByText("已按你的确认保存偏好记忆；后续是否使用仍由账户记忆开关控制。"))
      .toBeVisible();
    const modelMemories = await request.get(`${api}/api/v1/account/memories`);
    expect(await modelMemories.json()).toContainEqual(expect.objectContaining({
      category: "PACE", text: "偏好轻松节奏", constraint_type: "PREFERENCE",
      source: "MODEL_SUGGESTED", confirmed: true,
    }));
    await page.locator("#advisor-message").fill("那哪个更适合带孩子？");
    await page.getByRole("button", { name: "发送" }).click();
    await expect(page.getByText(/结合你前面提到的博物馆偏好/)).toBeVisible({ timeout: 30_000 });
    const parkMemory = await request.post(`${api}/api/v1/account/memories`, {
      headers: writeHeaders,
      data: { category: "GENERAL", text: "不去公园", constraint_type: "HARD_LIMIT" },
    });
    expect(parkMemory.ok(), await parkMemory.text()).toBeTruthy();
    const parkMemoryRecord = await parkMemory.json() as { id: string };
    await page.locator("#advisor-message").fill("看看时间怎么安排比较合适");
    await page.getByRole("button", { name: "发送" }).click();
    await expect(page.getByText("你希望每天安排得轻松一些吗？").first()).toBeVisible({ timeout: 30_000 });
    await expect(page.getByRole("region", { name: "等待你补充" }).last())
      .toContainText("点选即可继续原任务");
    const pendingQuestion = page.getByRole("region", { name: "等待你补充" }).last();
    await expect(pendingQuestion).toBeInViewport();
    await expect(pendingQuestion).toContainText("这一步需要你来决定");
    await expect(pendingQuestion).toContainText("在下方输入自己的答案");
    if (process.env.TRAVELPILOT_QA_SCREENSHOT) {
      await page.screenshot({ path: process.env.TRAVELPILOT_QA_SCREENSHOT, fullPage: false });
    }
    await page.locator("#advisor-message").fill("推荐午餐");
    await pendingQuestion.getByRole("button", { name: "改问其他问题" }).click();
    await expect(page.locator(".advisor-message.user").filter({ hasText: "取消" }).last()).toBeVisible();
    await expect(page.locator(".advisor-message.user").filter({ hasText: "推荐午餐" }).last()).toBeVisible();
    await expect(page.locator(".advisor-task-state.is-cancelled").last()).toContainText("已取消");
    await page.locator("#advisor-message").fill("看看时间怎么安排比较合适");
    await page.getByRole("button", { name: "发送" }).click();
    await expect(page.getByText("你希望每天安排得轻松一些吗？").last()).toBeVisible({ timeout: 30_000 });
    const refreshedPendingQuestion = page.getByRole("region", { name: "等待你补充" }).last();
    await expect(refreshedPendingQuestion).toBeVisible();
    await expect(refreshedPendingQuestion).toContainText("每天安排得轻松一些吗");
    const beforeAnswer = await request.get(`${api}/api/v1/trips/${trip.id}/plans`);
    expect(await beforeAnswer.json()).toHaveLength(1);
    await refreshedPendingQuestion.getByRole("button", { name: "自定义回答" }).click();
    await page.locator("#advisor-message").fill("请把后面的行程安排得不要太赶，多留一些休息时间");
    const scheduleResponsePromise = page.waitForResponse((response) =>
      response.url().includes("/api/v1/advisor/sessions/")
      && response.url().endsWith("/messages") && response.request().method() === "POST",
    );
    await page.getByRole("button", { name: "发送" }).click();
    const memoryConflictCard = page.getByRole("alert").filter({ hasText: "地点与你的长期记忆不一致" });
    await expect(memoryConflictCard).toContainText("人民公园");
    await memoryConflictCard.getByRole("button", { name: "我确认，本次仍按所选地点继续" }).click();
    await expect(page.locator(".advisor-live-warning").filter({ hasText: "本次草案按你的明确确认临时覆盖了长期偏好记忆" }))
      .toBeVisible({ timeout: 60_000 });
    await expect(page.locator(".advisor-live-warning").filter({ hasText: "请人工核对硬性限制：不去公园" })).toHaveCount(0);
    await expect(page.locator(".advisor-live-warning").filter({ hasText: "不吃辣" })).toBeVisible();
    const removeTestMemory = await request.delete(`${api}/api/v1/account/memories/${parkMemoryRecord.id}`, {
      headers: writeHeaders,
    });
    expect(removeTestMemory.status()).toBe(204);
    const scheduleSession = await (await scheduleResponsePromise).json() as {
      messages: Array<{ role: string; payload: Record<string, unknown> | null }>;
    };
    const schedulePayload = [...scheduleSession.messages].reverse().find((item) => item.role === "assistant")?.payload;
    expect((schedulePayload?.adjustments_applied as string[] | undefined) ?? []).toContain("已将节奏调整为 2/5");
    expect(Array.isArray(schedulePayload?.trip_action_items)).toBe(true);
    expect((schedulePayload?.trip_action_items as unknown[] | undefined)?.length).toBeGreaterThan(0);
    const draftFollowUps = page.getByRole("region", { name: "草案后续建议" }).last();
    await expect(draftFollowUps).toContainText("这份草案还有待完善的时段");
    await expect(draftFollowUps).toContainText("选点后需要重新检查草案");
    const lunchRecommendation = draftFollowUps.getByRole("button", { name: "推荐午餐" });
    await expect(lunchRecommendation).toBeVisible();
    const guideImport = draftFollowUps.getByRole("button", { name: "从攻略导入" }).first();
    await expect(guideImport).toBeVisible();
    await guideImport.click();
    const guideDialog = page.getByRole("dialog");
    await expect(guideDialog).toContainText("把攻略里的地点和实用细节留下来");
    await guideDialog.getByRole("button", { name: "关闭导入窗口" }).click();
    await expect(guideDialog).not.toBeVisible();
    const mealResponsePromise = page.waitForResponse((response) =>
      response.url().includes("/api/v1/advisor/sessions/")
      && response.url().endsWith("/messages") && response.request().method() === "POST",
    );
    await lunchRecommendation.click();
    const mealSession = await (await mealResponsePromise).json() as {
      messages: Array<{ role: string; payload: Record<string, unknown> | null }>;
    };
    const mealPayload = [...mealSession.messages].reverse().find((item) => item.role === "assistant")?.payload;
    expect(mealPayload?.planning_category).toBe("FOOD");
    expect(mealPayload?.meal_type).toBe("LUNCH");
    expect((mealPayload?.tool_calls as Array<{ tool?: string }> | undefined)?.some((call) => call.tool === "search_places")).toBe(true);
    expect(mealPayload?.schedule_recommendation_requested).toBe(false);
    const mealCandidate = page.locator(".advisor-live-candidate")
      .filter({ hasText: "模拟川味餐馆" });
    await expect(mealCandidate).toBeVisible({ timeout: 30_000 });
    await expect(mealCandidate).toContainText("午餐");
    await mealCandidate.getByRole("button", { name: "加入午餐" }).click();
    await mealCandidate.getByLabel("用餐日期").selectOption(format(start));
    await mealCandidate.getByLabel("用餐时间").fill("12:30");
    await expect(mealCandidate.getByLabel("用餐日期")).toHaveValue(format(start));
    await expect(mealCandidate.getByLabel("用餐时间")).toHaveValue("12:30");
    expect(await (await request.get(`${api}/api/v1/trips/${trip.id}/plans`)).json()).toHaveLength(1);
    const mealSessionId = new URL(page.url()).searchParams.get("sessionId");
    expect(mealSessionId).toBeTruthy();
    const mealSessionState = await (await request.get(
      `${api}/api/v1/advisor/sessions/${mealSessionId}`,
    )).json() as { candidates: Array<{ id: string; name: string; planning_category?: string }> };
    const selectedMeal = mealSessionState.candidates.find((candidate) =>
      candidate.name === "模拟川味餐馆" && candidate.planning_category === "FOOD",
    );
    expect(selectedMeal).toBeTruthy();
    const unusualMealDraft = await request.post(
      `${api}/api/v1/advisor/sessions/${mealSessionId}/draft`,
      {
        headers: writeHeaders,
        data: {
          candidate_ids: [selectedMeal!.id],
          schedules: [{ candidate_id: selectedMeal!.id, day: format(start), start_time: "21:00" }],
        },
      },
    );
    expect(unusualMealDraft.ok(), await unusualMealDraft.text()).toBeTruthy();
    const unusualMealState = await unusualMealDraft.json() as {
      draft: { warnings: string[] };
    };
    expect(unusualMealState.draft.warnings.some((warning) =>
      warning.includes("模拟川味餐馆") && warning.includes("不在常见参考时段 11:00–14:00"),
    )).toBe(true);
    await page.goto(`/advisor?sessionId=${mealSessionId}`);
    await expect(page.locator(".advisor-live-warning").filter({
      hasText: "模拟川味餐馆标记为午餐，当前安排在 21:00",
    })).toBeVisible();
    await mealCandidate.getByRole("button", { name: "已加入 ✓" }).click();
    const compoundGoal = "帮我规划整天行程，同时推荐午餐地点";
    await page.locator("#advisor-message").fill(compoundGoal);
    const compoundResponsePromise = page.waitForResponse((response) =>
      response.url().includes("/api/v1/advisor/sessions/")
      && response.url().endsWith("/messages") && response.request().method() === "POST",
    );
    await page.getByRole("button", { name: "发送" }).click();
    const compoundSession = await (await compoundResponsePromise).json() as {
      messages: Array<{ role: string; content: string; payload: Record<string, unknown> | null }>;
    };
    const compoundAssistant = [...compoundSession.messages].reverse().find((item) => item.role === "assistant");
    expect(compoundAssistant?.payload?.planning_category).toBe("FOOD");
    expect(compoundAssistant?.payload?.meal_type).toBe("LUNCH");
    expect(compoundAssistant?.payload?.schedule_recommendation_requested).toBe(true);
    expect((compoundAssistant?.payload?.verified_candidates as unknown[] | undefined)?.length)
      .toBeGreaterThan(0);
    await expect(page.locator(".advisor-candidate-evidence-note")).toContainText("不代表实时营业、价格或全网热度已核实");
    await expect(page.locator(".advisor-live-candidate").first()).toContainText("来源：本地模拟数据");
    const compoundMessage = page.locator(".advisor-message.assistant")
      .filter({ hasText: "你希望每天安排得轻松一些吗" }).last();
    await expect(compoundMessage.locator(".advisor-agent-trace")).toContainText("地点证据");
    await expect(compoundMessage.locator(".advisor-agent-trace")).toContainText("地图");
    await expect(compoundMessage.locator(".advisor-agent-trace")).toContainText("候选只代表地图检索结果");
    if (process.env.TRAVELPILOT_EVIDENCE_SCREENSHOT_PATH) {
      await compoundMessage.locator(".advisor-agent-trace").screenshot({
        path: process.env.TRAVELPILOT_EVIDENCE_SCREENSHOT_PATH,
      });
    }
    expect(compoundAssistant?.payload?.schedule_suggestions).toEqual(expect.any(Array));
    expect(compoundAssistant?.payload?.agent_task).toMatchObject({
      task_type: "SCHEDULE", status: "WAITING_USER",
    });
    expect((compoundAssistant?.payload?.tool_calls as Array<{ tool?: string }> | undefined)
      ?.some((call) => call.tool === "search_places")).toBe(true);
    expect(compoundAssistant?.content).toContain("你希望每天安排得轻松一些吗");
    expect(await (await request.get(`${api}/api/v1/trips/${trip.id}/plans`)).json()).toHaveLength(1);
    await page.locator("#advisor-message").fill("轻松一点");
    const resumedCompoundResponsePromise = page.waitForResponse((response) =>
      response.url().includes("/api/v1/advisor/sessions/")
      && response.url().endsWith("/messages") && response.request().method() === "POST",
    );
    await page.getByRole("button", { name: "发送" }).click();
    const resumedCompoundSession = await (await resumedCompoundResponsePromise).json() as {
      messages: Array<{ role: string; content: string; payload: Record<string, unknown> | null }>;
    };
    const resumedCompoundAssistant = [...resumedCompoundSession.messages].reverse()
      .find((item) => item.role === "assistant");
    expect(resumedCompoundAssistant?.payload?.planning_category).toBe("FOOD");
    expect(resumedCompoundAssistant?.payload?.schedule_recommendation_requested).toBe(true);
    expect((resumedCompoundAssistant?.payload?.schedule_suggestions as unknown[] | undefined)?.length)
      .toBeGreaterThan(0);
    expect(resumedCompoundAssistant?.content).toContain("按午餐需求核验到餐饮候选");
    expect(await (await request.get(`${api}/api/v1/trips/${trip.id}/plans`)).json()).toHaveLength(1);
    await page.locator("#advisor-message").fill("按轻松节奏优化时间安排");
    await page.getByRole("button", { name: "发送" }).click();
    await expect(page.getByText(/排出一版时间建议/).last())
      .toBeVisible({ timeout: 60_000 });
    const tripActionPanel = page.getByRole("region", { name: "这段旅行可以继续完成的事项" });
    await expect(tripActionPanel).toBeVisible();
    await expect(tripActionPanel.getByRole("button", { name: "找午餐", exact: true })).toHaveCount(0);
    await tripActionPanel.getByRole("button", { name: "填写出发地" }).click();
    await expect(page.locator("#advisor-origin-input")).toBeFocused();
    if (process.env.TRAVELPILOT_E2E_GAP_SCREENSHOT_PATH && testInfo.project.name === "chromium") {
      await tripActionPanel.scrollIntoViewIfNeeded();
      await page.screenshot({ path: process.env.TRAVELPILOT_E2E_GAP_SCREENSHOT_PATH });
    }
    const trace = page.locator(".advisor-agent-trace").filter({ hasText: "确定性排程器" }).last();
    await trace.locator("summary").click();
    await expect(trace).toContainText("确定性排程器");
    await expect(trace).toContainText("草案通过结构校验");
    const attentionPanel = page.getByRole("region", { name: "需要你决定的行程空档" });
    await expect(attentionPanel).toBeVisible();
    await attentionPanel.getByRole("button", { name: "选择处理方式" }).first().click();
    await expect(attentionPanel.getByText(/怎么处理：/)).toBeVisible();
    await expect(attentionPanel.getByRole("button", { name: "从攻略导入" })).toHaveCount(1);
    await attentionPanel.locator(".advisor-attention-choice-actions").getByRole("button", { name: "稍后", exact: true }).click();
    await attentionPanel.getByRole("button", { name: /查看已处理项/ }).click();
    await attentionPanel.getByRole("button", { name: "恢复检查" }).click();
    await attentionPanel.getByRole("button", { name: "查看待处理项" }).click();
    const mainSessionId = new URL(page.url()).searchParams.get("sessionId");
    expect(mainSessionId).toBeTruthy();
    await page.goto(`/advisor?sessionId=${restSession.id}`);
    const restAttentionPanel = page.getByRole("region", { name: "需要你决定的行程空档" });
    await expect(restAttentionPanel.getByText("当天安排跨度较长，未检测到明显休息空档")).toBeVisible();
    await restAttentionPanel.getByRole("button", { name: "检查休息安排" }).click();
    const restChoice = restAttentionPanel.locator(".advisor-attention-choice-actions");
    await expect(restChoice.getByRole("button", { name: "调整得轻松一点" })).toBeVisible();
    await expect(restChoice.getByRole("button", { name: "保留当前安排" })).toBeVisible();
    await expect(restChoice.getByRole("button", { name: "稍后" })).toBeVisible();
    await expect(restChoice.getByRole("button", { name: "从攻略导入" })).toHaveCount(0);
    await restChoice.getByRole("button", { name: "保留当前安排" }).click();
    await expect(page.locator(".advisor-action-notice"))
      .toContainText("已记录：这项对本次旅行不需要。");
    await restAttentionPanel.getByRole("button", { name: /查看已处理项/ }).click();
    await expect(restAttentionPanel).toContainText("当天安排跨度较长，未检测到明显休息空档");
    await expect(restAttentionPanel.getByRole("button", { name: "恢复检查" })).toBeVisible();
    const savedRestSession = await (await request.get(
      `${api}/api/v1/advisor/sessions/${restSession.id}`,
    )).json() as {
      context: { checklist_decisions?: Record<string, { decision: string; draft_version: number }> };
      draft: { version: number };
    };
    expect(savedRestSession.context.checklist_decisions?.[`REST_CHECK:${format(restTripStart)}`])
      .toEqual({ decision: "NOT_NEEDED", draft_version: savedRestSession.draft.version });
    expect(await (await request.get(`${api}/api/v1/trips/${restTrip.id}/plans`)).json())
      .toHaveLength(0);
    await restAttentionPanel.getByRole("button", { name: "查看待处理项" }).click();
    await page.goto(`/advisor?sessionId=${mainSessionId}`);
    await expect(page.getByRole("region", { name: "顾问对话" })).toBeVisible();
    const transitRequest = "从人民公园到武侯祠怎么坐地铁";
    await page.locator("#advisor-message").fill(transitRequest);
    let dropRouteResponse = true;
    let compositePrompt = "";
    let dropCompositeResponse = false;
    const compositeRetryKeys: string[] = [];
    await page.route("**/api/v1/advisor/sessions/*/messages", async (route) => {
      const body = JSON.parse(route.request().postData() ?? "{}") as { content?: string };
      if (body.content === compositePrompt && compositePrompt) {
        compositeRetryKeys.push(route.request().headers()["idempotency-key"] ?? "");
      }
      if (
        dropRouteResponse && body.content === transitRequest
        || dropCompositeResponse && body.content === compositePrompt && Boolean(compositePrompt)
      ) {
        const processed = await route.fetch();
        expect(processed.ok()).toBeTruthy();
        if (body.content === transitRequest) dropRouteResponse = false;
        if (body.content === compositePrompt) dropCompositeResponse = false;
        await route.abort("failed");
        return;
      }
      await route.continue();
    });
    await page.getByRole("button", { name: "发送" }).click();
    await expect(page.locator("#advisor-message")).toHaveValue(transitRequest);
    await page.reload();
    await expect(page.getByRole("region", { name: "顾问对话" })).toBeVisible();
    await page.locator("#advisor-message").fill(transitRequest);
    await page.getByRole("button", { name: "发送" }).click();
    const routeResult = page.getByRole("region", { name: "公交/地铁路线结果" });
    await expect(routeResult).toBeVisible({ timeout: 30_000 });
    await expect(routeResult).toContainText("人民公园");
    await expect(routeResult).toContainText("武侯祠");
    await expect(routeResult).toContainText("本地模拟");
    await expect(routeResult).toContainText("公交/地铁约");
    await expect(routeResult).toContainText("模拟公交换乘路线");
    const currentSessionId = new URL(page.url()).searchParams.get("sessionId");
    expect(currentSessionId).toBeTruthy();
    const recoveredSession = await request.get(`${api}/api/v1/advisor/sessions/${currentSessionId}`);
    const recoveredMessages = (await recoveredSession.json()).messages as Array<{ role: string; content: string }>;
    expect(recoveredMessages.filter((item) => item.role === "user" && item.content === transitRequest)).toHaveLength(1);
    await page.locator("#advisor-message").fill("从人民公园步行到武侯祠");
    await page.getByRole("button", { name: "发送" }).click();
    const walkingResult = page.getByRole("region", { name: "步行路线结果" });
    await expect(walkingResult).toBeVisible({ timeout: 30_000 });
    await expect(walkingResult).toContainText("步行约");
    await page.locator("#advisor-message").fill("从人民公园开车到武侯祠");
    await page.getByRole("button", { name: "发送" }).click();
    const drivingResult = page.getByRole("region", { name: "驾车路线结果" });
    await expect(drivingResult).toBeVisible({ timeout: 30_000 });
    await expect(drivingResult).toContainText("驾车约");
    compositePrompt = "帮我规划行程时间，同时查询人民公园到武侯祠的驾车路线";
    const compositeRequestPromise = page.waitForRequest((request) => {
      if (!request.url().includes("/api/v1/advisor/sessions/")
        || !request.url().endsWith("/messages") || request.method() !== "POST") return false;
      try {
        return (request.postDataJSON() as { content?: string }).content === compositePrompt;
      } catch {
        return false;
      }
    });
    dropCompositeResponse = true;
    await page.locator("#advisor-message").fill(compositePrompt);
    await page.getByRole("button", { name: "发送" }).click();
    await expect(page.locator(".advisor-live-error")).toBeVisible({ timeout: 30_000 });
    expect(compositeRetryKeys).toHaveLength(1);
    const stubStatsBeforeRetry = await (await request.get(`${stub}/stats`)).json() as {
      agent_decisions: number; extraction_requests: number;
    };
    const compositeResponsePromise = page.waitForResponse((response) =>
      response.url().includes("/api/v1/advisor/sessions/")
      && response.url().endsWith("/messages") && response.request().method() === "POST",
    );
    await page.getByRole("button", { name: "重试刚才请求" }).click();
    const compositeResponse = await compositeResponsePromise;
    expect(compositeResponse.ok()).toBeTruthy();
    const compositeSession = await compositeResponse.json() as {
      messages: Array<{
        role: string;
        content: string;
        idempotency_key?: string | null;
        payload: Record<string, unknown> | null;
      }>;
    };
    expect(compositeRetryKeys).toHaveLength(2);
    expect(compositeRetryKeys[1]).toBe(compositeRetryKeys[0]);
    const stubStatsAfterRetry = await (await request.get(`${stub}/stats`)).json() as {
      agent_decisions: number; extraction_requests: number;
    };
    expect(stubStatsAfterRetry).toEqual(stubStatsBeforeRetry);
    const compositeAssistant = [...compositeSession.messages].reverse()
      .find((item) => item.role === "assistant");
    expect(compositeAssistant?.payload?.turn_orchestration).toMatchObject({
      decision_history: [
        {
          eligible_stages: ["SCHEDULE", "ROUTE"],
          order: ["ROUTE", "SCHEDULE"], decision_source: "MODEL",
        },
        {
          eligible_stages: ["SCHEDULE"],
          order: ["SCHEDULE"], decision_source: "RULE",
        },
      ],
    });
    const compositeTrace = compositeAssistant?.payload?.agent_trace as {
      stage_statuses?: Array<{ stage?: string }>;
      transitions?: Array<{ stage?: string; tool_name?: string; action?: string }>;
    } | undefined;
    expect(compositeTrace?.stage_statuses?.map((stage) => stage.stage)).toEqual(["ROUTE", "SCHEDULE"]);
    expect(compositeTrace?.transitions?.some((transition) => transition.stage === "ROUTE")).toBe(true);
    expect(compositeTrace?.transitions?.some((transition) => transition.stage === "SCHEDULE")).toBe(true);
    const compositeRequest = await compositeRequestPromise;
    const compositeIdempotencyKey = compositeRequest.headers()["idempotency-key"];
    expect(compositeIdempotencyKey).toBeTruthy();
    expect(compositeSession.messages.filter((item) =>
      item.role === "user" && item.content === compositePrompt,
    )).toHaveLength(1);
    expect(compositeSession.messages.filter((item) =>
      item.role === "assistant"
      && item.payload?.response_to_idempotency_key === compositeIdempotencyKey,
    )).toHaveLength(1);
    const progressResponse = await request.get(
      `${api}/api/v1/advisor/sessions/${currentSessionId}/progress`,
      { headers: { "Idempotency-Key": compositeIdempotencyKey } },
    );
    expect(progressResponse.ok()).toBeTruthy();
    const progressEvents = await progressResponse.json() as Array<{
      phase: string; operation: string; metadata: Record<string, unknown>;
    }>;
    const stageOrderEvent = progressEvents.find((event) =>
      event.phase === "turn_orchestration"
      && event.operation === "stage_order_selected"
      && JSON.stringify(event.metadata.order) === JSON.stringify(["ROUTE", "SCHEDULE"]),
    );
    expect(stageOrderEvent).toBeTruthy();
    expect(stageOrderEvent?.metadata.observation_ref).toMatch(/^[a-f0-9]{64}$/);
    expect(stageOrderEvent?.metadata).not.toHaveProperty("user_goal");
    expect(stageOrderEvent?.metadata).not.toHaveProperty("place_names");
    const turnStageEvents = progressEvents.filter((event) => event.phase === "turn_stage");
    expect(turnStageEvents.some((event) =>
      event.metadata.tool === "ROUTE" && event.metadata.state === "STAGE_STARTED",
    )).toBe(true);
    expect(turnStageEvents.some((event) =>
      event.metadata.tool === "ROUTE" && event.metadata.state === "SUCCEEDED",
    )).toBe(true);
    expect(turnStageEvents.some((event) =>
      event.metadata.tool === "SCHEDULE" && event.metadata.state === "SUCCEEDED",
    )).toBe(true);
    expect(turnStageEvents.every((event) =>
      ["ROUTE", "SCHEDULE"].includes(String(event.metadata.tool)),
    )).toBe(true);
    await expect.poll(() => page.locator(".advisor-messages").evaluate((element) =>
      element.scrollTop + element.clientHeight >= element.scrollHeight - 2,
    )).toBe(true);
    const latestAgentTrace = page.locator(".advisor-agent-trace").last();
    await latestAgentTrace.locator("summary").click();
    await expect(latestAgentTrace.getByText("安全执行策略")).toBeVisible();
    await expect(latestAgentTrace).toContainText("路线：完成");
    await expect(latestAgentTrace).toContainText("行程安排：完成");
    await expect(latestAgentTrace.getByText("路线 · 两地路线查询")).toBeVisible();
    await expect(latestAgentTrace.getByText("行程安排 · 确定性排程器")).toBeVisible();
    const screenshotPath = testInfo.project.name === "chromium"
      ? process.env.TRAVELPILOT_E2E_SCREENSHOT_PATH
      : process.env.TRAVELPILOT_E2E_MOBILE_SCREENSHOT_PATH;
    if (screenshotPath) {
      await page.screenshot({ path: screenshotPath });
    }
    const stubStats = await request.get(`${stub}/stats`);
    const stubUsage = await stubStats.json() as {
      agent_decisions: number; extraction_requests: number; resumed_context_seen: boolean;
    };
    expect(stubUsage.agent_decisions).toBeGreaterThan(0);
    expect(stubUsage.extraction_requests).toBeGreaterThan(0);
    expect(stubUsage.resumed_context_seen).toBe(true);

    const beforeConfirmation = await request.get(`${api}/api/v1/trips/${trip.id}/plans`);
    expect(await beforeConfirmation.json()).toHaveLength(1);
    const refreshDraftResponsePromise = page.waitForResponse((response) =>
      response.url().includes("/api/v1/advisor/sessions/")
      && response.url().endsWith("/draft") && response.request().method() === "POST",
    );
    await page.getByRole("button", { name: /按当前选择重新生成/ }).click();
    const refreshDraftResponse = await refreshDraftResponsePromise;
    if (refreshDraftResponse.status() === 409) {
      const refreshConflict = page.getByRole("alert").filter({ hasText: "地点与你的长期记忆不一致" });
      await expect(refreshConflict).toContainText("人民公园");
      const overrideDraftResponsePromise = page.waitForResponse((response) =>
        response.url().includes("/api/v1/advisor/sessions/")
        && response.url().endsWith("/draft") && response.request().method() === "POST",
      );
      await refreshConflict.getByRole("button", { name: "我确认，本次仍按所选地点继续" }).click();
      expect((await overrideDraftResponsePromise).status()).toBe(200);
    } else {
      expect(refreshDraftResponse.status()).toBe(200);
    }
    await expect(page.getByRole("button", { name: "确认并应用到行程" })).toBeEnabled();
    await page.getByRole("button", { name: "确认并应用到行程" }).click();
    await expect(page).toHaveURL(new RegExp(`/trips/${trip.id}/plan$`));
    const afterConfirmation = await request.get(`${api}/api/v1/trips/${trip.id}/plans`);
    expect(await afterConfirmation.json()).toHaveLength(2);

    await page.goto("/advisor");
    await page.getByRole("button", { name: /开始新对话/ }).click();
    await page.getByRole("button", { name: "关联已有旅行" }).click();
    await page.getByLabel("选择要咨询的旅行").selectOption(trip.id);
    await page.getByRole("button", { name: "进入这段旅行的对话" }).click();
    await expect(page.getByRole("region", { name: "顾问对话" })).toBeVisible();
    await page.locator("#advisor-message").fill("看看时间怎么安排比较合适");
    await page.getByRole("button", { name: "发送" }).click();
    await expect(page.getByText("你希望每天安排得轻松一些吗？").first()).toBeVisible({ timeout: 30_000 });
    await page.locator("#advisor-message").fill("轻松一点");
    await page.getByRole("button", { name: "发送" }).click();
    await expect(page.getByText(/排出一版时间建议/).last())
      .toBeVisible({ timeout: 60_000 });
    const staleDraftResponsePromise = page.waitForResponse((response) =>
      response.url().includes("/api/v1/advisor/sessions/")
      && response.url().endsWith("/draft") && response.request().method() === "POST",
    );
    await page.getByRole("button", { name: /按当前选择重新生成/ }).click();
    expect((await staleDraftResponsePromise).status()).toBe(200);
    const latestBeforeRaceResponse = await request.get(`${api}/api/v1/trips/${trip.id}/plans`);
    const latestBeforeRace = (await latestBeforeRaceResponse.json())[0] as {
      id: string;
      nodes: Array<{ id: string }>;
    };
    const concurrentEdit = await request.patch(
      `${api}/api/v1/trips/${trip.id}/plans/${latestBeforeRace.id}/nodes/${latestBeforeRace.nodes[0].id}`,
      { headers: writeHeaders, data: { note: "并发修改：让旧草案失效" } },
    );
    expect(concurrentEdit.ok(), await concurrentEdit.text()).toBeTruthy();
    staleDraftRaceTriggered = true;
    expect(staleDraftRaceTriggered).toBe(true);
    const beforeStaleConfirmation = await request.get(`${api}/api/v1/trips/${trip.id}/plans`);
    expect(await beforeStaleConfirmation.json()).toHaveLength(3);
    await page.getByRole("button", { name: "确认并应用到行程" }).click();
    await expect(page.getByText(/原旅行计划已在其他位置更新/)).toBeVisible();
    expect(await (await request.get(`${api}/api/v1/trips/${trip.id}/plans`)).json()).toHaveLength(3);

    let latestPlan = ((await (await request.get(`${api}/api/v1/trips/${trip.id}/plans`)).json())[0]) as {
      id: string;
      nodes: Array<{ id: string }>;
    };
    expect(latestPlan.nodes).toHaveLength(2);
    for (let nodeIndex = 0; nodeIndex < latestPlan.nodes.length; nodeIndex += 1) {
      const node = latestPlan.nodes[nodeIndex];
      const revised = await request.patch(
        `${api}/api/v1/trips/${trip.id}/plans/${latestPlan.id}/nodes/${node.id}`,
        {
          headers: writeHeaders,
          data: { day: format(start), start_time: "09:00", end_time: "10:30", status: "FIXED" },
        },
      );
      expect(revised.ok(), await revised.text()).toBeTruthy();
      latestPlan = await revised.json() as typeof latestPlan;
    }
    const planCountWithFixedConflict = (await (await request.get(
      `${api}/api/v1/trips/${trip.id}/plans`,
    )).json()).length as number;
    expect(planCountWithFixedConflict).toBe(5);

    const staleSessionResponse = await request.post(`${api}/api/v1/advisor/sessions`, {
      headers: writeHeaders,
      data: { mode: "existing", trip_id: trip.id },
    });
    expect(staleSessionResponse.ok()).toBeTruthy();
    const staleSession = await staleSessionResponse.json() as { id: string };
    const firstTurn = await request.post(`${api}/api/v1/advisor/sessions/${staleSession.id}/messages`, {
      headers: keyedWriteHeaders(),
      data: { content: "看看时间怎么安排比较合适" },
    });
    expect(firstTurn.ok(), await firstTurn.text()).toBeTruthy();
    const pendingSession = await firstTurn.json() as {
      context: Record<string, unknown>;
      messages: Array<{ role: string; payload: Record<string, unknown> | null }>;
    };
    const pendingPayload = pendingSession.messages.at(-1)?.payload;
    expect(pendingSession.context.pending_agent_question).toBeTruthy();
    expect(pendingPayload?.context_revision).toEqual(expect.any(Number));
    expect(pendingPayload?.schedule_recommendation_requested).toBe(true);
    expect(pendingPayload?.pending_task).toBeTruthy();
    expect((pendingPayload?.agent_trace as { status?: string } | undefined)?.status).toBe("NEEDS_USER_INPUT");

    const contextUpdate = await request.patch(`${api}/api/v1/advisor/sessions/${staleSession.id}/context`, {
      headers: writeHeaders,
      data: {
        destination: "成都", start_date: format(start), end_date: format(end),
        budget: null, pace_level: 4, preference_text: "", origin: "", companions: [], hard_constraints: [],
      },
    });
    expect(contextUpdate.ok(), await contextUpdate.text()).toBeTruthy();
    const updatedContext = await contextUpdate.json() as { context: Record<string, unknown> };
    expect(updatedContext.context.pending_task_invalidated).toBe(true);
    const staleAnswer = await request.post(`${api}/api/v1/advisor/sessions/${staleSession.id}/messages`, {
      headers: keyedWriteHeaders(),
      data: { content: "轻松一点" },
    });
    expect(staleAnswer.ok(), await staleAnswer.text()).toBeTruthy();
    const staleResult = await staleAnswer.json() as {
      messages: Array<{ role: string; content: string; payload: Record<string, unknown> | null }>;
    };
    expect(staleResult.messages.at(-1)?.content).toContain("待办规划已作废");
    expect(staleResult.messages.at(-1)?.payload?.agent_trace).toBeNull();
    expect(await (await request.get(`${api}/api/v1/trips/${trip.id}/plans`)).json()).toHaveLength(planCountWithFixedConflict);

    const conflictQuestion = await request.post(`${api}/api/v1/advisor/sessions/${staleSession.id}/messages`, {
      headers: keyedWriteHeaders(),
      data: { content: "看看时间怎么安排比较合适" },
    });
    expect(conflictQuestion.ok(), await conflictQuestion.text()).toBeTruthy();
    expect((await conflictQuestion.json()).messages.at(-1).content).toContain("你希望每天安排得轻松一些吗");
    const conflictAnswer = await request.post(`${api}/api/v1/advisor/sessions/${staleSession.id}/messages`, {
      headers: keyedWriteHeaders(),
      data: { content: "两个预约都固定在上午九点，请检查时间冲突" },
    });
    expect(conflictAnswer.ok(), await conflictAnswer.text()).toBeTruthy();
    const conflictResult = await conflictAnswer.json() as {
      draft: Record<string, unknown> | null;
      messages: Array<{ role: string; content: string; payload: Record<string, unknown> | null }>;
    };
    expect(conflictResult.messages.at(-1)?.content).toContain("固定预约与其他地点的游览或交通时间冲突");
    expect((conflictResult.messages.at(-1)?.payload?.agent_trace as { status?: string } | undefined)?.status)
      .toBe("NEEDS_USER_INPUT");
    expect(conflictResult.draft).toBeNull();
    expect(await (await request.get(`${api}/api/v1/trips/${trip.id}/plans`)).json())
      .toHaveLength(planCountWithFixedConflict);

    const capacityTripResponse = await request.post(`${api}/api/v1/trips`, {
      headers: writeHeaders,
      data: {
        destination: "杭州", start_date: format(start), end_date: format(start), pace_level: 5,
      },
    });
    expect(capacityTripResponse.ok(), await capacityTripResponse.text()).toBeTruthy();
    const capacityTrip = await capacityTripResponse.json() as { id: string };
    for (const [name, latitude, longitude] of [
      ["西湖", 30.25, 120.15],
      ["灵隐寺", 30.24, 120.10],
      ["河坊街", 30.23, 120.17],
      ["浙江省博物馆", 30.26, 120.14],
    ] as const) {
      const saved = await request.post(`${api}/api/v1/trips/${capacityTrip.id}/wishlist`, {
        headers: writeHeaders,
        data: { name, category: "ATTRACTION", latitude, longitude, suggested_duration_min: 120, priority: 3 },
      });
      expect(saved.ok(), await saved.text()).toBeTruthy();
    }
    const capacityPlan = await request.post(
      `${api}/api/v1/trips/${capacityTrip.id}/plan-runs`, { headers: writeHeaders },
    );
    expect(capacityPlan.ok(), await capacityPlan.text()).toBeTruthy();
    expect((await capacityPlan.json()).nodes).toHaveLength(4);
    const slowerPace = await request.patch(`${api}/api/v1/trips/${capacityTrip.id}`, {
      headers: writeHeaders,
      data: { pace_level: 1 },
    });
    expect(slowerPace.ok(), await slowerPace.text()).toBeTruthy();

    await page.goto("/advisor");
    await page.getByRole("button", { name: /开始新对话/ }).click();
    await page.getByRole("button", { name: "关联已有旅行" }).click();
    await page.getByLabel("选择要咨询的旅行").selectOption(capacityTrip.id);
    await page.getByRole("button", { name: "进入这段旅行的对话" }).click();
    await page.locator("#advisor-message").fill("看看时间怎么安排比较合适");
    await page.getByRole("button", { name: "发送" }).click();
    await expect(page.getByText("你希望每天安排得轻松一些吗？").first()).toBeVisible({ timeout: 30_000 });
    await page.locator("#advisor-message").fill("轻松一点");
    await page.getByRole("button", { name: "发送" }).click();
    await expect(page.getByText(/无法全部放入当前日期和节奏/)).toBeVisible({ timeout: 60_000 });
    const capacitySessionResponse = await request.get(`${api}/api/v1/advisor/sessions`);
    const capacitySession = (await capacitySessionResponse.json()).find(
      (item: { trip_id: string }) => item.trip_id === capacityTrip.id,
    ) as { draft: Record<string, unknown> | null } | undefined;
    expect(capacitySession?.draft).toBeNull();
    expect(await (await request.get(`${api}/api/v1/trips/${capacityTrip.id}/plans`)).json())
      .toHaveLength(1);
    await page.locator("#advisor-message").fill("我在考虑西湖和灵隐寺");
    await page.getByRole("button", { name: "发送" }).click();
    await expect(page.getByText("西湖和灵隐寺都在当前行程中。"))
      .toBeVisible({ timeout: 30_000 });
    const capacitySessionId = new URL(page.url()).searchParams.get("sessionId");
    expect(capacitySessionId).toBeTruthy();
    const beforeReferenceRemoval = await request.get(
      `${api}/api/v1/advisor/sessions/${capacitySessionId}`,
    );
    const beforeReferenceState = await beforeReferenceRemoval.json() as {
      candidates: Array<{ id: string; name: string }>;
      context: { selected_candidate_ids: string[] };
    };
    const westLake = beforeReferenceState.candidates.find((item) => item.name === "西湖");
    expect(westLake).toBeTruthy();
    expect(beforeReferenceState.context.selected_candidate_ids).toContain(westLake?.id);
    await page.locator("#advisor-message").fill("把刚才提到的第一个地点移除");
    const referenceTurnResponse = page.waitForResponse((response) =>
      response.request().method() === "POST"
      && new URL(response.url()).pathname.endsWith(`/sessions/${capacitySessionId}/messages`),
    );
    await page.getByRole("button", { name: "发送" }).click();
    const completedReferenceTurn = await referenceTurnResponse;
    expect(completedReferenceTurn.ok(), await completedReferenceTurn.text()).toBeTruthy();
    await expect.poll(async () => {
      const stats = await (await request.get(`${stub}/stats`)).json() as {
        reference_extraction_seen: boolean; reference_history_place_seen: boolean;
      };
      return stats;
    }).toMatchObject({ reference_extraction_seen: true, reference_history_place_seen: true });
    const afterReferenceRemoval = await request.get(
      `${api}/api/v1/advisor/sessions/${capacitySessionId}`,
    );
    const afterReferenceState = await afterReferenceRemoval.json() as {
      candidates: Array<{ id: string; name: string }>;
      context: { selected_candidate_ids: string[] };
      messages: Array<{ role: string; content: string }>;
    };
    expect(afterReferenceState.context.selected_candidate_ids, JSON.stringify(afterReferenceState.messages.slice(-2)))
      .not.toContain(westLake?.id);
    await expect(page.getByText(/已从草案移除西湖/)).toBeVisible({ timeout: 5_000 });
    expect(await (await request.get(`${api}/api/v1/trips/${capacityTrip.id}/plans`)).json())
      .toHaveLength(1);

    const cancellationQuestion = await request.post(
      `${api}/api/v1/advisor/sessions/${capacitySessionId}/messages`,
      { headers: keyedWriteHeaders(), data: { content: "看看时间怎么安排比较合适" } },
    );
    expect(cancellationQuestion.ok(), await cancellationQuestion.text()).toBeTruthy();
    expect((await cancellationQuestion.json()).messages.at(-1).content)
      .toContain("你希望每天安排得轻松一些吗");
    const cancelledTurn = await request.post(
      `${api}/api/v1/advisor/sessions/${capacitySessionId}/messages`,
      { headers: keyedWriteHeaders(), data: { content: "取消" } },
    );
    expect(cancelledTurn.ok(), await cancelledTurn.text()).toBeTruthy();
    const cancelledResult = await cancelledTurn.json() as {
      messages: Array<{ role: string; content: string; payload: Record<string, unknown> | null }>;
    };
    const cancelledAssistant = cancelledResult.messages.at(-1);
    expect(cancelledAssistant?.content).toContain("行程调整已停止");
    expect(cancelledAssistant?.content).toContain("当前旅行数据没有变化");
    expect(cancelledAssistant?.payload?.agent_task).toMatchObject({ status: "CANCELLED" });
    expect(await (await request.get(`${api}/api/v1/trips/${capacityTrip.id}/plans`)).json())
      .toHaveLength(1);

    const mixedTripStart = new Date(start);
    mixedTripStart.setUTCDate(mixedTripStart.getUTCDate() + 20);
    const mixedTripResponse = await request.post(`${api}/api/v1/trips`, {
      headers: writeHeaders,
      data: {
        destination: "成都", start_date: format(mixedTripStart), end_date: format(mixedTripStart),
        pace_level: 3,
      },
    });
    expect(mixedTripResponse.ok(), await mixedTripResponse.text()).toBeTruthy();
    const mixedTrip = await mixedTripResponse.json() as { id: string };
    const mixedSessionResponse = await request.post(`${api}/api/v1/advisor/sessions`, {
      headers: keyedWriteHeaders(),
      data: { mode: "existing", trip_id: mixedTrip.id },
    });
    expect(mixedSessionResponse.ok(), await mixedSessionResponse.text()).toBeTruthy();
    const mixedSession = await mixedSessionResponse.json() as { id: string };
    await page.goto(`/advisor?sessionId=${mixedSession.id}`);
    await page.locator("#advisor-message").fill("帮我找几个适合游览的景点和午餐候选，我选好后再决定时间。");
    const mixedResponsePromise = page.waitForResponse((response) =>
      response.url().includes(`/api/v1/advisor/sessions/${mixedSession.id}/messages`)
      && response.request().method() === "POST",
    );
    await page.getByRole("button", { name: "发送" }).click();
    const mixedResponse = await mixedResponsePromise;
    expect(mixedResponse.ok(), await mixedResponse.text()).toBeTruthy();
    const mixedSessionState = await mixedResponse.json() as {
      payload?: Record<string, unknown>;
      messages: Array<{ role: string; payload: Record<string, unknown> | null }>;
    };
    const mixedAssistantPayload = [...mixedSessionState.messages].reverse()
      .find((item) => item.role === "assistant")?.payload;
    const mixedCandidates = mixedAssistantPayload?.verified_candidates as Array<{
      planning_category?: string;
    }> | undefined;
    expect(mixedCandidates?.map((candidate) => candidate.planning_category)).toContain("ATTRACTION");
    expect(mixedCandidates?.map((candidate) => candidate.planning_category)).toContain("FOOD");
    await expect(page.getByRole("heading", { name: "多类型旅行候选" })).toBeVisible();
    const allMixedCards = page.locator(".advisor-live-candidate");
    const mixedCardCount = await allMixedCards.count();
    for (let index = 0; index < mixedCardCount; index += 1) {
      const card = allMixedCards.nth(index);
      const name = await card.locator("strong").innerText();
      if (name.includes("成都大熊猫繁育研究基地") || name.includes("模拟川味餐馆")) continue;
      const removeSelection = card.getByRole("button", { name: "已加入 ✓" });
      if (await removeSelection.count()) await removeSelection.click();
    }
    const attractionCard = page.locator(".advisor-live-candidate")
      .filter({ hasText: "成都大熊猫繁育研究基地" }).first();
    const lunchCard = page.locator(".advisor-live-candidate")
      .filter({ hasText: "模拟川味餐馆" }).first();
    await expect(attractionCard).toBeVisible();
    await expect(lunchCard).toBeVisible();
    const addAttraction = attractionCard.getByRole("button", { name: "加入行程" });
    if (await addAttraction.count()) await addAttraction.click();
    const addLunch = lunchCard.getByRole("button", { name: "加入午餐" });
    if (await addLunch.count()) await addLunch.click();
    const selectedAttractionCard = page.locator(".advisor-live-candidate").filter({
      has: page.getByLabel("安排日期"),
    }).first();
    const selectedLunchCard = page.locator(".advisor-live-candidate").filter({
      has: page.getByLabel("用餐日期"),
    }).first();
    await selectedAttractionCard.getByLabel("安排日期").selectOption(format(mixedTripStart));
    await selectedAttractionCard.getByLabel("开始时间").fill("09:00");
    await selectedLunchCard.getByLabel("用餐日期").selectOption(format(mixedTripStart));
    await selectedLunchCard.getByLabel("用餐时间").fill("12:00");
    await expect(page.getByRole("button", { name: "检查并预览行程（2）" })).toBeEnabled();
    const mixedDraftPromise = page.waitForResponse((response) =>
      response.url().includes(`/api/v1/advisor/sessions/${mixedSession.id}/draft`)
      && response.request().method() === "POST",
    );
    await page.getByRole("button", { name: "检查并预览行程（2）" }).click();
    const mixedDraftResponse = await mixedDraftPromise;
    expect(mixedDraftResponse.ok(), await mixedDraftResponse.text()).toBeTruthy();
    const mixedDraftSession = await mixedDraftResponse.json() as {
      draft: { stops: Array<{ planning_category: string; meal_type?: string | null; start_time: string }> };
    };
    expect(mixedDraftSession.draft.stops).toEqual(expect.arrayContaining([
      expect.objectContaining({ planning_category: "ATTRACTION", start_time: "09:00:00" }),
      expect.objectContaining({ planning_category: "FOOD", meal_type: "LUNCH", start_time: "12:00:00" }),
    ]));
    expect(await (await request.get(`${api}/api/v1/trips/${mixedTrip.id}/plans`)).json()).toHaveLength(0);

    expect(browserErrors).toEqual([]);
    expect(unexpectedHttpErrors).toEqual([]);
  } catch (error) {
    testBodyFailed = true;
    throw error;
  } finally {
    if (registered && csrf) {
      try {
        const cleanup = await request.delete(`${api}/api/v1/account`, {
          headers: { "X-CSRF-Token": decodeURIComponent(csrf) },
        });
        expect(cleanup.status()).toBe(204);
      } catch (error) {
        if (!testBodyFailed) throw error;
      }
    }
  }
});
