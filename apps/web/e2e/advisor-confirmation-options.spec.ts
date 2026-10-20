import { expect, test } from "@playwright/test";

test("已明确安排时用结构化选项确认，并将答案交回同一顾问任务", async ({ page }) => {
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
  let submittedAnswer = "";
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
      submittedAnswer = (request.postDataJSON() as { content: string }).content;
      const updated = {
        ...session,
        messages: [...messages,
          { id: "user-2", role: "user", content: submittedAnswer, payload: null, created_at: now },
          { id: "assistant-2", role: "assistant",
            content: "已记录你的选择；检查行程调整预览后，再确认应用到正式行程。",
            payload: { response_source: "TEMPLATE" }, created_at: now },
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
  await expect.poll(() => submittedAnswer).toBe("确认，按当前选择加入安排");
  await expect(page.getByText("已记录你的选择；检查行程调整预览后，再确认应用到正式行程。", { exact: true })).toBeVisible();
  expect(confirmed).toBe(false);
});
