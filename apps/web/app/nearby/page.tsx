"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import MapView from "@/components/map/MapView";
import { getNearbyCommunities } from "@/lib/api";
import { DEFAULT_CENTER, DEFAULT_MINUTES } from "@/lib/constants";
import type { LayerVisibility, LngLat, NearbyCommunity } from "@/lib/types";

const RADIUS_M = 1000;
const STORAGE_KEY = "nearby-state-v1";

const DEFAULT_VISIBILITY: LayerVisibility = {
  isochrone: false,
  roads: true,
  buildings: true,
  water: true,
  barriers: true,
  // 关闭设施点层：地图空白处点击即选点（POI 命中会拦截选点回调）
  pois: false,
  straightCircle: false,
  baiduRoutes: false,
  idw: false,
};

interface SavedState {
  center: LngLat;
  communities: NearbyCommunity[];
  scrollTop: number;
}

export default function Page() {
  const [center, setCenter] = useState<LngLat>(DEFAULT_CENTER);
  const [communities, setCommunities] = useState<NearbyCommunity[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // 列表「在地图定位」：蓝色 3D 倒圆锥高亮（同评估页报告栏点选效果）
  const [highlight, setHighlight] = useState<{
    id: number;
    name: string;
    lng: number;
    lat: number;
  } | null>(null);

  const hydratedRef = useRef(false);
  const listRef = useRef<HTMLDivElement | null>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const scrollTopRef = useRef(0);
  const radiusRef = useRef(RADIUS_M);

  const persist = useCallback(() => {
    if (!hydratedRef.current) return;
    try {
      const saved: SavedState = {
        center,
        communities,
        scrollTop: scrollTopRef.current,
      };
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify(saved));
    } catch {
      // 存储不可用时静默（隐私模式等）
    }
  }, [center, communities]);

  const fetchNearby = useCallback(async (c: LngLat) => {
    setLoading(true);
    setError(null);
    try {
      const resp = await getNearbyCommunities(c.lng, c.lat, radiusRef.current);
      setCommunities(resp.communities);
    } catch (e) {
      setError(e instanceof Error ? e.message : "查询失败");
    } finally {
      setLoading(false);
    }
  }, []);

  // 初始化：优先恢复 sessionStorage 存档（从评估页回退时保留全部状态）；
  // 无存档时对默认中心查询一次
  useEffect(() => {
    let saved: SavedState | null = null;
    try {
      const raw = sessionStorage.getItem(STORAGE_KEY);
      if (raw) {
        const parsed = JSON.parse(raw) as SavedState;
        if (
          parsed?.center &&
          Number.isFinite(parsed.center.lng) &&
          Number.isFinite(parsed.center.lat) &&
          Array.isArray(parsed.communities)
        ) {
          saved = parsed;
        }
      }
    } catch {
      // 存档损坏时按无存档处理
    }
    hydratedRef.current = true;
    if (saved) {
      setCenter(saved.center);
      setCommunities(saved.communities);
      requestAnimationFrame(() => {
        if (listRef.current) listRef.current.scrollTop = saved!.scrollTop;
      });
    } else {
      fetchNearby(DEFAULT_CENTER);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 状态变化即落盘（center 列表），保证跳走再回来可完整恢复
  useEffect(() => {
    persist();
  }, [persist]);

  // 地图选点：400ms 防抖查询附近小区（重新选点后原定位标记作废）
  const handleCenterChange = useCallback(
    (c: LngLat) => {
      setCenter(c);
      setHighlight(null);
      if (debounceRef.current) clearTimeout(debounceRef.current);
      debounceRef.current = setTimeout(() => fetchNearby(c), 400);
    },
    [fetchNearby]
  );

  return (
    <div id="app-root" className="flex h-dvh flex-col overflow-hidden bg-slate-50">
      <header className="relative z-40 border-b border-slate-200 bg-white no-print">
        <div className="flex h-14 items-center gap-3 px-4">
          <Link
            href="/"
            className="flex h-9 items-center gap-1.5 rounded-lg border border-slate-300 px-3 text-sm font-medium text-slate-700 transition-colors hover:bg-slate-50"
            title="返回导航页"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden>
              <path
                d="M15 6l-6 6 6 6"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
            导航
          </Link>
          <span className="text-lg font-bold text-brand">附近小区生活圈</span>
          <span className="hidden text-xs font-medium tracking-wide text-slate-400 sm:inline">
            点击地图任意位置，查看周边 1 公里内小区
          </span>
          <div className="flex-1" />
          <span className="hidden text-sm text-slate-500 md:inline">
            {center.lat.toFixed(5)}, {center.lng.toFixed(5)}
          </span>
        </div>
      </header>

      <main className="flex min-h-0 flex-1 flex-col md:flex-row">
        {/* 地图：点击选点 */}
        <section className="relative h-[45vh] shrink-0 md:h-auto md:min-h-0 md:flex-1">
          <MapView
            center={center}
            minutes={DEFAULT_MINUTES}
            anchor={center}
            fabric={null}
            fastLayer={null}
            showFast={false}
            result={null}
            visible={DEFAULT_VISIBILITY}
            phase="idle"
            onCenterChange={handleCenterChange}
            highlightPoi={highlight}
          />
        </section>

        {/* 列表：附近小区 */}
        <aside className="flex min-h-0 flex-1 flex-col border-t border-slate-200 bg-white md:h-full md:w-[420px] md:flex-none md:border-l md:border-t-0">
          <div className="flex items-center justify-between border-b border-slate-100 px-4 py-3">
            <span className="text-sm font-semibold text-slate-900">
              附近小区（{RADIUS_M / 1000} 公里内）
            </span>
            <span className="text-xs text-slate-400">
              {loading ? "查询中…" : `共 ${communities.length} 个`}
            </span>
          </div>

          {error && (
            <div className="flex items-center gap-2 border-b border-red-200 bg-red-50 px-4 py-2 text-sm text-red-700">
              <span className="min-w-0 flex-1 truncate">查询失败：{error}</span>
              <button
                type="button"
                className="shrink-0 font-medium underline underline-offset-2"
                onClick={() => fetchNearby(center)}
              >
                重试
              </button>
            </div>
          )}

          <div
            ref={listRef}
            onScroll={(e) => {
              scrollTopRef.current = e.currentTarget.scrollTop;
            }}
            className="min-h-0 flex-1 overflow-y-auto"
          >
            {!loading && !error && communities.length === 0 && (
              <div className="px-4 py-10 text-center text-sm text-slate-400">
                该范围内暂无小区数据，试试在地图上换个位置
              </div>
            )}
            <ul className="divide-y divide-slate-100">
              {communities.map((c) => (
                <li key={c.id} className="flex items-center gap-3 px-4 py-3">
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm font-medium text-slate-900" title={c.name}>
                      {c.name}
                    </div>
                    <div className="mt-0.5 text-xs text-slate-500">
                      距离 {Math.round(c.distanceM)} 米
                      <button
                        type="button"
                        onClick={() =>
                          setHighlight((h) =>
                            h?.id === c.id
                              ? null
                              : { id: c.id, name: c.name, lng: c.lng, lat: c.lat }
                          )
                        }
                        className="ml-2 font-medium text-brand underline-offset-2 transition-colors hover:underline"
                      >
                        {highlight?.id === c.id ? "取消定位" : "在地图定位"}
                      </button>
                    </div>
                  </div>
                  <Link
                    href={`/evaluate?lat=${c.lat}&lng=${c.lng}&from=nearby`}
                    className="shrink-0 rounded-lg border border-brand px-3 py-1.5 text-sm font-medium text-brand transition-colors hover:bg-brand hover:text-white"
                  >
                    生活圈详情
                  </Link>
                </li>
              ))}
            </ul>
          </div>
        </aside>
      </main>
    </div>
  );
}
