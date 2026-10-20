import { expect, test } from "@playwright/test";

test("日期建议显示为可点选方案，继续排程不代表保存确认", async ({ page }) => {
  const sessionId = "confirmation-options-session";
  const now = "2026-10-20T08:00:00Z";
  const messages = [
    { id: "user-1", role: "user", content: "鑫丰特色餐饮", payload: null, created_at: now },
    {
      id: "assistant-1", role: "assistant",
      content: "鼓浪屿与文化线适合放在同一天。要不要我把这条线放到10/23，再帮你安排10/24和10/25？",
      payload: { agent_task: {
        task_id: "route-date-confirmation-task", task_type: "SCHEDULE", status: "WAITING_USER",
        goal: "安排鼓浪屿文化线和后续日期",
        context_revision: 0, turn_number: 2, answers: ["鼓浪屿文化线"],
        question: "要不要我把这条线放到10/23，再帮你安排10/24和10/25？",
        confirmation_options: [
          { label: "可以，按这个时间继续", answer: "可以，按你刚才建议的日期和时间继续安排" },
          { label: "换个日期或时间", answer: "我想调整刚才建议的日期或时间，先不要按原时间安排" },
          { label: "暂不安排", answer: "暂不安排这项内容" },
        ],
      } }, created_at: now,
    },
  ];
  const session = {
    id: sessionId, mode: "existing", trip_id: "trip-1", destination: "梅州",
    start_date: "2026-10-20", end_date: "2026-10-21", budget: null, pace_level: 3,
    preference_text: "", candidates: [{
      id: "guide-place-1", provider: "amap", provider_id: "place-1", name: "梅州学宫",
      address: "梅州市梅江区", category: "博物馆", planning_category: "ATTRACTION",
      latitude: 24.29, longitude: 116.12, duration_min: 60, opening_hours: null,
      confidence: 0.9, suggestion: null, verification_status: "VERIFIED", verified_at: now,
      guide_references: [{ source_id: "guide-1", title: "梅州一日游攻略", place_name: "梅州学宫",
        category: "ATTRACTION", guide_claim: "周一闭馆，其余时间开放", evidence_status: "IMPORTED_GUIDE" }],
    }],
    context: { revision: 0, selected_candidate_ids: [], current_plan: null,
      confirmed_memories: [], planning_inputs: { origin: "", companions: [], hard_constraints: [] } },
    draft: null, draft_version: 0, draft_stale: false, status: "ACTIVE",
    confirmed_trip_id: null, confirmed_plan_id: null,
    created_at: now, updated_at: now, messages,
  };
  const submittedAnswers: string[] = [];
  let confirmed = false;

  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname === "/api/v1/auth/me") {
      return route.fulfill({ json: {
        id: "user-1", email: "test@example.invalid", username: "tester",
        created_at: now, display_name: "测试用户", home_city: "", bio: "", has_avatar: false,
      } });
    }
    if (url.pathname === "/api/v1/trips") return route.fulfill({ json: [] });
    if (url.pathname === "/api/v1/advisor/status") {
      return route.fulfill({ json: {
        llm_status: "CONFIGURED", model_name: "test-model", output_token_limit: 1200,
        upstream_status: "CHECKED_PER_REQUEST",
      } });
    }
    if (url.pathname === `/api/v1/advisor/sessions/${sessionId}` && request.method() === "GET") {
      return route.fulfill({ json: session });
    }
    if (url.pathname === "/api/v1/advisor/sessions" && request.method() === "GET") {
      return route.fulfill({ json: [session] });
    }
    if (url.pathname.endsWith("/progress")) return route.fulfill({ json: [] });
    if (url.pathname.endsWith("/messages") && request.method() === "POST") {
      const submittedAnswer = (request.postDataJSON() as { content: string }).content;
      submittedAnswers.push(submittedAnswer);
      const assistantMessage = submittedAnswers.length === 1
        ? { id: "assistant-2", role: "assistant", content: "日期和住宿信息已核对。", payload: { agent_task: {
          task_id: "schedule-task", task_type: "SCHEDULE", status: "WAITING_USER",
          goal: "按日期安排每天的游览顺序",
          context_revision: 0, turn_number: 3, answers: ["鑫丰特色餐饮", "确认，按当前选择加入安排"],
          question: "要我现在按日期把每天的游览顺序排出来吗？",
          confirmation_options: [
            { label: "继续，生成待确认建议", answer: "好的，继续生成待确认建议" },
            { label: "补充或调整要求", answer: "我想先补充或调整要求" },
            { label: "暂不处理", answer: "暂不处理这项任务" },
          ],
        } }, created_at: now }
        : { id: "assistant-3", role: "assistant", content: "已继续生成待确认建议。", payload: { response_source: "TEMPLATE" }, created_at: now };
      const updated = {
        ...session,
        messages: [...messages,
          ...submittedAnswers.flatMap((answer, index) => [
            { id: `user-${index + 2}`, role: "user", content: answer, payload: null, created_at: now },
            ...(index === submittedAnswers.length - 1 ? [assistantMessage] : []),
          ]),
        ],
      };
      return route.fulfill({ json: updated });
    }
    if (url.pathname.endsWith("/confirm")) {
      confirmed = true;
      return route.fulfill({ status: 500, json: { detail: "确认接口不应在选择卡点击时调用" } });
    }
    return route.fulfill({ json: {} });
  });

  await page.goto(`/advisor?sessionId=${sessionId}`);
  const chatPanel = page.locator(".advisor-chat");
  await expect(chatPanel).toBeVisible();
  const desktopLayout = (await page.evaluate(() => window.innerWidth)) > 900;
  expect(await chatPanel.evaluate((element) => getComputedStyle(element).position))
    .toBe(desktopLayout ? "sticky" : "static");
  if (desktopLayout) {
    expect(await chatPanel.evaluate((element) => element.getBoundingClientRect().height)).toBeGreaterThan(500);
    expect(await page.locator(".advisor-messages").evaluate((element) => getComputedStyle(element).maxHeight)).toBe("none");
  }
  const choiceCard = page.getByRole("region", { name: "等待你补充" });
  await expect(choiceCard).toBeVisible();
  await expect(page.getByText("鑫丰特色餐饮", { exact: true }).first()).toBeVisible();
  await expect(choiceCard).toContainText("当前任务：安排鼓浪屿文化线和后续日期");
  await expect(page.getByText("攻略线索：《梅州一日游攻略》提到：周一闭馆，其余时间开放 · 尚未核验")).toBeVisible();
  await expect(choiceCard.getByRole("button", { name: "可以，按这个时间继续" })).toBeVisible();
  await expect(choiceCard.getByRole("button", { name: "自定义回答" })).toHaveCount(0);
  await choiceCard.getByRole("button", { name: "可以，按这个时间继续" }).click();
  await expect.poll(() => submittedAnswers.at(-1)).toBe("可以，按你刚才建议的日期和时间继续安排");
  const continueCard = page.getByRole("region", { name: "等待你补充" });
  await expect(continueCard.getByRole("button", { name: "继续，生成待确认建议" })).toBeVisible();
  await continueCard.getByRole("button", { name: "继续，生成待确认建议" }).click();
  await expect.poll(() => submittedAnswers.at(-1)).toBe("好的，继续生成待确认建议");
  await expect(page.getByText("已继续生成待确认建议。", { exact: true })).toBeVisible();
  expect(confirmed).toBe(false);
});
