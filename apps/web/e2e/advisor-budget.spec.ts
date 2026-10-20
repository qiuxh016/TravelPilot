import { expect, test } from "@playwright/test";

test("会话模型额度耗尽时，用户可以明确继续或取消待处理任务", async ({ page }) => {
  const sessionId = "budget-session";
  const messages = [
    { id: "user-1", role: "user", content: "帮我规划一段两日旅行", payload: null, created_at: "2026-10-18T00:00:00Z" },
    {
      id: "assistant-1", role: "assistant", content: "本次对话已达到模型输出额度。若要继续刚才的任务，请明确点击下方按钮；这会增加一轮额度。",
      payload: { response_source: "TEMPLATE", budget_continuation: {
        required: true, action_label: "继续本次对话", prompt: "继续本次对话",
        grant_output_tokens: 1200, used_output_tokens: 12000, session_output_limit: 12000,
      } }, created_at: "2026-10-18T00:01:00Z",
    },
  ];
  const session = {
    id: sessionId, mode: "discover", trip_id: null, destination: "", start_date: null, end_date: null,
    budget: null, pace_level: 3, preference_text: "", candidates: [],
    context: { revision: 0, selected_candidate_ids: [], current_plan: null, confirmed_memories: [], planning_inputs: { origin: "", companions: [], hard_constraints: [] } },
    draft: null, draft_version: 0, draft_stale: false, status: "ACTIVE", confirmed_trip_id: null,
    confirmed_plan_id: null, created_at: "2026-10-18T00:00:00Z", updated_at: "2026-10-18T00:01:00Z", messages,
  };
  let submittedContent = "";
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname === "/api/v1/auth/me") {
      return route.fulfill({ json: { id: "user-1", email: "test@example.invalid", username: "tester", created_at: "2026-10-18T00:00:00Z", display_name: "测试用户", home_city: "", bio: "", has_avatar: false } });
    }
    if (url.pathname === "/api/v1/trips") return route.fulfill({ json: [] });
    if (url.pathname === "/api/v1/advisor/status") {
      return route.fulfill({ json: { llm_status: "CONFIGURED", model_name: "test-model", output_token_limit: 1200, upstream_status: "CHECKED_PER_REQUEST" } });
    }
    if (url.pathname === "/api/v1/advisor/sessions" && request.method() === "GET") return route.fulfill({ json: [session] });
    if (url.pathname.endsWith("/progress")) return route.fulfill({ json: [] });
    if (url.pathname.endsWith("/messages") && request.method() === "POST") {
      submittedContent = (request.postDataJSON() as { content: string }).content;
      const responseText = submittedContent === "取消继续" ? "已取消本次顾问请求，旅行数据未改变。" : "已收到继续请求。";
      const updated = { ...session, messages: [...messages, { id: "user-2", role: "user", content: submittedContent, payload: null, created_at: "2026-10-18T00:02:00Z" }, { id: "assistant-2", role: "assistant", content: responseText, payload: { response_source: "TEMPLATE" }, created_at: "2026-10-18T00:03:00Z" }] };
      return route.fulfill({ json: updated });
    }
    return route.fulfill({ json: {} });
  });

  await page.goto(`/advisor?sessionId=${sessionId}`);
  const consent = page.getByRole("region", { name: "模型额度确认" });
  await expect(consent).toBeVisible();
  await expect(consent).toContainText("已用 12000 / 12000 个输出 token");
  await page.locator(".advisor-messages").evaluate((element) => {
    element.scrollTop = element.scrollHeight;
  });
  await consent.scrollIntoViewIfNeeded();
  if (process.env.TRAVELPILOT_BUDGET_SCREENSHOT_PATH) {
    await page.screenshot({ path: process.env.TRAVELPILOT_BUDGET_SCREENSHOT_PATH });
  }
  await consent.getByRole("button", { name: "继续本次对话" }).click();
  await expect.poll(() => submittedContent).toBe("继续本次对话");
  await expect(page.getByText("已收到继续请求。", { exact: true })).toBeVisible();

  submittedContent = "";
  await page.goto(`/advisor?sessionId=${sessionId}`);
  const cancelConsent = page.getByRole("region", { name: "模型额度确认" });
  await expect(cancelConsent).toBeVisible();
  await cancelConsent.getByRole("button", { name: "取消", exact: true }).click();
  await expect.poll(() => submittedContent).toBe("取消继续");
  await expect(page.getByText("已取消本次顾问请求，旅行数据未改变。", { exact: true })).toBeVisible();
});
