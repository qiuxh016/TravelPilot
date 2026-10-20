import { expect, test } from "@playwright/test";

test("选择目的地后收起候选卡，并由顾问接续询问偏好", async ({ page }) => {
  const sessionId = "destination-choice-session";
  const now = "2026-10-20T08:00:00Z";
  const initialSession = {
    id: sessionId, mode: "discover", trip_id: null, destination: null,
    start_date: null, end_date: null, budget: null, pace_level: 3,
    preference_text: "想找适合周末去的地方", context: {
      revision: 0, selected_candidate_ids: [], current_plan: null,
      confirmed_memories: [], planning_inputs: { origin: "", companions: [], hard_constraints: [] },
    },
    candidates: [], draft: null, draft_version: 0, draft_stale: false,
    status: "ACTIVE", confirmed_trip_id: null, confirmed_plan_id: null,
    created_at: now, updated_at: now,
    messages: [{
      id: "destination-recommendation-1", role: "assistant", content: "这里有几个城市可以考虑。",
      payload: {
        event: "DESTINATION_RECOMMENDATIONS",
        recommendations: [{ city: "泉州", matched_interests: ["历史文化", "地方美食"], place_count: 4,
          reasons: ["地图找到了历史与美食地点"], provider: "amap", retrieved_at: now,
          places: [{ name: "开元寺", category: "寺庙", address: "泉州市鲤城区", provider: "amap",
            provider_id: "quanzhou-kaiyuan", latitude: 24.95, longitude: 118.59, popularity_score: 4.8 }],
        }], search_count: 4, search_scope_complete: true,
      }, created_at: now,
    }],
  };
  type MockMessage = { id: string; role: string; content: string; payload: unknown; created_at: string };
  type MockSession = Omit<typeof initialSession, "destination" | "messages"> & {
    destination: string | null;
    messages: MockMessage[];
  };
  let session: MockSession = initialSession;
  let submittedFollowUp = "";
  let savedDestination = "";

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
    if (url.pathname === "/api/v1/advisor/sessions" && request.method() === "GET") {
      return route.fulfill({ json: [session] });
    }
    if (url.pathname === `/api/v1/advisor/sessions/${sessionId}/context` && request.method() === "PATCH") {
      savedDestination = request.postDataJSON().destination;
      session = { ...session, destination: savedDestination, updated_at: new Date().toISOString() };
      return route.fulfill({ json: session });
    }
    if (url.pathname === `/api/v1/advisor/sessions/${sessionId}/progress`) return route.fulfill({ json: [] });
    if (url.pathname === `/api/v1/advisor/sessions/${sessionId}/messages` && request.method() === "POST") {
      submittedFollowUp = request.postDataJSON().content;
      session = { ...session, messages: [...session.messages,
        { id: "user-choice", role: "user", content: "我选了泉州，请继续帮我规划。", payload: null, created_at: now },
        { id: "assistant-preference-question", role: "assistant", content: "好的，接着规划。你更喜欢历史文化还是自然风景？",
          payload: { agent_task: {
            task_id: "destination-preference-task", task_type: "DISCOVERY", status: "WAITING_USER",
            goal: "按用户偏好继续探索泉州", context_revision: 1, turn_number: 1,
            answers: [], question: "你更喜欢历史文化还是自然风景？",
            confirmation_options: [
              { label: "历史文化", answer: "我偏爱历史文化" },
              { label: "自然风景", answer: "我偏爱自然风景" },
            ],
          } }, created_at: now },
      ] };
      return route.fulfill({ json: session });
    }
    if (url.pathname === `/api/v1/advisor/sessions/${sessionId}` && request.method() === "GET") {
      return route.fulfill({ json: session });
    }
    return route.fulfill({ json: {} });
  });

  await page.goto(`/advisor?sessionId=${sessionId}`);
  await page.getByRole("heading", { name: "泉州", exact: true }).waitFor();
  await page.locator(".advisor-destination-grid article").getByRole("button", { name: "选择这个城市" }).click();

  expect(savedDestination).toBe("泉州");
  await expect(page.locator(".advisor-destination-results")).toHaveCount(0);
  expect(submittedFollowUp).toContain("我选了泉州");
  expect(submittedFollowUp).toBe("我选了泉州，请继续帮我规划。");
  await expect(page.getByText("好的，接着规划。你更喜欢历史文化还是自然风景？")).toBeVisible();
  const question = page.getByRole("region", { name: "等待你补充" });
  await expect(question.getByRole("button", { name: "历史文化" })).toBeVisible();
  await expect(question.getByRole("button", { name: "自然风景" })).toBeVisible();
});
