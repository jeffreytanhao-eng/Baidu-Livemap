import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // 编译产物目录可通过 NEXT_DIST_DIR 覆盖：E2E（playwright webServer，3100 端口 +
  // NEXT_PUBLIC_API_BASE=3100）与本地 dev（3000 + .env.local→8000）共用 .next 时，
  // E2E 编译的 chunk 会把 3100 烘焙进缓存并被 dev 复用，导致页面请求打到无人监听的
  // 3100 端口（"Failed to fetch"）。E2E 使用独立目录 .next-e2e 隔离（见 playwright.config.ts）。
  distDir: process.env.NEXT_DIST_DIR || ".next",
  // Baidu GL Map 的 destroy() 会拆共享内部状态，StrictMode 的 dev 双挂载会让
  // 第二次 new Map 崩在百度内部（'undefined' / markerMouseTarget），地图空白。
  reactStrictMode: false,
  eslint: {
    // lint is run separately via `npm run lint`
    ignoreDuringBuilds: true,
  },
};

export default nextConfig;
