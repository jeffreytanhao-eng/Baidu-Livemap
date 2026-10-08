import { expect, test } from "@playwright/test";

// 地图左键交互冒烟：拖拽平移 + 点击定位 + 无未捕获异常。
// 背景：BMapGL 的 destroy() 会拆页面级共享内部状态，重挂载后的新地图
// 「底图正常但左键全部失效」（markerMouseTarget 崩坏）。本用例保证
// 百度模式下鼠标机制健康：拖拽能改变地图中心、点击能触发定位链路。

test("地图左键拖拽与点击定位", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  await page.goto("/evaluate");

  // 等地图模式落定（baidu / svg）
  const mode = await page.evaluate(
    () =>
      new Promise<string>((resolve) => {
        const started = Date.now();
        const tick = () => {
          if ((window as any).__livemapMap) return resolve("baidu");
          if (document.querySelector("svg.cursor-crosshair")) return resolve("svg");
          if (Date.now() - started > 15000) return resolve("none");
          setTimeout(tick, 200);
        };
        tick();
      })
  );
  expect(mode, "应进入百度或 SVG 地图模式").not.toBe("none");

  if (mode === "baidu") {
    const readCenter = () =>
      page.evaluate(() => {
        const c = (window as any).__livemapMap.getCenter();
        return { lng: c.lng as number, lat: c.lat as number };
      });

    const box = await page.locator("#map-section").boundingBox();
    expect(box).not.toBeNull();
    const cx = box!.x + box!.width / 2;
    const cy = box!.y + box!.height / 2;

    const before = await readCenter();

    // 左键按住拖拽 → 底图应平移（地图中心改变）。
    // 起点偏移中心 120px：正中心压着中心标记的透明命中区（拖它=拖图钉）。
    const sx = cx + 120;
    const sy = cy + 120;
    await page.mouse.move(sx, sy);
    await page.mouse.down();
    for (let i = 1; i <= 10; i++) {
      await page.mouse.move(sx + i * 24, sy - i * 12, { steps: 2 });
    }
    await page.mouse.up();
    await page.waitForTimeout(900); // 等惯性结束

    const after = await readCenter();
    const moved = Math.hypot(after.lng - before.lng, after.lat - before.lat);
    expect(moved, "左键拖拽应改变地图中心").toBeGreaterThan(0.0005);

    // 左键点击 → 要么命中 POI（弹卡片），要么走 onCenterChange →
    // 小区定位请求。两者都证明点击事件到达了地图。
    let locateSeen = false;
    const onReq = (r: { url(): string }) => {
      if (r.url().includes("/api/v1/community/locate")) locateSeen = true;
    };
    page.on("request", onReq);
    const [locReq] = await Promise.all([
      page
        .waitForRequest(/api\/v1\/community\/locate/, { timeout: 6000 })
        .catch(() => null),
      page.mouse.click(cx - 180, cy - 140),
    ]);
    const poiPopup = await page
      .getByText(/在圈内|在圈外/)
      .first()
      .isVisible()
      .catch(() => false);
    expect(
      locReq !== null || poiPopup || locateSeen,
      "左键点击应触发定位链路（locate 请求）或 POI 卡片"
    ).toBeTruthy();
  } else {
    console.warn("当前为 SVG 兜底模式，跳过百度拖拽断言");
  }

  // 无未捕获异常：BMapGL 内部崩坏（如 markerMouseTarget）会在这里暴露
  expect(errors, `页面存在未捕获异常: ${errors.join(" | ")}`).toHaveLength(0);
});
