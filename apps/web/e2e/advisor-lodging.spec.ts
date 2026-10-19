import { expect, test } from "@playwright/test";

const api = process.env.TRAVELPILOT_TEST_API_URL ?? "http://localhost:8000";
const stub = process.env.TRAVELPILOT_TEST_STUB_URL ?? "http://127.0.0.1:8002";

test("住宿候选只加入住宿备选，确认前不写正式行程", async ({ page, request }, testInfo) => {
  test.setTimeout(90_000);
  const browserErrors: string[] = [];
  page.on("pageerror", (error) => browserErrors.push(error.message));
  const email = `advisor-lodging-${Date.now()}@example.invalid`;
  const password = "travelpilot-test-2026";
  let registered = false;
  let csrf = "";

  try {
    expect((await request.post(`${stub}/reset`)).status()).toBe(204);
    const registration = await request.post(`${api}/api/v1/auth/register`, {
      data: { email, password },
    });
    expect(registration.status()).toBe(202);
    registered = true;
    const mailbox = await request.get(`${api}/api/v1/auth/dev-mailbox/${email}`);
    const token = new URL((await mailbox.json()).link).searchParams.get("token");
    expect(token).toBeTruthy();
    expect((await request.post(`${api}/api/v1/auth/verify-email`, { data: { token } })).ok()).toBeTruthy();
    expect((await request.post(`${api}/api/v1/auth/login`, { data: { email, password } })).ok()).toBeTruthy();

    const cookies = (await request.storageState()).cookies;
    csrf = cookies.find((cookie) => cookie.name === "travelpilot_csrf")?.value ?? "";
    await page.context().addCookies(cookies);
    const headers = { "X-CSRF-Token": decodeURIComponent(csrf) };
    const start = new Date();
    start.setUTCDate(start.getUTCDate() + 35);
    const end = new Date(start);
    end.setUTCDate(end.getUTCDate() + 1);
    const format = (value: Date) => value.toISOString().slice(0, 10);

    const tripResponse = await request.post(`${api}/api/v1/trips`, {
      headers,
      data: { destination: "成都", start_date: format(start), end_date: format(end), pace_level: 3 },
    });
    expect(tripResponse.ok(), await tripResponse.text()).toBeTruthy();
    const trip = await tripResponse.json() as { id: string };
    const sessionResponse = await request.post(`${api}/api/v1/advisor/sessions`, {
      headers,
      data: { mode: "existing", trip_id: trip.id },
    });
    expect(sessionResponse.ok(), await sessionResponse.text()).toBeTruthy();
    const session = await sessionResponse.json() as { id: string };

    await page.goto(`/advisor?sessionId=${session.id}`);
    await expect(page.locator("#advisor-message")).toBeVisible();
    await page.locator("#advisor-message").fill("只帮我找成都的酒店住宿候选");
    const messageResponsePromise = page.waitForResponse((response) =>
      new URL(response.url()).pathname.endsWith(`/sessions/${session.id}/messages`)
      && response.request().method() === "POST",
    );
    await page.getByRole("button", { name: "发送" }).click();
    const messageResponse = await messageResponsePromise;
    expect(messageResponse.ok(), await messageResponse.text()).toBeTruthy();
    const updated = await messageResponse.json() as {
      messages: Array<{ id: string; role: string; payload: Record<string, unknown> | null }>;
    };
    const assistantReply = [...updated.messages].reverse().find((item) => item.role === "assistant");
    const payload = assistantReply?.payload;
    expect(assistantReply?.id).toBeTruthy();
    expect(payload?.planning_category).toBe("LODGING");
    expect(payload?.schedule_recommendation_requested).toBe(false);
    expect((payload?.tool_calls as Array<{ tool?: string }> | undefined)
      ?.some((call) => call.tool === "search_places")).toBe(true);

    const taskState = page.getByRole("region", { name: "住宿规划任务状态" });
    await expect(taskState).toBeVisible();
    await expect(taskState).toContainText("加入清单不代表预订");

    const assistantCard = page.locator(".advisor-message.assistant").last();
    const feedbackResponsePromise = page.waitForResponse((response) =>
      new URL(response.url()).pathname.endsWith(`/sessions/${session.id}/messages/${assistantReply?.id}/feedback`)
      && response.request().method() === "PUT",
    );
    await assistantCard.getByRole("button", { name: "有帮助", exact: true }).click();
    const feedbackResponse = await feedbackResponsePromise;
    expect(feedbackResponse.ok(), await feedbackResponse.text()).toBeTruthy();
    await expect(assistantCard).toContainText("已反馈：这条回复有帮助");
    const persistedFeedbackSession = await request.get(`${api}/api/v1/advisor/sessions/${session.id}`);
    const persistedFeedback = (await persistedFeedbackSession.json()).messages.find(
      (item: { id: string }) => item.id === assistantReply?.id,
    )?.payload?.advisor_feedback;
    expect(persistedFeedback?.rating).toBe("HELPFUL");

    const hotel = page.locator(".advisor-live-candidate").filter({ hasText: "模拟旅行酒店" });
    await expect(hotel).toBeVisible({ timeout: 30_000 });
    await expect(hotel).toContainText("住宿");
    await expect(page.locator(".advisor-live-candidate").filter({ hasText: "模拟川味餐馆" })).toHaveCount(0);
    await hotel.getByRole("button", { name: "加入住宿" }).click();
    await expect(hotel.locator(".advisor-lodging-note")).toContainText("不会占用每日游览时间");
    await expect(hotel.locator(".advisor-candidate-schedule")).toHaveCount(0);

    const draftResponsePromise = page.waitForResponse((response) =>
      new URL(response.url()).pathname.endsWith(`/sessions/${session.id}/draft`)
      && response.request().method() === "POST",
    );
    await page.getByRole("button", { name: /检查并生成草案/ }).click();
    const draftResponse = await draftResponsePromise;
    expect(draftResponse.ok(), await draftResponse.text()).toBeTruthy();
    const drafted = await draftResponse.json() as {
      draft: { stops: unknown[]; lodging: Array<{ name: string }> };
    };
    expect(drafted.draft.stops).toEqual([]);
    expect(drafted.draft.lodging.map((item) => item.name)).toContain("模拟旅行酒店");
    expect(await (await request.get(`${api}/api/v1/trips/${trip.id}/plans`)).json()).toEqual([]);
    await expect(page.locator(".advisor-lodging-summary")).toContainText("住宿备选 · 尚未预订");
    await expect(page.getByRole("button", { name: "确认并保存行程" })).toBeEnabled();
    expect(browserErrors).toEqual([]);

    const screenshotPath = process.env.TRAVELPILOT_QA_SCREENSHOT;
    if (screenshotPath) {
      const suffix = testInfo.project.name === "chromium" ? "desktop" : "mobile";
      await page.screenshot({ path: screenshotPath.replace("{viewport}", suffix), fullPage: true });
    }
  } finally {
    if (registered) {
      await request.delete(`${api}/api/v1/account`, {
        headers: { "X-CSRF-Token": decodeURIComponent(csrf) },
      });
    }
  }
});
