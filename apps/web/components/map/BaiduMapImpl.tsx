"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  BUILDING_COLORS,
  POI_CATEGORY_META,
  WALK_SPEED_M_PER_MIN,
  isoColor,
} from "@/lib/constants";
import { METERS_PER_DEG_LAT, featureToRings } from "@/lib/geo";
import type {
  CheckupResponse,
  FabricResponse,
  GeoFeature,
  LayerVisibility,
  LngLat,
  Poi,
} from "@/lib/types";
import type { MapImplProps } from "./mapTypes";
import CommunityInfoBox from "./CommunityInfoBox";

function defaultZoom(minutes: number): number {
  const r = minutes * WALK_SPEED_M_PER_MIN;
  if (r <= 400) return 17;
  if (r <= 620) return 16.5;
  if (r <= 900) return 16;
  if (r <= 1300) return 15.5;
  if (r <= 1800) return 15;
  return 14.5;
}

/** 倒圆锥高亮符号：锥尖指向设施点，深天蓝渐变 3D 锥面 + 底面椭圆，无边框线条。
 *  动画：每秒 1.5 次微微上下跳动（无旋转）。 */
function drawCone(ctx: CanvasRenderingContext2D, x: number, y: number, t: number) {
  const r = 11; // 底面半径
  const h = 30; // 锥高
  const ry = r * 0.38; // 底面椭圆纵半径（透视）
  // 每秒 1.5 次跳动（|sin| 周期 π/1.5π = 2/3 s）
  const bounce = Math.abs(Math.sin(1.5 * Math.PI * t));
  const dy = -6 * bounce;
  const top = y - h + dy;
  const tipY = y + dy;

  // 地面投影：跳得越高影子越小
  ctx.beginPath();
  ctx.ellipse(x, y + 2, r * (0.55 + 0.35 * (1 - bounce)), ry * 0.9, 0, 0, Math.PI * 2);
  ctx.fillStyle = "rgba(15, 23, 42, 0.18)";
  ctx.fill();

  // 锥身：深天蓝横向渐变模拟 3D 光照
  const grad = ctx.createLinearGradient(x - r, 0, x + r, 0);
  grad.addColorStop(0, "#075985");
  grad.addColorStop(0.45, "#0284c7");
  grad.addColorStop(0.8, "#0ea5e9");
  grad.addColorStop(1, "#0369a1");
  ctx.beginPath();
  ctx.ellipse(x, top, r, ry, 0, 0, Math.PI); // 右缘 → 下弧 → 左缘
  ctx.lineTo(x, tipY);
  ctx.closePath();
  ctx.fillStyle = grad;
  ctx.fill();

  // 底面（顶视椭圆开口）
  ctx.beginPath();
  ctx.ellipse(x, top, r, ry, 0, 0, Math.PI * 2);
  ctx.fillStyle = "#38bdf8";
  ctx.fill();
}

/** POI 圆点半径（米），与原百度 Circle 保持一致。 */
const POI_RADIUS_M = 16;
const NO_DASH: number[] = [];

interface OpStyle {
  fill?: string;
  fillAlpha?: number;
  stroke?: string;
  strokeAlpha?: number;
  lineWidth?: number;
  dash?: number[];
}

/**
 * 一条可绘制图元。路径按「经纬度空间」构建一次并缓存成 Path2D，
 * 每帧只改画布的变换矩阵即可完成平移/缩放，不再重建上万个路径。
 */
interface DrawOp extends OpStyle {
  path: Path2D;
  /** [minLng, minLat, maxLng, maxLat]，用于视野裁剪 */
  bbox: [number, number, number, number];
}

function bboxOf(coords: number[][][]): [number, number, number, number] {
  let minLng = Infinity;
  let minLat = Infinity;
  let maxLng = -Infinity;
  let maxLat = -Infinity;
  for (const part of coords) {
    for (const pt of part) {
      if (pt[0] < minLng) minLng = pt[0];
      if (pt[1] < minLat) minLat = pt[1];
      if (pt[0] > maxLng) maxLng = pt[0];
      if (pt[1] > maxLat) maxLat = pt[1];
    }
  }
  return [minLng, minLat, maxLng, maxLat];
}

function polyOp(rings: number[][][], style: OpStyle): DrawOp | null {
  const usable = rings.filter((r) => r.length >= 3);
  if (!usable.length) return null;
  const path = new Path2D();
  for (const ring of usable) {
    path.moveTo(ring[0][0], ring[0][1]);
    for (let i = 1; i < ring.length; i++) path.lineTo(ring[i][0], ring[i][1]);
    path.closePath();
  }
  return { path, bbox: bboxOf(usable), ...style };
}

function lineOp(lines: number[][][], style: OpStyle): DrawOp | null {
  const usable = lines.filter((l) => l.length >= 2);
  if (!usable.length) return null;
  const path = new Path2D();
  for (const line of usable) {
    path.moveTo(line[0][0], line[0][1]);
    for (let i = 1; i < line.length; i++) path.lineTo(line[i][0], line[i][1]);
  }
  return { path, bbox: bboxOf(usable), ...style };
}

/** 依据当前图层开关与数据，重建全部绘制图元。 */
function buildOps(
  fabric: FabricResponse | null,
  result: CheckupResponse | null,
  fastLayer: GeoFeature | null,
  showFast: boolean,
  visible: LayerVisibility
): DrawOp[] {
  const ops: DrawOp[] = [];
  const push = (op: DrawOp | null) => {
    if (op) ops.push(op);
  };

  if (visible.water && fabric) {
    for (const f of fabric.water.features) {
      push(
        polyOp(featureToRings(f), {
          fill: "#bfdbfe",
          fillAlpha: 0.75,
          stroke: "#93c5fd",
          lineWidth: 1,
        })
      );
    }
  }

  // OSM 建筑空洞占位面：半透明浅灰 + 虚线描边，压在真实建筑下方
  if (visible.buildings && fabric?.residential) {
    for (const f of fabric.residential.features) {
      push(
        polyOp(featureToRings(f), {
          fill: "#94a3b8",
          fillAlpha: 0.12,
          stroke: "#64748b",
          strokeAlpha: 0.7,
          lineWidth: 1.5,
          dash: [6, 4],
        })
      );
    }
  }

  if (visible.buildings && fabric) {
    for (const f of fabric.buildings.features) {
      const cat = (f.properties?.category as string) ?? "blocked";
      const style = BUILDING_COLORS[cat] ?? BUILDING_COLORS.blocked;
      const transparent = style.fill === "transparent";
      push(
        polyOp(featureToRings(f), {
          fill: transparent ? undefined : style.fill,
          fillAlpha: transparent ? 0 : (style.fillOpacity ?? 0.6),
          stroke: style.stroke ?? "#d6d3d1",
          lineWidth: style.stroke ? 1.5 : 0.5,
        })
      );
    }
  }

  if (visible.roads && fabric) {
    for (const f of fabric.roads.features) {
      const bridge = Boolean(f.properties?.bridge);
      push(
        lineOp(featureToRings(f), {
          stroke: bridge ? "#52525b" : "#a8a29e",
          lineWidth: bridge ? 4 : 2,
        })
      );
    }
  }

  if (visible.barriers && fabric) {
    for (const f of fabric.barriers.features) {
      push(
        lineOp(featureToRings(f), {
          stroke: "#ef4444",
          lineWidth: 3,
          dash: [8, 6],
        })
      );
    }
  }

  if (visible.isochrone && fastLayer && showFast) {
    push(
      polyOp(featureToRings(fastLayer), {
        fill: "#0891b2",
        fillAlpha: 0.18,
        stroke: "#0891b2",
        lineWidth: 2,
        dash: [8, 6],
      })
    );
  }

  if (visible.isochrone && result) {
    const asc = [...result.layers].sort((a, b) => a.minutes - b.minutes);
    const outerFirst = [...result.layers].sort((a, b) => b.minutes - a.minutes);
    for (const layer of outerFirst) {
      const idx = asc.findIndex((x) => x.minutes === layer.minutes);
      const color = isoColor(Math.max(0, idx), Math.max(1, asc.length));
      push(
        polyOp(featureToRings(layer.polygon), {
          fill: color,
          fillAlpha: 0.38,
          stroke: color,
          // 1px 细描边：分层靠色相区分即可，粗描边会让边缘显得粗糙
          lineWidth: 1,
        })
      );
    }
    for (const f of result.enclaves) {
      push(
        polyOp(featureToRings(f), {
          fill: "#fbbf24",
          fillAlpha: 0.3,
          stroke: "#d97706",
          lineWidth: 1.5,
          dash: [8, 6],
        })
      );
    }
  }

  if (visible.baiduRoutes && result?.baiduRoutes) {
    const colors = ["#ea580c", "#7c3aed"];
    result.baiduRoutes.forEach((route, i) => {
      push(
        lineOp([route.path], {
          stroke: colors[i % colors.length],
          lineWidth: 3,
          dash: [8, 6],
        })
      );
    });
  }

  return ops;
}

export default function BaiduMapImpl(props: MapImplProps) {
  const {
    center,
    minutes,
    anchor,
    fabric,
    fastLayer,
    showFast,
    result,
    visible,
    highlightPoi,
    communityInfo,
    communityDistanceM,
    onCloseCommunityInfo,
  } = props;

  const containerRef = useRef<HTMLDivElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const markerHitRef = useRef<HTMLDivElement | null>(null);
  const popupRef = useRef<HTMLDivElement | null>(null);
  const communityBoxRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<any>(null);
  const opsRef = useRef<DrawOp[]>([]);
  const rafRef = useRef<number | null>(null);
  // 当前视野投影（经纬度 → 画布 CSS 像素），每次重绘后刷新
  const projRef = useRef<{ x0: number; y0: number; sx: number; sy: number } | null>(null);
  // 拖拽中心点时的临时位置（未提交前覆盖 props.center）
  const dragRef = useRef<LngLat | null>(null);
  const draggingRef = useRef(false);

  const [selectedPoi, setSelectedPoi] = useState<Poi | null>(null);
  const selectedPoiRef = useRef<Poi | null>(null);
  selectedPoiRef.current = selectedPoi;

  const propsRef = useRef(props);
  propsRef.current = props;

  // 高亮圆锥动画时钟（秒）
  const animTRef = useRef(0);

  /** 命中检测：点击位置附近的 POI。 */
  const hitTestPoi = useCallback((latlng: LngLat): Poi | null => {
    const proj = projRef.current;
    const p = propsRef.current;
    const pois = p.result?.pois;
    if (!proj || !pois || !p.visible.pois) return null;
    const x = (latlng.lng - proj.x0) * proj.sx;
    const y = (proj.y0 - latlng.lat) * proj.sy;
    const rPx = (POI_RADIUS_M * proj.sy) / METERS_PER_DEG_LAT;
    const tol = Math.max(12, rPx + 6);
    let best: Poi | null = null;
    let bestDist = tol;
    for (const poi of pois) {
      const d = Math.hypot(
        (poi.lng - proj.x0) * proj.sx - x,
        (proj.y0 - poi.lat) * proj.sy - y
      );
      if (d <= bestDist) {
        bestDist = d;
        best = poi;
      }
    }
    return best;
  }, []);

  /**
   * 全量重绘。百度的底图与手势完全不动，所有矢量由我们自己画到 canvas 上，
   * 因此不会再产生成千上万个百度 overlay 对象。
   */
  const draw = useCallback(() => {
    const map = mapRef.current;
    const canvas = canvasRef.current;
    if (!map || !canvas) return;
    const p = propsRef.current;

    const size = map.getSize();
    const w = Math.round(size.width);
    const h = Math.round(size.height);
    if (!(w > 0) || !(h > 0)) return;

    const dpr = window.devicePixelRatio || 1;
    if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
      canvas.width = Math.round(w * dpr);
      canvas.height = Math.round(h * dpr);
    }
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const bounds = map.getBounds();
    const sw = bounds.getSouthWest();
    const ne = bounds.getNorthEast();
    const x0 = sw.lng;
    const y0 = ne.lat;
    const spanLng = ne.lng - sw.lng;
    const spanLat = y0 - sw.lat;
    if (!(spanLng > 0) || !(spanLat > 0)) return;

    const sx = w / spanLng;
    const sy = h / spanLat;
    projRef.current = { x0, y0, sx, sy };
    const px = (lng: number) => (lng - x0) * sx;
    const py = (lat: number) => (y0 - lat) * sy;

    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    // 缓存路径建在经纬度空间，这里把经纬度线性映射到像素（城市尺度下误差 < 1px）
    ctx.setTransform(dpr * sx, 0, 0, -dpr * sy, -dpr * x0 * sx, dpr * y0 * sy);
    const padLng = spanLng * 0.05;
    const padLat = spanLat * 0.05;
    for (const op of opsRef.current) {
      const [minLng, minLat, maxLng, maxLat] = op.bbox;
      if (maxLng < x0 - padLng || minLng > x0 + spanLng + padLng) continue;
      if (maxLat < y0 - spanLat - padLat || minLat > y0 + padLat) continue;
      if (op.fill) {
        ctx.globalAlpha = op.fillAlpha ?? 1;
        ctx.fillStyle = op.fill;
        ctx.fill(op.path, "evenodd");
      }
      if (op.stroke) {
        ctx.globalAlpha = op.strokeAlpha ?? 1;
        ctx.strokeStyle = op.stroke;
        // 圆角连接：多边形边界（尤其缓冲斑边缘）miter 直角会显得尖锐粗糙
        ctx.lineJoin = "round";
        ctx.lineCap = "round";
        // 变换里含 sx 倍缩放，这里换回 CSS 像素宽度
        ctx.lineWidth = (op.lineWidth ?? 1) / sx;
        ctx.setLineDash(op.dash ? op.dash.map((v) => v / sx) : NO_DASH);
        ctx.stroke(op.path);
      }
    }
    ctx.globalAlpha = 1;
    ctx.setLineDash(NO_DASH);

    // 以下为屏幕空间绘制（不随缩放改变大小）
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    if (p.visible.straightCircle && p.result) {
      const r = (p.result.straightCircle.radiusM * sy) / METERS_PER_DEG_LAT;
      ctx.beginPath();
      ctx.arc(px(p.anchor.lng), py(p.anchor.lat), r, 0, Math.PI * 2);
      ctx.setLineDash([6, 5]);
      ctx.strokeStyle = "#94a3b8";
      ctx.lineWidth = 2;
      ctx.stroke();
      ctx.setLineDash(NO_DASH);
    }

    // POI 圆点：世界半径随缩放变化，但保证最小 4px 屏幕半径，
    // 否则整圈视野下 16m 点只有约 5px，图例与地图对不上
    const rWorld = (POI_RADIUS_M * sy) / METERS_PER_DEG_LAT;
    const rPx = Math.max(4, rWorld);
    if (p.visible.pois && p.result) {
      for (const poi of p.result.pois) {
        const x = px(poi.lng);
        const y = py(poi.lat);
        if (x < -40 || y < -40 || x > w + 40 || y > h + 40) continue;
        const meta = POI_CATEGORY_META[poi.category];
        ctx.beginPath();
        ctx.arc(x, y, rPx, 0, Math.PI * 2);
        ctx.globalAlpha = poi.within ? 0.95 : 0.55;
        ctx.fillStyle = meta?.color ?? "#64748b";
        ctx.fill();
        ctx.globalAlpha = 1;
        ctx.strokeStyle = "#ffffff";
        ctx.lineWidth = 1.5;
        ctx.stroke();
      }
    }

    // 中心标记（拖拽中跟随临时位置）
    const mc = dragRef.current ?? p.center;
    const mx = px(mc.lng);
    const my = py(mc.lat);
    ctx.beginPath();
    ctx.moveTo(mx, my);
    ctx.lineTo(mx, my - 14);
    ctx.strokeStyle = "#be123c";
    ctx.lineWidth = 3;
    ctx.stroke();
    ctx.beginPath();
    ctx.arc(mx, my - 20, 8, 0, Math.PI * 2);
    ctx.fillStyle = "#e11d48";
    ctx.fill();
    ctx.strokeStyle = "#ffffff";
    ctx.lineWidth = 2;
    ctx.stroke();

    // 高亮设施（报告栏点选）：天蓝色 3D 倒圆锥（跳动+旋转）+ 名称标签，屏幕空间不随缩放变形
    const hl = p.highlightPoi;
    if (hl) {
      const hx = px(hl.lng);
      const hy = py(hl.lat);
      if (hx > -80 && hy > -100 && hx < w + 80 && hy < h + 100) {
        drawCone(ctx, hx, hy, animTRef.current);
        ctx.font = "600 12px ui-sans-serif, system-ui, sans-serif";
        const tw = ctx.measureText(hl.name).width;
        const lx = hx + 16;
        const ly = hy - 44;
        ctx.fillStyle = "rgba(255, 255, 255, 0.95)";
        ctx.strokeStyle = "#0284c7";
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        ctx.rect(lx, ly, tw + 14, 22);
        ctx.fill();
        ctx.stroke();
        ctx.fillStyle = "#1e293b";
        ctx.fillText(hl.name, lx + 7, ly + 15);
      }
    }

    const hit = markerHitRef.current;
    if (hit) {
      hit.style.display = "block";
      hit.style.left = `${mx - 15}px`;
      hit.style.top = `${my - 42}px`;
    }

    // 小区资讯框：定位点右侧；越界时翻到左侧
    const cbox = communityBoxRef.current;
    if (cbox) {
      const bw = cbox.offsetWidth || 240;
      const bh = cbox.offsetHeight || 300;
      const left = mx + 24 + bw > w ? Math.max(8, mx - 24 - bw) : mx + 24;
      const top = Math.max(8, Math.min(my - 24, h - bh - 8));
      cbox.style.display = "block";
      cbox.style.left = `${left}px`;
      cbox.style.top = `${top}px`;
    }

    const poi = selectedPoiRef.current;
    const popup = popupRef.current;
    if (popup) {
      if (poi) {
        popup.style.display = "block";
        popup.style.left = `${px(poi.lng)}px`;
        popup.style.top = `${py(poi.lat) - rPx - 8}px`;
      } else {
        popup.style.display = "none";
      }
    }
  }, []);

  const drawRef = useRef(draw);
  drawRef.current = draw;

  // ---- init once ----
  // 注意：绝不能调用 map.destroy()。BMapGL 的 destroy() 会拆页面级共享内部状态，
  // 同页面此后再 new 的地图会坏掉内部鼠标机制（markerMouseTarget），表现为
  // 底图正常渲染但左键点击/拖拽全部失效——dev 热更新（HMR）触发组件重挂载
  // 时必现。因此卸载时只移除监听 + 清空容器 DOM，放任地图对象随页面回收。
  useEffect(() => {
    const B = window.BMapGL;
    const el = containerRef.current;
    if (!B || !el) return;
    el.innerHTML = "";
    const map = new B.Map(el, {
      minZoom: 5,
      maxZoom: 19,
      enableMapClick: true,
    });
    map.centerAndZoom(
      new B.Point(propsRef.current.center.lng, propsRef.current.center.lat),
      defaultZoom(propsRef.current.minutes)
    );
    map.enableScrollWheelZoom();

    // 地图只负责底图与手势；矢量全部自绘，因此这里不再 addOverlay
    const schedule = () => {
      if (rafRef.current !== null) return;
      rafRef.current = requestAnimationFrame(() => {
        rafRef.current = null;
        drawRef.current();
      });
    };
    const events = [
      "movestart",
      "moving",
      "moveend",
      "zoomstart",
      "zooming",
      "zoomend",
    ];
    events.forEach((ev) => map.addEventListener(ev, schedule));

    map.addEventListener("click", (e: any) => {
      const latlng = e?.latlng;
      if (!latlng) return;
      const hit = hitTestPoi({ lng: latlng.lng, lat: latlng.lat });
      if (hit) {
        setSelectedPoi(hit);
        return;
      }
      setSelectedPoi(null);
      propsRef.current.onCenterChange({ lng: latlng.lng, lat: latlng.lat });
    });

    const ro = new ResizeObserver(() => drawRef.current());
    ro.observe(el);

    mapRef.current = map;
    // 调试/E2E 钩子：暴露地图实例，供 Playwright 验证拖拽/点击是否真正生效
    (window as any).__livemapMap = map;
    drawRef.current();

    return () => {
      events.forEach((ev) => map.removeEventListener(ev, schedule));
      ro.disconnect();
      if (rafRef.current !== null) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }
      if (mapRef.current === map) mapRef.current = null;
      // 故意不调用 map.destroy()（原因见上方注释），仅清掉挂载点 DOM
      el.innerHTML = "";
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ---- 数据/开关变化：重建图元并重绘 ----
  useEffect(() => {
    opsRef.current = buildOps(fabric, result, fastLayer, showFast, visible);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fabric, result, fastLayer, showFast, visible, anchor.lng, anchor.lat]);

  // 中心点、选中态、高亮设施、资讯框变化后需要重绘（标记/弹窗/高亮锥/资讯框位置都在 draw 里更新）
  useEffect(() => {
    drawRef.current();
  }, [
    center.lng,
    center.lat,
    selectedPoi,
    highlightPoi,
    communityInfo,
    fabric,
    result,
    fastLayer,
    showFast,
    visible,
  ]);

  // 高亮设施不在当前视野内时平移过去
  useEffect(() => {
    const B = window.BMapGL;
    const map = mapRef.current;
    if (!B || !map || !highlightPoi) return;
    try {
      const bounds = map.getBounds();
      const sw = bounds.getSouthWest();
      const ne = bounds.getNorthEast();
      const inside =
        highlightPoi.lng > sw.lng &&
        highlightPoi.lng < ne.lng &&
        highlightPoi.lat > sw.lat &&
        highlightPoi.lat < ne.lat;
      if (!inside) {
        map.panTo(new B.Point(highlightPoi.lng, highlightPoi.lat));
      }
    } catch {
      // 视野未就绪时忽略
    }
  }, [highlightPoi]);

  // 高亮圆锥动画循环：rAF 驱动跳动（3Hz）+ 周期旋转
  useEffect(() => {
    if (!highlightPoi) return;
    let raf = 0;
    const t0 = performance.now();
    const tick = (now: number) => {
      animTRef.current = (now - t0) / 1000;
      drawRef.current();
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => {
      cancelAnimationFrame(raf);
      animTRef.current = 0;
    };
  }, [highlightPoi]);

  // ---- 中心标记拖拽（视觉由 canvas 绘制，透明命中区只负责拖）----
  const onMarkerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.stopPropagation();
    (e.currentTarget as Element).setPointerCapture?.(e.pointerId);
    draggingRef.current = true;
    dragRef.current = propsRef.current.center;
  };
  const onMarkerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!draggingRef.current) return;
    const proj = projRef.current;
    const canvas = canvasRef.current;
    if (!proj || !canvas) return;
    const rect = canvas.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;
    dragRef.current = { lng: proj.x0 + x / proj.sx, lat: proj.y0 - y / proj.sy };
    drawRef.current();
  };
  const onMarkerUp = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!draggingRef.current) return;
    draggingRef.current = false;
    (e.currentTarget as Element).releasePointerCapture?.(e.pointerId);
    const next = dragRef.current;
    dragRef.current = null;
    if (next) propsRef.current.onCenterChange(next);
    drawRef.current();
  };

  // ---- fit viewport ----
  useEffect(() => {
    const B = window.BMapGL;
    const map = mapRef.current;
    if (!B || !map) return;
    if (result && result.layers.length > 0) {
      const outer = [...result.layers].sort((a, b) => b.minutes - a.minutes)[0];
      const rings = featureToRings(outer.polygon);
      const pts = rings.flat().map((p) => new B.Point(p[0], p[1]));
      if (pts.length > 2) {
        try {
          map.setViewport(pts, { margins: [80, 40, 40, 40] });
          return;
        } catch {
          // fall through
        }
      }
    }
    if (!result) {
      map.centerAndZoom(new B.Point(center.lng, center.lat), defaultZoom(minutes));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [result, anchor.lng, anchor.lat]);

  const poiMeta = selectedPoi ? POI_CATEGORY_META[selectedPoi.category] : null;

  return (
    <div className="relative h-full w-full">
      <div ref={containerRef} className="h-full w-full" />
      <canvas ref={canvasRef} className="pointer-events-none absolute inset-0" />
      {/* 中心标记的透明命中区（视觉已在 canvas 上，这里只处理拖拽） */}
      <div
        ref={markerHitRef}
        className="absolute z-10 h-[46px] w-[30px] cursor-grab active:cursor-grabbing"
        style={{ display: "none" }}
        onPointerDown={onMarkerDown}
        onPointerMove={onMarkerMove}
        onPointerUp={onMarkerUp}
        onPointerCancel={onMarkerUp}
      />
      {selectedPoi && (
        <div
          ref={popupRef}
          className="absolute z-20 w-52 -translate-x-1/2 -translate-y-full rounded-lg border border-slate-200 bg-white p-3 shadow-lg"
          style={{ display: "none" }}
        >
          <div className="flex items-start justify-between gap-2">
            <div className="text-sm font-semibold text-slate-800">
              {selectedPoi.name}
            </div>
            <button
              type="button"
              aria-label="关闭"
              className="text-slate-400 hover:text-slate-600"
              onClick={() => setSelectedPoi(null)}
            >
              ✕
            </button>
          </div>
          <div className="mt-1 text-xs text-slate-500">
            {poiMeta?.name ?? selectedPoi.category} · 步行{" "}
            {selectedPoi.walkMinutes} 分钟
          </div>
          <div className="mt-2 text-sm">
            {selectedPoi.within ? (
              <span className="text-green-700">在圈内</span>
            ) : (
              <span className="text-red-700">在圈外</span>
            )}
          </div>
        </div>
      )}
      {communityInfo && (
        <CommunityInfoBox
          ref={communityBoxRef}
          info={communityInfo}
          distanceM={communityDistanceM ?? undefined}
          onClose={onCloseCommunityInfo}
          className="absolute z-30"
        />
      )}
    </div>
  );
}