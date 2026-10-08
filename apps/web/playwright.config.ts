import { defineConfig } from "@playwright/test";

// E2E 冒烟（不进入 CI）。
//
// 端口说明：本地 3000 常被占用（如 WSL 转发），E2E 固定使用 3100。
// Web 以 NEXT_PUBLIC_API_BASE=http://localhost:3100 启动，浏览器对 /api/* 的
// 请求由测试内的 page.route 代理转发到 8000（服务端转发，绕过 CORS），
// 因此无需改动 API 的 CORS 配置。
//
// 推荐用法：先按 README 四步起好 API(8000)，再执行 `npx playwright test`。
// 若 8000 无服务，下方 webServer 会尝试用全局 python 起 uvicorn（需已 pip 安装依赖）。
export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  retries: 0,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: "http://localhost:3100",
    viewport: { width: 1440, height: 900 },
  },
  webServer: [
    {
      command: "python -m uvicorn app.main:app --port 8000",
      cwd: "../api",
      url: "http://localhost:8000/health",
      env: { DEMO_MODE: "true" },
      reuseExistingServer: true,
      timeout: 120_000,
    },
    {
      command: "npm run dev -- -p 3100",
      url: "http://localhost:3100",
      // NEXT_DIST_DIR 独立：避免 E2E 的 3100 烘焙产物污染本地 dev 的 .next 缓存
      env: { NEXT_PUBLIC_API_BASE: "http://localhost:3100", NEXT_DIST_DIR: ".next-e2e" },
      reuseExistingServer: true,
      timeout: 180_000,
    },
  ],
});
