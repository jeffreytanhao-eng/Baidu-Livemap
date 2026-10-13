import { expect, test } from "@playwright/test";

// DPI 缩放点击偏移复现：deviceScaleFactor=1.25（模拟 Windows 125% 系统缩放）下，
// 点击地图某像素位置，locate 请求的经纬度应与 getBounds 线性投影的期望值一致。

async function probe(browser: any, dsf: number) {
  const ctx = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    deviceScaleFactor: dsf,
  });
  const page = await ctx.newPage();
  await page.goto("/evaluate?lat=39.9149&lng=116.4595&minutes=15");

  const mode = await page.evaluate(
    () =>
      new Promise<string>((resolve) => {
        const started = Date.now();
        const tick = () => {
          if ((window as any).__livemapMap) return resolve("baidu");
          if (document.querySelector("svg.cursor-crosshair")) return resolve("svg");
          if (Date.now() - started > 20000) return resolve("none");
          setTimeout(tick, 200);
        };
        tick();
      })
  );
  if (mode !== "baidu") {
    await ctx.close();
    return { mode, offsets: [] as { dx: number; dy: number; errM: number }[] };
  }

  const box = await page.locator("#map-section").boundingBox();
  const cx = box!.x + box!.width / 2;
  const cy = box!.y + box!.height / 2;

  const offsets: { dx: number; dy: number; errM: number }[] = [];
  for (const [dx, dy] of [
    [-160, -110],
    [190, 130],
    [-210, 150],
  ] as const) {
    const view = await page.evaluate(() => {
      const m = (window as any).__livemapMap;
      const b = m.getBounds();
      const s = m.getSize();
      return {
        swLng: b.sw.lng,
        neLng: b.ne.lng,
        swLat: b.sw.lat,
        neLat: b.ne.lat,
        w: s.width,
        h: s.height,
        dpr: window.devicePixelRatio,
      };
    });
    const px = cx + dx;
    const py = cy + dy;
    // 期望值基于 DOM 实测几何（boundingBox 为 CSS 像素），与 GL 内部报告解耦
    const expLng =
      view.swLng + ((px - box!.x) / box!.width) * (view.neLng - view.swLng);
    const expLat =
      view.neLat - ((py - box!.y) / box!.height) * (view.neLat - view.swLat);
    console.log(
      `dsf 实测 dpr=${view.dpr} getSize=(${view.w}x${view.h}) domBox=(${Math.round(box!.width)}x${Math.round(box!.height)})`
    );

    const [req] = await Promise.all([
      page
        .waitForRequest(/api\/v1\/community\/locate/, { timeout: 5000 })
        .catch(() => null),
      page.mouse.click(px, py),
    ]);
    if (!req) continue; // 可能命中 POI 卡片，换位置重试
    const url = new URL(req.url());
    const gotLng = parseFloat(url.searchParams.get("lng") || "");
    const gotLat = parseFloat(url.searchParams.get("lat") || "");
    if (Number.isNaN(gotLng) || Number.isNaN(gotLat)) continue;
    const errM = Math.hypot(
      (gotLng - expLng) * 111320 * Math.cos((expLat * Math.PI) / 180),
      (gotLat - expLat) * 110540
    );
    offsets.push({ dx, dy, errM });
  }
  await ctx.close();
  return { mode, offsets };
}

test("点击经纬度在高 DPI 下不偏移", async ({ browser }) => {
  const base = await probe(browser, 1);
  const hi = await probe(browser, 1.25);
  console.log(`dsf=1    mode=${base.mode} offsets=${JSON.stringify(base.offsets)}`);
  console.log(`dsf=1.25 mode=${hi.mode} offsets=${JSON.stringify(hi.offsets)}`);
  expect(base.mode, "应进入百度模式").toBe("baidu");
  expect(hi.mode, "应进入百度模式").toBe("baidu");
  expect(base.offsets.length, "100% 缩放应采集到点击样本").toBeGreaterThan(0);
  expect(hi.offsets.length, "125% 缩放应采集到点击样本").toBeGreaterThan(0);
  for (const o of base.offsets)
    expect(o.errM, `dsf=1 偏差过大 dx=${o.dx} dy=${o.dy}`).toBeLessThan(40);
  for (const o of hi.offsets)
    expect(o.errM, `dsf=1.25 偏差过大 dx=${o.dx} dy=${o.dy}`).toBeLessThan(40);
});
