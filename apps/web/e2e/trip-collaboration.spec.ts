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
  const ownerHeaders = { "X-CSRF-Token": owner.csrf };
  const friendHeaders = { "X-CSRF-Token": friend.csrf };
  let ownerId = "";

  try {
    const friendSearch = await owner.context.get(`${api}/friends/search?q=${friend.username}`);
    expect(friendSearch.ok()).toBeTruthy();
    const friendProfile = (await friendSearch.json())[0] as { user_id: string };
    expect((await owner.context.post(`${api}/friends/requests/users/${friendProfile.user_id}`, { headers: ownerHeaders })).ok()).toBeTruthy();

    const incoming = await friend.context.get(`${api}/friends/requests`);
    expect(incoming.ok()).toBeTruthy();
    const requestRow = (await incoming.json())[0] as { id: string };
    expect((await friend.context.post(`${api}/friends/requests/${requestRow.id}/accept`, { headers: friendHeaders })).ok()).toBeTruthy();

    const ownerProfileResponse = await owner.context.get(`${api}/friends/search?q=${owner.username}`);
    const ownerProfile = (await ownerProfileResponse.json())[0] as { user_id: string };
    ownerId = ownerProfile.user_id;
    const createdTrip = await owner.context.post(`${api}/trips`, {
      headers: ownerHeaders,
      data: { destination: "协作验收城市", start_date: "2026-11-10", end_date: "2026-11-11", pace_level: 3 },
    });
    expect(createdTrip.ok()).toBeTruthy();
    const trip = await createdTrip.json() as { id: string };

    const invited = await owner.context.post(`${api}/trips/${trip.id}/members`, {
      headers: ownerHeaders,
      data: { user_id: friendProfile.user_id, role: "EDITOR" },
    });
    expect(invited.ok(), `Invitation failed: ${invited.status()} ${await invited.text()}`).toBeTruthy();
    const membership = await invited.json() as { id: string };

    await page.context().addCookies(friend.state.cookies);
    await page.goto("/friends");
    await expect(page.getByRole("heading", { name: /小团体旅行邀请/ })).toBeVisible();
    await page.getByRole("button", { name: "接受", exact: true }).click();
    await expect(page.getByRole("status")).toContainText("已加入旅行小团体");
    await expect(page.getByText(/协作验收城市/)).toBeVisible();
    const sharedDetail = await friend.context.get(`${api}/group-trips/${trip.id}`);
    expect(sharedDetail.ok(), `Shared trip detail failed: ${sharedDetail.status()} ${await sharedDetail.text()}`).toBeTruthy();
    await page.getByRole("button", { name: "打开共享旅行" }).click();
    await expect(page.getByRole("heading", { name: "协作验收城市", level: 3 })).toBeVisible();
    await expect(page.getByText("这段旅行还没有保存的行程计划。")).toBeVisible();

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
    } finally {
      await ownerContext.close();
    }
  } finally {
    const cleanup = await Promise.allSettled([
      ownerId ? owner.context.delete(`${api}/account`, { headers: ownerHeaders }) : Promise.resolve(null),
      friend.context.delete(`${api}/account`, { headers: friendHeaders }),
    ]);
    for (const result of cleanup) {
      if (result.status === "rejected") console.warn("Temporary collaboration test account cleanup failed.");
    }
    await owner.context.dispose();
    await friend.context.dispose();
  }
});
