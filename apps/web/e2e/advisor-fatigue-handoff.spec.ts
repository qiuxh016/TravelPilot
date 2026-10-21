import { expect, test } from "@playwright/test";

const api = process.env.TRAVELPILOT_TEST_API_URL ?? "http://localhost:8000";
const stub = process.env.TRAVELPILOT_TEST_STUB_URL ?? "http://127.0.0.1:8002";

test("顾问内完成途中调整预览，确认后才保存", async ({ page, request }) => {
  test.setTimeout(120_000);
  const email = `fatigue-handoff-${Date.now()}@example.invalid`;
  const password = "travelpilot-test-2026";
  let registered = false;
  let csrf = "";
  try {
    expect((await request.post(`${stub}/reset`)).status()).toBe(204);
    expect((await request.post(`${api}/api/v1/auth/register`, {
      data: { email, password },
    })).status()).toBe(202);
    registered = true;
    const mailbox = await request.get(`${api}/api/v1/auth/dev-mailbox/${email}`);
    const link = (await mailbox.json()).link as string;
    const token = new URL(link, api).searchParams.get("token");
    expect(token).toBeTruthy();
    expect((await request.post(`${api}/api/v1/auth/verify-email`, { data: { token } })).ok()).toBeTruthy();
    expect((await request.post(`${api}/api/v1/auth/login`, { data: { email, password } })).ok()).toBeTruthy();
    csrf = (await request.storageState()).cookies.find((cookie) => cookie.name === "travelpilot_csrf")?.value ?? "";
    const headers = { "X-CSRF-Token": decodeURIComponent(csrf) };

    const start = new Date();
    start.setUTCDate(start.getUTCDate() + 30);
    const date = start.toISOString().slice(0, 10);
    const tripResponse = await request.post(`${api}/api/v1/trips`, {
      headers, data: { destination: "成都", start_date: date, end_date: date, pace_level: 3 },
    });
    expect(tripResponse.ok(), await tripResponse.text()).toBeTruthy();
    const trip = await tripResponse.json() as { id: string };
    for (const [name, latitude, longitude] of [
      ["人民公园", 30.663, 104.055], ["武侯祠", 30.646, 104.048], ["宽窄巷子", 30.663, 104.052],
    ] as const) {
      const saved = await request.post(`${api}/api/v1/trips/${trip.id}/wishlist`, {
        headers,
        data: { name, category: "ATTRACTION", latitude, longitude, suggested_duration_min: 90, priority: 3 },
      });
      expect(saved.ok(), await saved.text()).toBeTruthy();
    }
    const planResponse = await request.post(`${api}/api/v1/trips/${trip.id}/plan-runs`, { headers });
    expect(planResponse.ok(), await planResponse.text()).toBeTruthy();
    expect(await (await request.get(`${api}/api/v1/trips/${trip.id}/plans`)).json()).toHaveLength(1);
    const addedPoiResponse = await request.post(`${api}/api/v1/trips/${trip.id}/wishlist`, {
      headers,
      data: {
        name: "杜甫草堂", category: "ATTRACTION", latitude: 30.659, longitude: 104.028,
        suggested_duration_min: 45, priority: 2,
      },
    });
    expect(addedPoiResponse.ok(), await addedPoiResponse.text()).toBeTruthy();
    const addedPoi = await addedPoiResponse.json() as { poi_id: string };
    const sessionResponse = await request.post(`${api}/api/v1/advisor/sessions`, {
      headers, data: { mode: "existing", trip_id: trip.id },
    });
    expect(sessionResponse.ok(), await sessionResponse.text()).toBeTruthy();
    const session = await sessionResponse.json() as { id: string };

    await page.goto("/login");
    await page.getByLabel("邮箱或用户名").fill(email);
    await page.getByLabel(/密码/).fill(password);
    await page.getByRole("button", { name: "登录", exact: true }).click();
    await expect(page).toHaveURL(/\/trips$/);
    await page.goto(`/advisor?sessionId=${session.id}`);
    await expect(page.getByRole("region", { name: "顾问对话" })).toBeVisible();
    await page.locator("#advisor-message").fill("我今天有点累，帮我把后面的安排放松些");
    const turnResponse = page.waitForResponse((response) =>
      response.request().method() === "POST"
      && new URL(response.url()).pathname.endsWith(`/sessions/${session.id}/messages`),
    );
    await page.getByRole("button", { name: "发送" }).click();
    const turn = await turnResponse;
    expect(turn.ok(), await turn.text()).toBeTruthy();
    const body = await turn.json() as {
      pace_level: number;
      messages: Array<{ payload: Record<string, unknown> | null }>;
    };
    const payload = body.messages.at(-1)?.payload;
    expect(payload?.agent_action).toBe("HANDOFF_TO_EVENT_REPLAN");
    expect(payload?.event_replan_handoff).toMatchObject({ event_type: "USER_FATIGUE" });
    expect(body.pace_level).toBe(3);
    expect(payload?.schedule_suggestions).toEqual([]);

    const handoff = page.locator(".advisor-event-replan-handoff");
    await expect(handoff).toContainText("确认前不会写入新计划");
    await handoff.getByRole("button", { name: /设置时间并查看途中调整预览/ }).click();
    await expect(page.getByRole("dialog")).toBeVisible();
    await expect(page.getByLabel("发生了什么？")).toHaveValue("USER_FATIGUE");
    await page.getByLabel("生效时间").fill(`${date}T09:00`);
    const previewResponse = page.waitForResponse((response) =>
      response.request().method() === "POST"
      && new URL(response.url()).pathname.endsWith("/replan-runs"),
    );
    await page.getByRole("button", { name: /生成预览/ }).click();
    const preview = await previewResponse;
    expect(preview.ok(), await preview.text()).toBeTruthy();
    const previewBody = await preview.json() as {
      proposed_plan_status: string; diff: Array<{ operation: string; reason_code: string }>;
    };
    expect(previewBody.proposed_plan_status).toBe("READY");
    expect(previewBody.diff.some((item) => item.reason_code === "USER_FATIGUE" && item.operation === "REMOVE")).toBeTruthy();
    expect(await (await request.get(`${api}/api/v1/trips/${trip.id}/plans`)).json()).toHaveLength(1);
    await page.getByRole("button", { name: /确认并更新正式行程/ }).click();
    await expect(page.getByText("调整已保存", { exact: true })).toBeVisible();
    expect(await (await request.get(`${api}/api/v1/trips/${trip.id}/plans`)).json()).toHaveLength(2);

    const weatherSessionResponse = await request.post(`${api}/api/v1/advisor/sessions`, {
      headers, data: { mode: "existing", trip_id: trip.id },
    });
    expect(weatherSessionResponse.ok(), await weatherSessionResponse.text()).toBeTruthy();
    const weatherSession = await weatherSessionResponse.json() as { id: string };
    await page.goto(`/advisor?sessionId=${weatherSession.id}`);
    await page.locator("#advisor-message").fill("天气突变，调整后面安排");
    const weatherTurnResponse = page.waitForResponse((response) =>
      response.request().method() === "POST"
      && new URL(response.url()).pathname.endsWith(`/sessions/${weatherSession.id}/messages`),
    );
    await page.getByRole("button", { name: "发送" }).click();
    const weatherTurn = await weatherTurnResponse;
    expect(weatherTurn.ok(), await weatherTurn.text()).toBeTruthy();
    const weatherBody = await weatherTurn.json() as {
      messages: Array<{ payload: Record<string, unknown> | null }>;
    };
    expect(weatherBody.messages.at(-1)?.payload?.event_replan_handoff).toMatchObject({
      event_type: "WEATHER_CHANGE",
      prefill: {},
    });
    const weatherHandoff = page.locator(".advisor-event-replan-handoff");
    await weatherHandoff.getByRole("button").click();
    await expect(page.getByLabel("发生了什么？")).toHaveValue("WEATHER_CHANGE");
    await expect(page.getByLabel("生效时间")).toHaveValue("");
    await expect(page.getByLabel("天气情况")).toHaveValue("");
    await page.getByLabel("生效时间").fill(`${date}T09:00`);
    const invalidWeatherField = page.evaluate(() => new Promise<string>((resolve) => {
      document.querySelector(".advisor-whatif form")?.addEventListener("invalid", (event) => {
        resolve((event.target as HTMLElement).getAttribute("aria-label") ?? "unknown");
      }, true);
    }));
    await page.getByRole("button", { name: /生成预览/ }).click();
    await expect(invalidWeatherField).resolves.toBe("天气情况");
    if (process.env.TRAVELPILOT_UI_EVIDENCE_PATH) {
      await page.screenshot({ path: process.env.TRAVELPILOT_UI_EVIDENCE_PATH, fullPage: false });
    }

    async function requestEventHandoff(message: string, eventType: string) {
      const createdSession = await request.post(`${api}/api/v1/advisor/sessions`, {
        headers, data: { mode: "existing", trip_id: trip.id },
      });
      expect(createdSession.ok(), await createdSession.text()).toBeTruthy();
      const createdSessionBody = await createdSession.json() as { id: string };
      await page.goto(`/advisor?sessionId=${createdSessionBody.id}`);
      await page.locator("#advisor-message").fill(message);
      const responsePromise = page.waitForResponse((response) =>
        response.request().method() === "POST"
        && new URL(response.url()).pathname.endsWith(`/sessions/${createdSessionBody.id}/messages`),
      );
      await page.getByRole("button", { name: "发送" }).click();
      const response = await responsePromise;
      expect(response.ok(), await response.text()).toBeTruthy();
      const responseBody = await response.json() as {
        messages: Array<{ payload: Record<string, unknown> | null }>;
      };
      expect(responseBody.messages.at(-1)?.payload?.event_replan_handoff).toMatchObject({ event_type: eventType });
      await page.locator(".advisor-event-replan-handoff").getByRole("button").click();
      await expect(page.getByLabel("发生了什么？")).toHaveValue(eventType);
    }

    async function previewAndConfirm(expectedOperation?: string, reasonCode?: string) {
      const before = await request.get(`${api}/api/v1/trips/${trip.id}/plans`);
      const beforePlans = await before.json() as Array<{ version: number }>;
      const previewResponsePromise = page.waitForResponse((response) =>
        response.request().method() === "POST"
        && new URL(response.url()).pathname.endsWith("/replan-runs"),
      );
      await page.getByRole("button", { name: /生成预览/ }).click();
      const response = await previewResponsePromise;
      expect(response.ok(), await response.text()).toBeTruthy();
      const preview = await response.json() as {
        proposed_plan_status: string;
        diff: Array<{ operation: string; reason_code: string }>;
      };
      expect(preview.proposed_plan_status).toBe("READY");
      if (expectedOperation && reasonCode) {
        expect(preview.diff.some((item) => item.operation === expectedOperation && item.reason_code === reasonCode)).toBeTruthy();
      }
      expect(await (await request.get(`${api}/api/v1/trips/${trip.id}/plans`)).json()).toHaveLength(beforePlans.length);
      await page.getByRole("button", { name: /确认并更新正式行程/ }).click();
      await expect(page.getByText("调整已保存", { exact: true })).toBeVisible();
      const afterPlans = await (await request.get(`${api}/api/v1/trips/${trip.id}/plans`)).json() as Array<{ version: number }>;
      expect(afterPlans).toHaveLength(beforePlans.length + 1);
    }

    await requestEventHandoff("堵车晚了半小时，重新安排后面的计划", "TIME_DELAY");
    await expect(page.getByLabel("延误分钟数")).toHaveValue("30");
    await page.getByLabel("生效时间").fill(`${date}T09:00`);
    await previewAndConfirm("MOVE", "TIME_DELAY");

    await requestEventHandoff("人民公园临时闭馆了，帮我重新安排后续行程", "POI_CANCELLED");
    await page.getByLabel("生效时间").fill(`${date}T09:00`);
    const latestPlan = await (await request.get(`${api}/api/v1/trips/${trip.id}/plans`)).json() as Array<{
      nodes: Array<{ poi_id: string }>;
    }>;
    expect(latestPlan[0]?.nodes.length).toBeGreaterThanOrEqual(2);
    await page.getByLabel("涉及地点").selectOption(latestPlan[0].nodes[0].poi_id);
    await previewAndConfirm("REMOVE", "POI_CANCELLED");

    await requestEventHandoff("我的预约时间变了，帮我调整这段行程", "RESERVATION_CHANGE");
    await page.getByLabel("生效时间").fill(`${date}T09:00`);
    const reservationPlan = await (await request.get(`${api}/api/v1/trips/${trip.id}/plans`)).json() as Array<{
      nodes: Array<{ poi_id: string }>;
    }>;
    expect(reservationPlan[0]?.nodes.length).toBeGreaterThan(0);
    await page.getByLabel("涉及地点").selectOption(reservationPlan[0].nodes[0].poi_id);
    await page.getByLabel("预约开始").fill("10:00");
    await page.getByLabel("预约结束").fill("11:30");
    await previewAndConfirm("MOVE", "RESERVATION_CHANGE");

    await requestEventHandoff("我还想临时加上杜甫草堂，帮我安排到后面行程", "USER_ADD");
    await page.getByLabel("生效时间").fill(`${date}T09:00`);
    await page.getByLabel("涉及地点").selectOption(addedPoi.poi_id);
    await page.getByLabel("营业开始").fill("09:00");
    await page.getByLabel("营业结束").fill("20:00");
    await previewAndConfirm("ADD", "USER_ADD");
  } finally {
    if (registered && csrf) {
      const cleanup = await request.delete(`${api}/api/v1/account`, {
        headers: { "X-CSRF-Token": decodeURIComponent(csrf) },
      });
      expect(cleanup.status()).toBe(204);
    }
  }
});
