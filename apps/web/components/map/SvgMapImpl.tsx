"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  BUILDING_COLORS,
  POI_CATEGORY_META,
  WALK_SPEED_M_PER_MIN,
  isoColor,
} from "@/lib/constants";
import { featureToPathData, fromMeters, toMeters } from "@/lib/geo";
import type { GeoFeature, LngLat, Poi } from "@/lib/types";
import type { MapImplProps } from "./mapTypes";
import CommunityInfoBox from "./CommunityInfoBox";

interface ViewState {
  cx: number; // meter-space (svg coords) center x
  cy: number;
  k: number; // zoom factor
}

export default function SvgMapImpl(props: MapImplProps) {
  const {
    center,
    minutes,
    anchor,
    fabric,
    fastLayer,
    showFast,
    result,
    visible,
    onCenterChange,
    highlightPoi,
    communityInfo,
    communityDistanceM,
    onCloseCommunityInfo,
  } = props;

  const svgRef = useRef<SVGSVGElement | null>(null);
  const [size, setSize] = useState({ w: 800, h: 600 });
  const baseRadius = result?.straightCircle.radiusM ?? minutes * WALK_SPEED_M_PER_MIN;
  const baseHalf = baseRadius * 1.25;

  const [view, setView] = useState<ViewState>({ cx: 0, cy: 0, k: 1 });
  // reset view when anchor / base radius changes
  useEffect(() => {
    setView({ cx: 0, cy: 0, k: 1 });
  }, [anchor.lng, anchor.lat, baseRadius]);

  const [popup, setPopup] = useState<Poi | null>(null);
  const [dragPos, setDragPos] = useState<LngLat | null>(null);

  // ---- container size ----
  useEffect(() => {
    const el = svgRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => {
      setSize({ w: el.clientWidth || 800, h: el.clientHeight || 600 });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const halfH = baseHalf / view.k;
  const halfW = halfH * (size.w / size.h);
  const viewBox = `${view.cx - halfW} ${view.cy - halfH} ${2 * halfW} ${2 * halfH}`;

  const screenToLngLat = useCallback(
    (clientX: number, clientY: number): LngLat => {
      const el = svgRef.current!;
      const rect = el.getBoundingClientRect();
      const mx = view.cx + ((clientX - rect.left) / rect.width - 0.5) * 2 * halfW;
      const my = view.cy + ((clientY - rect.top) / rect.height - 0.5) * 2 * halfH;
      return fromMeters(mx, -my, anchor);
    },
    [view.cx, view.cy, halfW, halfH, anchor]
  );

  // ---- pan & click ----
  const dragRef = useRef<{
    startX: number;
    startY: number;
    cx: number;
    cy: number;
    moved: boolean;
    marker: boolean;
  } | null>(null);

  const onPointerDown = (e: React.PointerEvent<SVGSVGElement>) => {
    (e.target as Element).setPointerCapture?.(e.pointerId);
    dragRef.current = {
      startX: e.clientX,
      startY: e.clientY,
      cx: view.cx,
      cy: view.cy,
      moved: false,
      marker: false,
    };
  };
  const onPointerMove = (e: React.PointerEvent<SVGSVGElement>) => {
    const d = dragRef.current;
    if (!d) return;
    const dx = e.clientX - d.startX;
    const dy = e.clientY - d.startY;
    if (Math.abs(dx) + Math.abs(dy) > 4) d.moved = true;
    if (d.marker) {
      setDragPos(screenToLngLat(e.clientX, e.clientY));
      return;
    }
    if (!d.moved) return;
    const el = svgRef.current!;
    const mPerPxX = (2 * halfW) / el.clientWidth;
    const mPerPxY = (2 * halfH) / el.clientHeight;
    setView((v) => ({ ...v, cx: d.cx - dx * mPerPxX, cy: d.cy - dy * mPerPxY }));
  };
  const onPointerUp = (e: React.PointerEvent<SVGSVGElement>) => {
    const d = dragRef.current;
    dragRef.current = null;
    if (!d) return;
    if (d.marker) {
      if (d.moved) {
        const ll = screenToLngLat(e.clientX, e.clientY);
        onCenterChange(ll);
      }
      setDragPos(null);
      return;
    }
    if (!d.moved) {
      setPopup(null);
      onCenterChange(screenToLngLat(e.clientX, e.clientY));
    }
  };

  const onWheel = (e: React.WheelEvent<SVGSVGElement>) => {
    const factor = e.deltaY < 0 ? 1.2 : 1 / 1.2;
    setView((v) => ({ ...v, k: Math.min(12, Math.max(0.4, v.k * factor)) }));
  };

  const onMarkerDown = (e: React.PointerEvent) => {
    e.stopPropagation();
    dragRef.current = {
      startX: e.clientX,
      startY: e.clientY,
      cx: view.cx,
      cy: view.cy,
      moved: false,
      marker: true,
    };
    (e.target as Element).setPointerCapture?.(e.pointerId);
  };

  // ---- derived paths ----
  const paths = useMemo(() => {
    const fc = (features: GeoFeature[] | undefined) =>
      (features ?? []).map((f, i) => ({ key: i, d: featureToPathData(f, anchor), f }));
    return {
      water: fc(fabric?.water.features),
      residential: fc(fabric?.residential?.features),
      buildings: fc(fabric?.buildings.features),
      roads: fc(fabric?.roads.features),
      barriers: fc(fabric?.barriers.features),
      enclaves: fc(result?.enclaves),
    };
  }, [fabric, result, anchor]);

  const sortedLayers = useMemo(
    () => (result ? [...result.layers].sort((a, b) => b.minutes - a.minutes) : []),
    [result]
  );
  const layerCount = result?.layers.length ?? 0;
  const innerIndexOf = (minutesOfLayer: number) =>
    (result?.layers ?? [])
      .slice()
      .sort((a, b) => a.minutes - b.minutes)
      .findIndex((l) => l.minutes === minutesOfLayer);

  const markerM = toMeters(dragPos ?? center, anchor);

  // 小区资讯框屏幕位置（定位点右侧，越界翻左）
  const communityPos = communityInfo
    ? (() => {
        const m = toMeters(communityInfo, anchor);
        const x = ((m.x - (view.cx - halfW)) / (2 * halfW)) * size.w;
        const y = ((-m.y - (view.cy - halfH)) / (2 * halfH)) * size.h;
        const bw = 240;
        return {
          left: x + 24 + bw > size.w ? Math.max(8, x - 24 - bw) : x + 24,
          top: Math.max(8, Math.min(y - 24, size.h - 320)),
        };
      })()
    : null;

  // 报告栏点选的高亮设施（倒四棱锥）
  const highlightM = highlightPoi ? toMeters(highlightPoi, anchor) : null;

  const popupPos = popup
    ? (() => {
        const m = toMeters(popup, anchor);
        return {
          x: ((m.x - (view.cx - halfW)) / (2 * halfW)) * size.w,
          y: ((-m.y - (view.cy - halfH)) / (2 * halfH)) * size.h,
        };
      })()
    : null;

  const fastPath = fastLayer && showFast ? featureToPathData(fastLayer, anchor) : null;

  return (
    <div className="relative h-full w-full overflow-hidden bg-[#f2f1ec]">
      <svg
        ref={svgRef}
        className="h-full w-full cursor-crosshair touch-none select-none"
        viewBox={viewBox}
        preserveAspectRatio="none"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onWheel={onWheel}
      >
        {/* 直线对照圆（默认关） */}
        {visible.straightCircle && result && (
          <circle
            cx={0}
            cy={0}
            r={result.straightCircle.radiusM}
            fill="none"
            stroke="#94a3b8"
            strokeWidth={5}
            strokeDasharray="24 16"
          />
        )}

        {/* 水域 */}
        {visible.water &&
          paths.water.map((p) => (
            <path key={`w${p.key}`} d={p.d} fill="#bfdbfe" stroke="#93c5fd" strokeWidth={3} fillRule="evenodd" />
          ))}

        {/* OSM 建筑空洞占位面：半透明浅灰 + 虚线描边，压在真实建筑下方 */}
        {visible.buildings &&
          paths.residential.map((p) => (
            <path
              key={`rp${p.key}`}
              d={p.d}
              fill="#94a3b8"
              fillOpacity={0.12}
              stroke="#64748b"
              strokeOpacity={0.7}
              strokeWidth={4}
              strokeDasharray="18 12"
              fillRule="evenodd"
            />
          ))}

        {/* 建筑分色 */}
        {visible.buildings &&
          paths.buildings.map((p) => {
            const cat = (p.f.properties?.category as string) ?? "blocked";
            const style = BUILDING_COLORS[cat] ?? BUILDING_COLORS.blocked;
            return (
              <path
                key={`b${p.key}`}
                d={p.d}
                fill={style.fill}
                fillOpacity={style.fillOpacity ?? 0.6}
                stroke={style.stroke ?? "#d6d3d1"}
                strokeWidth={style.stroke ? 4 : 1.5}
                fillRule="evenodd"
              />
            );
          })}

        {/* 路网 */}
        {visible.roads &&
          paths.roads.map((p) => (
            <path
              key={`r${p.key}`}
              d={p.d}
              fill="none"
              stroke={p.f.properties?.bridge ? "#52525b" : "#a8a29e"}
              strokeWidth={p.f.properties?.bridge ? 7 : 4}
              strokeLinecap="round"
            />
          ))}

        {/* 快速路/阻隔 */}
        {visible.barriers &&
          paths.barriers.map((p) => (
            <path
              key={`x${p.key}`}
              d={p.d}
              fill="none"
              stroke="#ef4444"
              strokeWidth={6}
              strokeDasharray="18 12"
              strokeLinecap="round"
            />
          ))}

        {/* 等时圈：fastLayer 快拍（精拍返回后替换） */}
        {visible.isochrone && fastPath && (
          <path
            d={fastPath}
            fill="#0891b2"
            fillOpacity={0.18}
            stroke="#0891b2"
            strokeOpacity={0.6}
            strokeWidth={2}
            strokeDasharray="20 14"
            fillRule="evenodd"
          />
        )}

        {/* 等时圈：精拍分层（内深外浅） */}
        {visible.isochrone &&
          result &&
          sortedLayers.map((l) => {
            const innerIdx = Math.max(0, innerIndexOf(l.minutes));
            const color = isoColor(innerIdx, Math.max(1, layerCount));
            return (
              <path
                key={`iso${l.minutes}`}
                d={featureToPathData(l.polygon, anchor)}
                fill={color}
                fillOpacity={0.38}
                stroke={color}
                strokeOpacity={0.9}
                strokeWidth={1}
                strokeLinejoin="round"
                fillRule="evenodd"
              />
            );
          })}

        {/* 飞地（过桥）描边区分 */}
        {visible.isochrone &&
          result &&
          paths.enclaves.map((p) => (
            <path
              key={`e${p.key}`}
              d={p.d}
              fill="#fbbf24"
              fillOpacity={0.3}
              stroke="#d97706"
              strokeWidth={6}
              strokeDasharray="16 10"
              fillRule="evenodd"
            />
          ))}

        {/* 百度步行对照（橙/紫虚线，默认关） */}
        {visible.baiduRoutes &&
          result?.baiduRoutes?.map((r, i) => {
            const color = ["#ea580c", "#7c3aed"][i % 2];
            const d = r.path
              .map(([lng, lat], j) => {
                const m = toMeters({ lng, lat }, anchor);
                return `${j === 0 ? "M" : "L"}${m.x},${-m.y}`;
              })
              .join(" ");
            return (
              <path
                key={`br${i}`}
                d={d}
                fill="none"
                stroke={color}
                strokeWidth={7}
                strokeDasharray="22 14"
                strokeLinecap="round"
                strokeOpacity={0.95}
              >
                <title>{r.name}</title>
              </path>
            );
          })}

        {/* POI */}
        {visible.pois &&
          result?.pois.map((p) => {
            const m = toMeters(p, anchor);
            const meta = POI_CATEGORY_META[p.category];
            return (
              <circle
                key={p.id}
                cx={m.x}
                cy={-m.y}
                r={22}
                fill={meta?.color ?? "#64748b"}
                fillOpacity={p.within ? 0.95 : 0.55}
                stroke="#ffffff"
                strokeWidth={6}
                style={{ cursor: "pointer" }}
                onPointerDown={(e) => e.stopPropagation()}
                onClick={(e) => {
                  e.stopPropagation();
                  setPopup(p);
                }}
              >
                <title>{p.name}</title>
              </circle>
            );
          })}

        {/* 高亮设施（报告栏点选）：深天蓝 3D 倒圆锥，跳动（1.5Hz），无旋转无边框 */}
        {highlightM && highlightPoi && (
          <g transform={`translate(${highlightM.x} ${-highlightM.y})`} pointerEvents="none">
            <defs>
              <linearGradient id="poi-cone-grad" x1="0" y1="0" x2="1" y2="0">
                <stop offset="0" stopColor="#075985" />
                <stop offset="0.45" stopColor="#0284c7" />
                <stop offset="0.8" stopColor="#0ea5e9" />
                <stop offset="1" stopColor="#0369a1" />
              </linearGradient>
            </defs>
            <ellipse cx={0} cy={2} rx={42} ry={10} fill="rgba(15,23,42,0.18)">
              <animate attributeName="rx" values="42;26;42" dur="0.667s" repeatCount="indefinite" />
            </ellipse>
            <g>
              <animateTransform
                attributeName="transform"
                type="translate"
                values="0 0;0 -9;0 0"
                keyTimes="0;0.5;1"
                dur="0.667s"
                repeatCount="indefinite"
              />
              <ellipse cx={0} cy={-84} rx={60} ry={23} fill="#38bdf8" />
              <path d="M -60 -84 A 60 23 0 0 0 60 -84 L 0 0 Z" fill="url(#poi-cone-grad)" />
            </g>
            <g transform="translate(24 -50)">
              <rect
                x={0}
                y={-15}
                rx={6}
                width={highlightPoi.name.length * 13 + 16}
                height={24}
                fill="#ffffff"
                fillOpacity={0.95}
                stroke="#0284c7"
                strokeWidth={2}
              />
              <text x={8} y={2} fontSize={13} fontWeight={600} fill="#1e293b">
                {highlightPoi.name}
              </text>
            </g>
          </g>
        )}

        {/* 中心标记（可拖拽） */}
        <g
          transform={`translate(${markerM.x} ${-markerM.y})`}
          onPointerDown={onMarkerDown}
          style={{ cursor: "grab" }}
        >
          <circle r={44} fill="transparent" />
          <path d="M0,40 L-22,-6 A26,26 0 1 1 22,-6 Z" fill="#0f766e" stroke="#ffffff" strokeWidth={6} />
          <circle r={10} cy={-14} fill="#ffffff" />
        </g>
      </svg>

      {/* 缩放按钮 */}
      <div className="absolute right-3 top-3 flex flex-col gap-1 no-print">
        <button
          type="button"
          aria-label="放大"
          className="h-8 w-8 rounded border border-slate-300 bg-white text-lg leading-none text-slate-700 shadow-sm hover:bg-slate-50"
          onClick={() => setView((v) => ({ ...v, k: Math.min(12, v.k * 1.4) }))}
        >
          +
        </button>
        <button
          type="button"
          aria-label="缩小"
          className="h-8 w-8 rounded border border-slate-300 bg-white text-lg leading-none text-slate-700 shadow-sm hover:bg-slate-50"
          onClick={() => setView((v) => ({ ...v, k: Math.max(0.4, v.k / 1.4) }))}
        >
          −
        </button>
      </div>

      {/* POI 弹窗 */}
      {popup && popupPos && (
        <div
          className="absolute z-20 w-52 -translate-x-1/2 rounded-lg border border-slate-200 bg-white p-3 shadow-lg"
          style={{
            left: Math.min(Math.max(popupPos.x, 110), size.w - 110),
            top: Math.max(popupPos.y - 150, 8),
          }}
        >
          <div className="flex items-start justify-between gap-2">
            <div className="text-sm font-semibold text-slate-800">{popup.name}</div>
            <button
              type="button"
              className="text-slate-400 hover:text-slate-600"
              onClick={() => setPopup(null)}
              aria-label="关闭"
            >
              ×
            </button>
          </div>
          <div className="mt-1 text-xs text-slate-500">
            {POI_CATEGORY_META[popup.category]?.name ?? popup.category}
          </div>
          <div className="mt-2 flex items-center justify-between text-sm">
            <span className="text-slate-700">步行 {popup.walkMinutes} 分钟</span>
            <span
              className={`rounded-full px-2 py-0.5 text-xs font-medium ${
                popup.within ? "bg-green-50 text-green-700" : "bg-red-50 text-red-700"
              }`}
            >
              {popup.within ? "在圈内" : "在圈外"}
            </span>
          </div>
        </div>
      )}
      {/* 小区资讯框 */}
      {communityInfo && communityPos && (
        <CommunityInfoBox
          info={communityInfo}
          distanceM={communityDistanceM ?? undefined}
          onClose={onCloseCommunityInfo}
          className="absolute z-30"
          style={{ display: "block", left: communityPos.left, top: communityPos.top }}
        />
      )}
    </div>
  );
}
