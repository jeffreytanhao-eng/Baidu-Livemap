import { expect, test, type Page } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

// 实拍路演截图（DEMO_MODE 快照路径，无需 AK）：
// 产出 docs/screenshots/01/02/03/04/06 五张 PNG，每张断言 >30KB 防白图。
// 05（盲区筛选）随图层控件移除，对应 png 仅作历史留档，不再自动重拍。
// 代理模式同 smoke.spec.ts：同源 /api/* 由服务端转发到真实 API。

const API_TARGET = process.env.E2E_API_TARGET ?? "http://localhost:8000";
const SHOT_DIR = path.resolve(__dirname, "../../../docs/screenshots");
const MIN_BYTES = 30 * 1024;

test.beforeEach(async ({ page }) => {
  await page.route(/localhost:3100\/(api\/|health)/, async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const resp = await route.fetch({
      url: `${API_TARGET}${url.pathname}${url.search}`,
      method: req.method(),
      headers: { "Content-Type": "application/json" },
      postData: req.postData() ?? undefined,
    });
    await route.fulfill({ response: resp });
  });
});

// 打开评估页并等 15 分钟自动体验完成、SVG 图层稳定
async function openReady(page: Page) {
  await page.goto("/evaluate");
  await expect(page.getByText("生活圈评分", { exact: true })).toBeVisible({
    timeout: 45_000,
  });
  await page.waitForTimeout(1800);
}

async function snap(page: Page, name: string, fullPage = false) {
  fs.mkdirSync(SHOT_DIR, { recursive: true });
  const file = path.join(SHOT_DIR, name);
  await page.screenshot({ path: file, fullPage });
  const size = fs.statSync(file).size;
  expect(size).toBeGreaterThan(MIN_BYTES);
  console.log(`[screenshot] ${name} ${(size / 1024).toFixed(1)} KB`);
}

test.describe("路演实拍截图", () => {
  test("01 总览：默认 15 分钟自动出圈", async ({ page }) => {
    await openReady(page);
    await snap(page, "01-overview-15min.png");
  });

  test("02 建筑可进入性：住宅绕开、商场可穿行", async ({ page }) => {
    await openReady(page);
    // 关掉等时圈与 POI，只留建筑+水域+路网突出建筑分色
    await page.locator("#layer-isochrone").click();
    await page.locator("#layer-pois").click();
    await page.waitForTimeout(400);
    await snap(page, "03-buildings-enterability.png");
  });

  test("04 报告栏：雷达与达标明细", async ({ page }) => {
    await openReady(page);
    // 盲区速览/扇区缺口条已移除，仅捕获报告栏剩余图表
    await snap(page, "04-sector-gap.png");
  });

  test("06 报告打印版：仅留报告与关键图", async ({ page }) => {
    await openReady(page);
    await page.emulateMedia({ media: "print" });
    await page.waitForTimeout(400);
    await snap(page, "06-report-print.png", true);
  });
});
