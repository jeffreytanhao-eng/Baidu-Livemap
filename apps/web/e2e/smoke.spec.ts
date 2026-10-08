import { expect, test } from "@playwright/test";

// 冒烟用例（DEMO_MODE 快照路径，无需 AK）：
// 1. 首屏样例中心 + 默认 15 分钟自动出综合分；
// 2. 切 20 分钟但未点「开始体验」：出现「参数已改」提示、报告水印仍在，
//    且网络层不再发起 checkup 请求。
//
// Web 以 NEXT_PUBLIC_API_BASE=<web 源> 启动（见 playwright.config.ts），
// 此处把同源的 /api/* 与 /health 请求在服务端转发到真实 API，绕过浏览器 CORS。

const API_TARGET = process.env.E2E_API_TARGET ?? "http://localhost:8000";

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

test.describe("生活圈体验冒烟", () => {
  test("默认 15 分钟自动出分", async ({ page }) => {
    await page.goto("/evaluate");
    // 直接进入评估页：回退键不可选（仅从附近小区页跳入时可用）
    await expect(page.getByRole("button", { name: "返回附近小区" })).toBeDisabled();
    await expect(page.getByText("生活圈评分", { exact: true })).toBeVisible({ timeout: 45_000 });
    const score = page.locator("#report-panel span.text-2xl");
    await expect(score).toHaveText(/^\d+\.\d$/);
  });

  test("切 5 未点开始不出新结果", async ({ page }) => {
    await page.goto("/evaluate");
    await expect(page.getByText("生活圈评分", { exact: true })).toBeVisible({ timeout: 45_000 });

    // 初始自动体验完成后才挂计数：之后任何 checkup 请求都算违规。
    // fallback 转交 beforeEach 的代理路由，不真正拦截。
    let checkupCalls = 0;
    await page.route("**/api/v1/checkup", (route) => {
      checkupCalls += 1;
      void route.fallback();
    });

    await page.getByRole("button", { name: "5", exact: true }).click();

    // 参数已改提示 + 报告水印（仍为上次 15 分钟结果）
    await expect(page.getByText(/参数已改/).first()).toBeVisible();
    await expect(page.getByText("以下为上次 15 分钟结果").first()).toBeVisible();

    await page.waitForTimeout(1500);
    expect(checkupCalls).toBe(0);
  });

  test("附近小区：列表跳详情可回退且状态保留", async ({ page }) => {
    await page.goto("/nearby");

    // 默认中心（劲松）自动查询出小区列表
    await expect(page.getByText(/附近小区（1 公里内）/)).toBeVisible();
    const firstItem = page.locator("aside li").first();
    await expect(firstItem).toBeVisible({ timeout: 15_000 });
    const firstName = (await firstItem.locator(".truncate").textContent())?.trim() ?? "";

    // 点「生活圈详情」→ 评估页带 from=nearby
    await page.getByRole("link", { name: "生活圈详情" }).first().click();
    await expect(page).toHaveURL(/\/evaluate\?.*from=nearby/);

    // URL 带坐标 → 自动跑体检出分
    await expect(page.getByText("生活圈评分", { exact: true })).toBeVisible({ timeout: 45_000 });

    // 回退键可选 → 返回后列表状态保留（sessionStorage 恢复，首项一致）
    const back = page.getByRole("button", { name: "返回附近小区" });
    await expect(back).toBeEnabled();
    await back.click();
    await expect(page).toHaveURL(/\/nearby$/);
    const restored = page.locator("aside li").first();
    await expect(restored).toBeVisible({ timeout: 15_000 });
    if (firstName) {
      await expect(restored.locator(".truncate")).toHaveText(firstName);
    }
  });
});
