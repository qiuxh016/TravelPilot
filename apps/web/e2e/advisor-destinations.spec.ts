import { expect, test } from "@playwright/test";

const api = process.env.TRAVELPILOT_TEST_API_URL ?? "http://localhost:8000";

test("未定目的地候选可查看地图证据并将所选城市写入顾问会话", async ({ page, request }, testInfo) => {
  test.skip(
    process.env.TRAVELPILOT_TEST_DEV_MAILBOX !== "1",
    "requires an isolated API configured with the development email outbox",
  );
  const browserErrors: string[] = [];
  page.on("pageerror", (error) => browserErrors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error") browserErrors.push(message.text());
  });
  const email = `advisor-destination-${Date.now()}@example.invalid`;
  const password = "travelpilot-test-2026";
  let csrf = "";
  try {
    expect((await request.post(`${api}/api/v1/auth/register`, { data: { email, password } })).ok()).toBeTruthy();
    const mailbox = await request.get(`${api}/api/v1/auth/dev-mailbox/${email}`);
    const token = new URL((await mailbox.json()).link, api).searchParams.get("token")!;
    expect(token).toBeTruthy();
    expect((await request.post(`${api}/api/v1/auth/verify-email`, { data: { token } })).ok()).toBeTruthy();
    expect((await request.post(`${api}/api/v1/auth/login`, { data: { email, password } })).ok()).toBeTruthy();

    const cookies = (await request.storageState()).cookies;
    csrf = cookies.find((cookie) => cookie.name === "travelpilot_csrf")?.value ?? "";
    await page.context().addCookies(cookies);
    const created = await request.post(`${api}/api/v1/advisor/sessions`, {
    headers: { "X-CSRF-Token": decodeURIComponent(csrf) },
    data: { mode: "discover", preference_text: "", pace_level: 3 },
  });
  expect(created.status()).toBe(201);
  const session = await created.json();
  const liveMode = process.env.TRAVELPILOT_LIVE_DESTINATION === "1";
  let submittedCity: string | undefined;
  const sampleRecommendation = {
    city: "泉州", matched_interests: ["历史文化", "地方美食"], place_count: 4,
    reasons: ["地图检索到与你关注的历史文化相关地点"], provider: "amap", retrieved_at: new Date().toISOString(),
    places: [{ name: "开元寺", category: "风景名胜", address: "泉州市鲤城区", provider: "amap",
      provider_id: "test-quanzhou-temple", latitude: 24.95, longitude: 118.59, popularity_score: 4.8 }],
  };
  if (liveMode) {
    await page.route("http://localhost:8000/**", (route) => {
      const target = new URL(route.request().url());
      target.port = new URL(api).port;
      return route.continue({ url: target.toString() });
    });
  } else {
    await page.route(`${api}/api/v1/advisor/sessions/${session.id}/destinations`, async (route) => {
      submittedCity = route.request().postDataJSON()?.city;
      const updated = { ...session, messages: [...session.messages, {
        id: crypto.randomUUID(), role: "assistant", content: "按地图证据整理候选。",
        payload: {
          event: "DESTINATION_RECOMMENDATIONS", recommendations: [sampleRecommendation],
          search_count: 5, search_scope_complete: false,
          agent_task: {
            task_id: "destination-fixture-task", task_type: "DISCOVERY", status: "WAITING_USER",
            goal: "帮我找一些适合旅行的目的地", context_revision: 0, turn_number: 1,
            answers: [], question: "你更想体验海滨还是地方美食？",
            action_summary: "先收集一个关键偏好再继续搜索",
          },
          agent_trace: {
            status: "NEEDS_USER_INPUT", decision_source: "MODEL",
            decision_summary: "先查有代表性的候选，再根据结果收敛",
            transitions: [{ tool: "search_destination_places", status: "VERIFIED" }],
            execution_metrics: { logical_steps: 1, tool_attempts: 1, output_tokens: 9 },
          },
        },
        created_at: new Date().toISOString(),
      }] };
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(updated) });
    });
  }

    await page.goto(`/advisor?sessionId=${session.id}`);
    await expect(page).toHaveURL(new RegExp(`/advisor\\?sessionId=${session.id}`));
    await expect(page).toHaveTitle("TravelPilot");
    const inspirationButton = page.getByRole("button", { name: "先给我一些目的地灵感" });
    await expect(inspirationButton).toBeEnabled();
    await expect(page.getByText(/可以指定任意城市/)).toBeVisible();
    if (!liveMode) await page.getByLabel("想探索哪座城市？（可选）").fill("泉州");
    await inspirationButton.click();
    if (liveMode) {
      await expect(page.getByText("候选来自高德地点检索")).toBeVisible();
      await expect(page.locator(".advisor-destination-grid article")).toHaveCount(3);
    } else {
      await expect(page.getByRole("heading", { name: "泉州", exact: true })).toBeVisible();
      await expect(page.getByText("开元寺")).toBeVisible();
      await expect(page.getByText("地图评分 4.8")).toBeVisible();
      await expect(page.getByText(/本轮查询 5 组/)).toBeVisible();
      await expect(page.getByText(/不代表已比较全部城市/)).toBeVisible();
      await expect(page.getByRole("region", { name: "等待你补充" })).toContainText("你更想体验海滨还是地方美食？");
      await expect(page.getByText("目的地探索")).toBeVisible();
      expect(submittedCity).toBe("泉州");
    }
    if (process.env.TRAVELPILOT_QA_SCREENSHOT) {
      const screenshotPath = process.env.TRAVELPILOT_QA_SCREENSHOT.replace(
        ".png", `-${testInfo.project.name}.png`,
      );
      await page.screenshot({ path: screenshotPath, fullPage: true });
    }
    await page.setViewportSize({ width: 390, height: 844 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
    await page.setViewportSize({ width: 1440, height: 1000 });
    const chosenCity = (await page.locator(".advisor-destination-grid article h3").first().textContent())?.trim();
    expect(chosenCity).toBeTruthy();
    await page.locator(".advisor-destination-grid article").first().getByRole("button", { name: "选择这个城市" }).click();
    await expect(page.locator(".advisor-action-notice")).toContainText(new RegExp(`${chosenCity}\\s*已设为当前目的地`));
    const persisted = await request.get(`${api}/api/v1/advisor/sessions/${session.id}`);
    expect((await persisted.json()).destination).toBe(chosenCity);
    expect(browserErrors).toEqual([]);
  } finally {
    if (csrf) {
      await request.delete(`${api}/api/v1/account`, {
        headers: { "X-CSRF-Token": decodeURIComponent(csrf) },
      });
    }
  }
});
