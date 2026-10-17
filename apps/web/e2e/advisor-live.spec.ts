import { expect, test } from "@playwright/test";

const api = process.env.TRAVELPILOT_TEST_API_URL ?? "http://localhost:8000";

test.skip(!process.env.TRAVELPILOT_LIVE_LLM, "真实模型调用需显式设置 TRAVELPILOT_LIVE_LLM=1");

test("真实模型识别地点，经高德核验后仅在用户确认时保存行程", async ({ page, request }) => {
  test.setTimeout(120_000);
  const email = `advisor-live-${Date.now()}@example.invalid`;
  const password = "travelpilot-test-2026";
  const registered = await request.post(`${api}/api/v1/auth/register`, { data: { email, password } });
  expect(registered.ok()).toBeTruthy();
  const mailbox = await request.get(`${api}/api/v1/auth/dev-mailbox/${email}`);
  const token = new URL((await mailbox.json()).link).searchParams.get("token")!;
  expect((await request.post(`${api}/api/v1/auth/verify-email`, { data: { token } })).ok()).toBeTruthy();
  const login = await request.post(`${api}/api/v1/auth/login`, { data: { email, password } });
  expect(login.ok()).toBeTruthy();
  const cookies = (await request.storageState()).cookies;
  const csrf = cookies.find((cookie) => cookie.name === "travelpilot_csrf")?.value ?? "";
  await page.context().addCookies(cookies);
  await page.route("http://localhost:8000/**", (route) => {
    const target = new URL(route.request().url());
    target.port = new URL(api).port;
    return route.continue({ url: target.toString() });
  });

  try {
    await page.goto("/advisor");
    await page.getByLabel("目的地").fill("成都");
    await page.getByRole("button", { name: "创建会话" }).click();
    await expect(page.getByRole("region", { name: "顾问对话" })).toBeVisible();
    await page.locator("#advisor-message").fill("我想去宽窄巷子");
    await page.getByRole("button", { name: "发送" }).click();
    await expect(page.getByText("高德地图已核验").first()).toBeVisible({ timeout: 90_000 });
    await expect(page.locator(".advisor-message-source").last()).toContainText("模型已理解");

    const sessionId = new URL(page.url()).searchParams.get("sessionId");
    const sessionsResponse = await request.get(`${api}/api/v1/advisor/sessions`);
    expect(sessionsResponse.ok()).toBeTruthy();
    const sessions = await sessionsResponse.json() as { id: string; messages: { role: string; payload?: { response_source?: string; extraction_source?: string; llm_tokens?: number; llm_output_tokens?: number } | null }[]; candidates: { provider: string; verification_status: string }[] }[];
    const session = sessions.find((item) => item.id === sessionId) ?? sessions[0];
    expect(session).toBeTruthy();
    const assistantReply = session.messages.filter((item) => item.role === "assistant").at(-1);
    expect(assistantReply?.payload?.extraction_source).toBe("MODEL");
    expect(["MODEL", "TEMPLATE"]).toContain(assistantReply?.payload?.response_source);
    expect(assistantReply?.payload?.llm_output_tokens ?? 0).toBeLessThanOrEqual(1200);
    expect(session.candidates.some((item) => item.provider === "amap" && item.verification_status === "VERIFIED")).toBeTruthy();
    const tripsResponse = await request.get(`${api}/api/v1/trips`);
    expect(await tripsResponse.json()).toHaveLength(0);

    const candidateCheckboxes = page.locator(".advisor-live-candidate input[type=checkbox]");
    const candidateCount = await candidateCheckboxes.count();
    expect(candidateCount).toBeGreaterThan(0);
    for (let index = 1; index < candidateCount; index += 1) {
      if (await candidateCheckboxes.nth(index).isChecked()) await candidateCheckboxes.nth(index).uncheck();
    }
    await page.getByRole("button", { name: /生成草案/ }).click();
    await expect(page.getByText("第 1 版草案", { exact: true })).toBeVisible();
    await expect(page.getByText("确认后写入正式旅行")).toBeVisible();
    await page.getByRole("button", { name: "确认并保存行程" }).click();
    await expect(page).toHaveURL(/\/trips\/[^/]+\/plan$/);
    await expect(page.getByRole("heading", { name: "把想去的地方，排成一天" })).toBeVisible();
  } finally {
    await request.delete(`${api}/api/v1/account`, {
      headers: { "X-CSRF-Token": decodeURIComponent(csrf) },
    });
  }
});

test("目的地未定时保留偏好并追问城市，不虚构候选或计划", async ({ page, request }) => {
  test.setTimeout(120_000);
  const email = `advisor-discover-${Date.now()}@example.invalid`;
  const password = "travelpilot-test-2026";
  const registered = await request.post(`${api}/api/v1/auth/register`, { data: { email, password } });
  expect(registered.ok()).toBeTruthy();
  const mailbox = await request.get(`${api}/api/v1/auth/dev-mailbox/${email}`);
  const token = new URL((await mailbox.json()).link).searchParams.get("token")!;
  expect((await request.post(`${api}/api/v1/auth/verify-email`, { data: { token } })).ok()).toBeTruthy();
  const login = await request.post(`${api}/api/v1/auth/login`, { data: { email, password } });
  expect(login.ok()).toBeTruthy();
  const cookies = (await request.storageState()).cookies;
  const csrf = cookies.find((cookie) => cookie.name === "travelpilot_csrf")?.value ?? "";
  await page.context().addCookies(cookies);
  await page.route("http://localhost:8000/**", (route) => {
    const target = new URL(route.request().url());
    target.port = new URL(api).port;
    return route.continue({ url: target.toString() });
  });

  try {
    await page.goto("/advisor");
    await page.getByRole("button", { name: "先聊旅行想法" }).click();
    await page.getByRole("button", { name: "创建会话" }).click();
    await expect(page.getByRole("region", { name: "顾问对话" })).toBeVisible();
    await page.locator("#advisor-message").fill("我还没定目的地，喜欢自然风景和川菜，也不想去博物馆。你能先推荐几个城市吗？");
    await page.getByRole("button", { name: "发送" }).click();
    await expect(page.locator(".advisor-message-source").last()).toContainText(/模型已理解|模型暂不可用，已切换本地识别/, { timeout: 90_000 });

    const sessionId = new URL(page.url()).searchParams.get("sessionId");
    const sessionsResponse = await request.get(`${api}/api/v1/advisor/sessions`);
    expect(sessionsResponse.ok()).toBeTruthy();
    const sessions = await sessionsResponse.json() as { id: string; destination: string; context: { planning_state: { missing_required: string[] } }; messages: { role: string; content: string; payload?: { response_source?: string; extraction_source?: string; llm_output_tokens?: number } | null }[]; candidates: unknown[] }[];
    const session = sessions.find((item) => item.id === sessionId) ?? sessions[0];
    expect(session).toBeTruthy();
    expect(session?.destination).toBe("");
    expect(session?.context.planning_state.missing_required).toContain("destination");
    expect(session?.candidates).toHaveLength(0);
    const assistantReply = session?.messages.filter((item) => item.role === "assistant").at(-1);
    expect(["MODEL", "TEMPLATE"]).toContain(assistantReply?.payload?.extraction_source);
    expect(assistantReply?.payload?.llm_output_tokens ?? 0).toBeLessThanOrEqual(1200);
    expect(assistantReply?.content).toMatch(/城市|目的地/);
    expect(await request.get(`${api}/api/v1/trips`).then((response) => response.json())).toHaveLength(0);
  } finally {
    await request.delete(`${api}/api/v1/account`, {
      headers: { "X-CSRF-Token": decodeURIComponent(csrf) },
    });
  }
});
