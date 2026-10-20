import { expect, test } from "@playwright/test";

const tripId = "00000000-0000-4000-8000-000000000001";
const contentId = "00000000-0000-4000-8000-000000000002";
const entityId = "00000000-0000-4000-8000-000000000003";
const candidateId = "00000000-0000-4000-8000-000000000004";
const factId = "00000000-0000-4000-8000-000000000005";

function guide(status: string, text: string, metadata: Record<string, unknown> | null = null, error: string | null = null) {
  return {
    id: contentId,
    trip_id: tripId,
    content_type: "TEXT",
    text,
    status,
    error,
    skill_name: "攻略导入",
    skill_version: "1.1.0",
    skill_applicability: null,
    token_usage: 28,
    extraction_metadata: metadata,
    guide_facts: [{
      id: factId,
      kind: "OPENING_HOURS",
      title: "开放时间",
      content: "周一闭馆，其余时间开放",
      evidence: "周一闭馆，其余时间开放",
      related_places: ["梅州学宫"],
      confidence: 0.82,
      saved: status === "IMPORTED",
      edited: false,
    }],
    created_at: "2026-10-20T02:00:00Z",
    entities: [{
      id: entityId,
      name: "梅州学宫",
      category: "ATTRACTION",
      suggestion: "建议安排半小时参观",
      evidence: "上午11:00参观梅州学宫",
      confidence: 0.91,
      candidates: [{
        id: candidateId,
        entity_id: entityId,
        provider: "amap",
        provider_id: "amap-poi-1",
        name: "梅州学宫",
        address: "梅州市梅江区",
        latitude: 24.3,
        longitude: 116.1,
        category: "ATTRACTION",
        duration_min: 30,
        score: 0.94,
        confirmed: status === "IMPORTED",
      }],
    }],
  };
}

async function mockWorkspace(page: import("@playwright/test").Page) {
  const history: Array<Record<string, unknown>> = [];
  let current = guide("NEEDS_CONFIRMATION", "梅州一日游攻略\n上午11:00参观梅州学宫\n周一闭馆，其余时间开放");
  let retryResponse: Record<string, unknown> | null = null;
  let importResponse: Record<string, unknown> | null = null;
  let confirmationPayload: Record<string, unknown> | null = null;
  let importedText = "";

  await page.route("http://localhost:8000/api/v1/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === "/api/v1/auth/me") {
      await route.fulfill({ json: {
        id: "00000000-0000-4000-8000-000000000099",
        email: "guide-test@example.invalid",
        username: "guide-test",
        created_at: "2026-10-20T00:00:00Z",
        display_name: "攻略测试",
        home_city: "梅州",
        bio: "",
        has_avatar: false,
      } });
    } else if (path === "/api/v1/trips") {
      await route.fulfill({ json: [{
        id: tripId, destination: "梅州", start_date: "2026-10-20", end_date: "2026-10-21",
        budget: 2000, pace_level: 3, status: "PLANNING", has_plan: false,
        created_at: "2026-10-20T00:00:00Z", updated_at: "2026-10-20T00:00:00Z",
      }] });
    } else if (path === `/api/v1/trips/${tripId}/content` && request.method() === "GET") {
      await route.fulfill({ json: history });
    } else if (path === `/api/v1/trips/${tripId}/content/ocr`) {
      await route.fulfill({ json: { text: "午餐推荐鑫丰特色餐饮", confidence: 0.97, stored: false } });
    } else if (path === `/api/v1/trips/${tripId}/content` && request.method() === "POST") {
      importedText = (request.postDataJSON() as { text: string }).text;
      current = (importResponse ?? retryResponse ?? guide("NEEDS_CONFIRMATION", importedText)) as typeof current;
      history.unshift({ id: contentId, content_type: "TEXT", status: current.status, error: null,
        extraction_metadata: null, created_at: current.created_at });
      await route.fulfill({ status: 201, json: current });
    } else if (path === `/api/v1/content/${contentId}/confirm`) {
      confirmationPayload = request.postDataJSON() as Record<string, unknown>;
      current = guide("IMPORTED", importedText || current.text);
      await route.fulfill({ json: current });
    } else if (path === `/api/v1/content/${contentId}/retry`) {
      current = (retryResponse ?? guide("NEEDS_CONFIRMATION", current.text)) as typeof current;
      await route.fulfill({ json: current });
    } else if (path === `/api/v1/content/${contentId}` && request.method() === "GET") {
      await route.fulfill({ json: current });
    } else if (path === `/api/v1/content/${contentId}` && request.method() === "DELETE") {
      history.splice(0, history.length);
      await route.fulfill({ status: 204, body: "" });
    } else {
      await route.fulfill({ status: 404, json: { detail: `Unexpected mocked request: ${request.method()} ${path}` } });
    }
  });

  await page.goto(`/trips/${tripId}/import`);
  await expect(page.getByRole("heading", { name: "把攻略里的地点和实用细节留下来" })).toBeVisible();
  return {
    getImportedText: () => importedText,
    getConfirmationPayload: () => confirmationPayload,
    setRetryResponse: (value: Record<string, unknown>) => { retryResponse = value; },
    setImportResponse: (value: Record<string, unknown>) => { importResponse = value; },
  };
}

test("攻略导入文本可提取、确认保存、重开并删除记录", async ({ page }) => {
  const state = await mockWorkspace(page);
  const sourceText = "梅州一日游攻略\n上午11:00参观梅州学宫\n周一闭馆，其余时间开放";
  await page.getByLabel("攻略原文").fill(sourceText);
  await page.getByRole("button", { name: "提取地点" }).click();

  await expect(page.getByRole("heading", { name: "梅州学宫" })).toBeVisible();
  await expect(page.getByText("周一闭馆，其余时间开放").first()).toBeVisible();
  await expect(page.getByText("原文依据：上午11:00参观梅州学宫")).toBeVisible();
  expect(state.getImportedText()).toBe(sourceText);

  await page.getByLabel(/梅州学宫 ·/).check();
  await page.getByRole("button", { name: "保存 1 个地点与 1 条攻略要点" }).click();
  await expect(page.getByText("攻略识别 · IMPORTED")).toBeVisible();
  expect(state.getConfirmationPayload()).toEqual({ candidate_ids: [candidateId], fact_ids: [factId] });

  await page.getByText(/这段旅行的攻略导入记录/).click();
  await page.getByRole("button", { name: /打开并继续查看或处理/ }).click();
  await expect(page.getByRole("heading", { name: "梅州学宫" })).toBeVisible();
  page.on("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "删除记录" }).click();
  await expect(page.getByText(/这段旅行的攻略导入记录/)).toHaveCount(0);
});

test("图片 OCR 结果填入原文框，失败分段可重试且保留本地兜底提示", async ({ page }) => {
  const state = await mockWorkspace(page);
  await page.getByLabel(/选择图片/).setInputFiles({
    name: "guide.png", mimeType: "image/png", buffer: Buffer.from("mock image"),
  });
  await expect(page.getByLabel(/识别文字/)).toHaveValue("午餐推荐鑫丰特色餐饮");

  state.setRetryResponse(guide("PARTIAL", "梅州一日游攻略", {
    total_chunks: 2,
    completed_chunks: 0,
    retry_attempts: 0,
    failed_chunks: [{ index: 0, start: 0, end: 20 }],
    fallback_chunks: [{ index: 0, start: 0, end: 20 }],
  }));
  await page.getByLabel(/识别文字/).fill("梅州一日游攻略，梅州学宫开放时间攻略");
  await page.getByRole("button", { name: "提取地点" }).click();
  await expect(page.getByText(/本地规则保留地点或攻略细节/)).toBeVisible();
  await page.getByRole("button", { name: "缩小片段重试 1 个失败片段" }).click();
  await expect(page.getByRole("heading", { name: "攻略识别 · PARTIAL" })).toBeVisible();
});

test("旧版超过地点上限的失败记录可按新上限重新提取整篇攻略", async ({ page }) => {
  const state = await mockWorkspace(page);
  state.setImportResponse(guide("FAILED", "梅州多日攻略", {
    failure_code: "RESULT_LIMIT_EXCEEDED",
    total_chunks: null,
    completed_chunks: null,
    retry_attempts: 0,
    failed_chunks: [],
    fallback_chunks: [],
  }, "攻略信息量超过单次确认上限；请按行程日期或主题拆分后导入。"));
  state.setRetryResponse(guide("NEEDS_CONFIRMATION", "梅州多日攻略"));

  await page.getByLabel("攻略原文").fill("梅州多日攻略：包含多个地点和游览细节");
  await page.getByRole("button", { name: "提取地点" }).click();
  await expect(page.getByText(/超过单次确认上限/)).toBeVisible();
  await page.getByRole("button", { name: "按新上限重新提取整篇攻略" }).click();
  await expect(page.getByRole("heading", { name: "攻略识别 · NEEDS_CONFIRMATION" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "梅州学宫" })).toBeVisible();
});
