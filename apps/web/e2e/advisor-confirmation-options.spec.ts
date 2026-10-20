import { expect, test } from "@playwright/test";

test("信息确认和继续排程分成不同结构化选项", async ({ page }) => {
  const sessionId = "confirmation-options-session";
  const now = "2026-10-20T08:00:00Z";
  const messages = [
    { id: "user-1", role: "user", content: "鑫丰特色餐饮", payload: null, created_at: now },
    {
      id: "assistant-1", role: "assistant",
      content: "好的，已整理为第 1 天午餐安排。需要我把鑫丰特色餐饮作为第 1 天午餐的待确认安排吗？",
      payload: { agent_task: {
        task_id: "meal-confirmation-task", task_type: "SCHEDULE", status: "WAITING_USER",
        context_revision: 0, turn_number: 2, answers: ["鑫丰特色餐饮"],
        question: "需要我把鑫丰特色餐饮作为第 1 天午餐的待确认安排吗？",
        confirmation_options: [
          { label: "确认，加入安排", answer: "确认，按当前选择加入安排" },
          { label: "返回调整", answer: "先不确认，我想调整这项安排" },
          { label: "暂不安排", answer: "暂不安排这项内容" },
        ],
      } }, created_at: now,
    },
  ];
  const session = {
    id: sessionId, mode: "existing", trip_id: "trip-1", destination: "梅州",
    start_date: "2026-10-20", end_date: "2026-10-21", budget: null, pace_level: 3,
    preference_text: "", candidates: [],
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
  const choiceCard = page.getByRole("region", { name: "等待你补充" });
  await expect(choiceCard).toBeVisible();
  await expect(choiceCard).toContainText("鑫丰特色餐饮");
  await expect(choiceCard.getByRole("button", { name: "确认，加入安排" })).toBeVisible();
  await choiceCard.getByRole("button", { name: "确认，加入安排" }).click();
  await expect.poll(() => submittedAnswers.at(-1)).toBe("确认，按当前选择加入安排");
  const continueCard = page.getByRole("region", { name: "等待你补充" });
  await expect(continueCard.getByRole("button", { name: "继续，生成待确认建议" })).toBeVisible();
  await continueCard.getByRole("button", { name: "继续，生成待确认建议" }).click();
  await expect.poll(() => submittedAnswers.at(-1)).toBe("好的，继续生成待确认建议");
  await expect(page.getByText("已继续生成待确认建议。", { exact: true })).toBeVisible();
  expect(confirmed).toBe(false);
});
