import { expect, test } from "@playwright/test";
import type { APIRequestContext } from "@playwright/test";
import { request as standaloneRequest } from "@playwright/test";

const apiOrigin = process.env.TRAVELPILOT_E2E_API_URL ?? "http://localhost:8000";
const api = `${apiOrigin}/api/v1`;

async function createVerifiedUser(request: APIRequestContext, suffix: string) {
  const email = `trip-group-${suffix}-${Date.now()}@example.invalid`;
  const username = `group_${suffix[0]}_${Date.now().toString().slice(-8)}`;
  const password = "travelpilot-test-2026";
  const context = await standaloneRequest.newContext({ baseURL: apiOrigin });
  const registered = await context.post(`${api}/auth/register`, {
    data: { email, username, display_name: `旅行${suffix}`, password },
  });
  if (!registered.ok()) throw new Error(`Account setup failed: ${JSON.stringify(await registered.json())}`);
  const mailbox = await context.get(`${api}/auth/dev-mailbox/${email}`);
  const verifyToken = new URL((await mailbox.json()).link).searchParams.get("token")!;
  expect((await context.post(`${api}/auth/verify-email`, { data: { token: verifyToken } })).ok()).toBeTruthy();
  expect((await context.post(`${api}/auth/login`, { data: { email, password } })).ok()).toBeTruthy();
  const state = await context.storageState();
  const csrf = decodeURIComponent(state.cookies.find((cookie) => cookie.name === "travelpilot_csrf")?.value ?? "");
  return { context, state, email, username, csrf };
}

test("好友可接受小团体旅行邀请并查看共享旅行，所有者可管理成员角色", async ({ page, request }) => {
  test.setTimeout(90_000);
  const owner = await createVerifiedUser(request, "owner");
  const friend = await createVerifiedUser(request, "editor");
  const observer = await createVerifiedUser(request, "observer");
  const ownerHeaders = { "X-CSRF-Token": owner.csrf };
  const friendHeaders = { "X-CSRF-Token": friend.csrf };
  let ownerId = "";
  let ownerAccountDeleted = false;
  let friendAccountDeleted = false;

  try {
    const friendSearch = await owner.context.get(`${api}/friends/search?q=${friend.username}`);
    expect(friendSearch.ok()).toBeTruthy();
    const friendProfile = (await friendSearch.json())[0] as { user_id: string };
    expect((await owner.context.post(`${api}/friends/requests/users/${friendProfile.user_id}`, { headers: ownerHeaders })).ok()).toBeTruthy();

    const incoming = await friend.context.get(`${api}/friends/requests`);
    expect(incoming.ok()).toBeTruthy();
    const requestRow = (await incoming.json())[0] as { id: string };
    expect((await friend.context.post(`${api}/friends/requests/${requestRow.id}/accept`, { headers: friendHeaders })).ok()).toBeTruthy();

    const observerSearch = await owner.context.get(`${api}/friends/search?q=${observer.username}`);
    const observerProfile = (await observerSearch.json())[0] as { user_id: string };
    expect((await owner.context.post(`${api}/friends/requests/users/${observerProfile.user_id}`, { headers: ownerHeaders })).ok()).toBeTruthy();
    const observerRequests = await observer.context.get(`${api}/friends/requests`);
    const observerRequest = (await observerRequests.json())[0] as { id: string };
    expect((await observer.context.post(`${api}/friends/requests/${observerRequest.id}/accept`, {
      headers: { "X-CSRF-Token": observer.csrf },
    })).ok()).toBeTruthy();

    const ownerProfileResponse = await owner.context.get(`${api}/friends/search?q=${owner.username}`);
    const ownerProfile = (await ownerProfileResponse.json())[0] as { user_id: string };
    ownerId = ownerProfile.user_id;
    const createdTrip = await owner.context.post(`${api}/trips`, {
      headers: ownerHeaders,
      data: { destination: "协作验收城市", start_date: "2026-11-10", end_date: "2026-11-11", pace_level: 3 },
    });
    expect(createdTrip.ok()).toBeTruthy();
    const trip = await createdTrip.json() as { id: string };
    const initialStopResponse = await owner.context.post(`${api}/trips/${trip.id}/wishlist`, {
      headers: ownerHeaders,
      data: { name: "共同测试景点", category: "ATTRACTION", latitude: 24.3, longitude: 116.1, suggested_duration_min: 60 },
    });
    expect(initialStopResponse.ok()).toBeTruthy();
    const initialPlanResponse = await owner.context.post(`${api}/trips/${trip.id}/plan-runs`, {
      headers: ownerHeaders,
      data: { expected_base_version: 0, reason: "协作编辑验收初始版本" },
    });
    expect(initialPlanResponse.ok()).toBeTruthy();
    const initialPlan = await initialPlanResponse.json() as { id: string; version: number; nodes: { id: string; day: string }[] };
    expect(initialPlan.nodes).toHaveLength(1);
    expect((await observer.context.get(`${api}/group-trips/${trip.id}`)).status()).toBe(404);
    expect((await observer.context.get(`${api}/trips/${trip.id}`)).status()).toBe(404);

    const invited = await owner.context.post(`${api}/trips/${trip.id}/members`, {
      headers: ownerHeaders,
      data: { user_id: friendProfile.user_id, role: "EDITOR" },
    });
    expect(invited.ok(), `Invitation failed: ${invited.status()} ${await invited.text()}`).toBeTruthy();
    const membership = await invited.json() as { id: string };

    await page.context().addCookies(friend.state.cookies);
    await page.route("**/api/v1/group-trips", (route) => route.fulfill({
      status: 404,
      contentType: "application/json",
      body: JSON.stringify({ detail: "Not Found" }),
    }));
    await page.goto("/friends");
    await expect(page.getByRole("heading", { name: /我的好友 1/ })).toBeVisible();
    await expect(page.locator(".social-message.is-error")).toContainText("小团体旅行列表");
    await page.unroute("**/api/v1/group-trips");
    await expect(page.getByRole("heading", { name: /小团体旅行邀请/ })).toBeVisible();
    await page.getByRole("button", { name: "接受", exact: true }).click();
    await expect(page.getByRole("status")).toContainText("已加入旅行小团体");
    await expect(page.getByText(/协作验收城市/)).toBeVisible();
    const tripEdit = await friend.context.patch(`${api}/trips/${trip.id}`, {
      headers: friendHeaders,
      data: { budget: 3000 },
    });
    expect(tripEdit.ok(), `Shared trip update failed: ${tripEdit.status()} ${await tripEdit.text()}`).toBeTruthy();
    expect(Number((await tripEdit.json()).budget)).toBe(3000);
    const sharedDetail = await friend.context.get(`${api}/group-trips/${trip.id}`);
    expect(sharedDetail.ok(), `Shared trip detail failed: ${sharedDetail.status()} ${await sharedDetail.text()}`).toBeTruthy();
    await page.getByRole("button", { name: "打开共享旅行" }).click();
    await expect(page.getByRole("heading", { name: "协作验收城市", level: 3 })).toBeVisible();
    await expect(page.getByText("共同测试景点", { exact: true })).toBeVisible();
    const groupMessage = "午餐想选离景点近一些的地方，大家觉得呢？";
    await page.getByLabel("发送旅行消息").fill(groupMessage);
    await page.getByRole("button", { name: "发送", exact: true }).click();
    await expect(page.getByText(groupMessage)).toBeVisible();
    await expect.poll(async () => {
      const sharedMessages = await owner.context.get(`${api}/group-trips/${trip.id}/discussion`);
      if (!sharedMessages.ok()) return false;
      return (await sharedMessages.json()).some((message: { body: string }) => message.body === groupMessage);
    }).toBeTruthy();
    await page.getByText(/旅行活动记录/).click();
    await expect(page.getByText(/生成了行程计划 v1/)).toBeVisible();
    await page.getByRole("button", { name: "调整", exact: true }).click();
    await page.getByLabel("开始").fill("10:00");
    await page.getByLabel("结束").fill("11:00");
    await page.getByRole("button", { name: "保存", exact: true }).click();
    await expect(page.getByText(/10:00–11:00/)).toBeVisible();

    const latestPlans = await friend.context.get(`${api}/trips/${trip.id}/plans`);
    const latestPlan = (await latestPlans.json())[0] as { id: string; version: number };
    expect(latestPlan.version).toBe(initialPlan.version + 1);
    const staleEdit = await friend.context.patch(`${api}/trips/${trip.id}/plans/${initialPlan.id}/nodes/${initialPlan.nodes[0].id}`, {
      headers: friendHeaders,
      data: { day: initialPlan.nodes[0].day, start_time: "11:00", end_time: "12:00", expected_base_version: initialPlan.version },
    });
    expect(staleEdit.status()).toBe(409);
    const conflict = await staleEdit.json();
    expect(conflict.detail.latest_version).toBe(latestPlan.version);
    expect(conflict.detail.latest_changes[0].actor_display_name).toBe("旅行editor");
    const planRecord = await friend.context.get(`${api}/trips/${trip.id}/plans/${latestPlan.id}`);
    expect((await planRecord.json()).created_by_display_name).toBe("旅行editor");

    const addedStop = await friend.context.post(`${api}/trips/${trip.id}/wishlist`, {
      headers: friendHeaders,
      data: { name: "编辑者添加的地点", category: "ATTRACTION", latitude: 24.31, longitude: 116.11, suggested_duration_min: 45 },
    });
    expect(addedStop.ok()).toBeTruthy();
    await page.getByRole("link", { name: "添加共同地点" }).click();
    await expect(page.getByLabel("选择要探索的旅行")).toHaveValue(trip.id);
    await page.goBack();

    const ownerContext = await page.context().browser()!.newContext();
    try {
      await ownerContext.addCookies(owner.state.cookies);
      const ownerPage = await ownerContext.newPage();
      await ownerPage.goto(`/trips/${trip.id}`);
      await ownerPage.getByRole("button", { name: "分享设置" }).click();
      await expect(ownerPage.getByText("已加入小团体")).toBeVisible();
      await ownerPage.getByLabel("设置 旅行editor 的权限").selectOption("VIEWER");
      await expect(ownerPage.getByText("已将 旅行editor 设为查看者。")).toBeVisible();
      const memberRows = await owner.context.get(`${api}/trips/${trip.id}/members`);
      expect((await memberRows.json()).find((row: { id: string }) => row.id === membership.id)?.role).toBe("VIEWER");
      const deniedEdit = await friend.context.post(`${api}/trips/${trip.id}/wishlist`, {
        headers: friendHeaders,
        data: { name: "查看者不应添加", category: "ATTRACTION", latitude: 24.32, longitude: 116.12, suggested_duration_min: 45 },
      });
      expect(deniedEdit.status()).toBe(403);
      await page.goto(`/trips/${trip.id}/plan`);
      await expect(page.getByRole("button", { name: "调整", exact: true })).toHaveCount(0);
      const revoked = await owner.context.delete(`${api}/trips/${trip.id}/members/${membership.id}`, {
        headers: ownerHeaders,
      });
      expect(revoked.status()).toBe(204);
      expect((await friend.context.get(`${api}/group-trips/${trip.id}/discussion`)).status()).toBe(404);
      expect((await friend.context.get(`${api}/group-trips/${trip.id}`)).status()).toBe(404);
      const ownerExport = await owner.context.get(`${api}/account/export`);
      expect(ownerExport.ok()).toBeTruthy();
      const exported = await ownerExport.json();
      const preservedMessage = exported.data.trip_discussion_messages.find(
        (item: { body: string }) => item.body === groupMessage,
      );
      expect(preservedMessage.author_user_id).toBe(friendProfile.user_id);
      expect(preservedMessage.author_display_name).toBe("旅行editor");

      const cascadeTripResponse = await owner.context.post(`${api}/trips`, {
        headers: ownerHeaders,
        data: { destination: "账户删除级联验收", start_date: "2026-11-12", end_date: "2026-11-13", pace_level: 3 },
      });
      expect(cascadeTripResponse.ok()).toBeTruthy();
      const cascadeTrip = await cascadeTripResponse.json() as { id: string };
      const cascadeInvite = await owner.context.post(`${api}/trips/${cascadeTrip.id}/members`, {
        headers: ownerHeaders,
        data: { user_id: observerProfile.user_id, role: "VIEWER" },
      });
      expect(cascadeInvite.ok()).toBeTruthy();
      const cascadeMembership = await cascadeInvite.json() as { id: string };
      expect((await observer.context.post(`${api}/trip-invitations/${cascadeMembership.id}/accept`, {
        headers: { "X-CSRF-Token": observer.csrf },
      })).ok()).toBeTruthy();
      expect((await observer.context.get(`${api}/group-trips/${cascadeTrip.id}`)).ok()).toBeTruthy();
      const readonlyTripResponse = await owner.context.post(`${api}/trips`, {
        headers: ownerHeaders,
        data: { destination: "旧版只读分享验收", start_date: "2026-11-14", end_date: "2026-11-15", pace_level: 3 },
      });
      expect(readonlyTripResponse.ok()).toBeTruthy();
      const readonlyTrip = await readonlyTripResponse.json() as { id: string };
      const shareResponse = await owner.context.post(`${api}/trips/${readonlyTrip.id}/shares`, {
        headers: ownerHeaders,
        data: { friend_user_id: observerProfile.user_id, scope: "TRIP_AND_PLAN" },
      });
      expect(shareResponse.ok()).toBeTruthy();
      expect((await observer.context.get(`${api}/shared/trips/${readonlyTrip.id}`)).ok()).toBeTruthy();
      expect((await observer.context.patch(`${api}/trips/${readonlyTrip.id}`, {
        headers: { "X-CSRF-Token": observer.csrf }, data: { budget: 5000 },
      })).status()).toBe(404);
      expect((await friend.context.delete(`${api}/account`, { headers: friendHeaders })).status()).toBe(204);
      friendAccountDeleted = true;
      const afterMemberDeletion = await owner.context.get(`${api}/account/export`);
      const memberDeletedExport = await afterMemberDeletion.json();
      const retainedAfterDeletion = memberDeletedExport.data.trip_discussion_messages.find(
        (item: { body: string }) => item.body === groupMessage,
      );
      expect(retainedAfterDeletion.author_user_id).toBeNull();
      expect(retainedAfterDeletion.author_display_name).toBe("旅行editor");
      expect((await owner.context.delete(`${api}/account`, { headers: ownerHeaders })).status()).toBe(204);
      ownerAccountDeleted = true;
      expect((await observer.context.get(`${api}/group-trips/${cascadeTrip.id}`)).status()).toBe(404);
      expect((await observer.context.get(`${api}/shared/trips/${readonlyTrip.id}`)).status()).toBe(404);
    } finally {
      await ownerContext.close();
    }
  } finally {
    const cleanup = await Promise.allSettled([
      ownerId && !ownerAccountDeleted ? owner.context.delete(`${api}/account`, { headers: ownerHeaders }) : Promise.resolve(null),
      friendAccountDeleted ? Promise.resolve(null) : friend.context.delete(`${api}/account`, { headers: friendHeaders }),
      observer.context.delete(`${api}/account`, { headers: { "X-CSRF-Token": observer.csrf } }),
    ]);
    for (const result of cleanup) {
      if (result.status === "rejected") console.warn("Temporary collaboration test account cleanup failed.");
    }
    await owner.context.dispose();
    await friend.context.dispose();
    await observer.context.dispose();
  }
});
