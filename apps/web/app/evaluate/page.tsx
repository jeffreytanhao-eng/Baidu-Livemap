"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import LayerPanel from "@/components/map/LayerPanel";
import MapView from "@/components/map/MapView";
import MobileBar from "@/components/MobileBar";
import ReportPanel from "@/components/report/ReportPanel";
import TopBar from "@/components/TopBar";
import { Button } from "@/components/ui/button";
import { getFabric, getSamples, locateCommunity, postCheckup, reverseGeocode } from "@/lib/api";
import {
  DEFAULT_CENTER,
  DEFAULT_LOCATION_LABEL,
  DEFAULT_MINUTES,
  MAX_MINUTES,
  MIN_MINUTES,
  WALK_SPEED_M_PER_MIN,
} from "@/lib/constants";
import { distanceMeters } from "@/lib/geo";
import type {
  CheckupResponse,
  CommunityInfo,
  FabricResponse,
  GeoFeature,
  LayerVisibility,
  LngLat,
  Phase,
  SamplePoint,
} from "@/lib/types";

const DEFAULT_VISIBILITY: LayerVisibility = {
  isochrone: true,
  roads: true,
  buildings: true,
  water: true,
  barriers: true,
  pois: true,
  straightCircle: false,
  baiduRoutes: false,
  idw: false,
};

export default function Page() {
  const router = useRouter();
  // ---- 参数（改动不发请求）----
  const [center, setCenter] = useState<LngLat>(DEFAULT_CENTER);
  const [minutes, setMinutes] = useState<number>(DEFAULT_MINUTES);
  const [locationLabel, setLocationLabel] = useState(DEFAULT_LOCATION_LABEL);

  // ---- 已应用（上次成功体验）----
  const [applied, setApplied] = useState<{ center: LngLat; minutes: number } | null>(null);
  const [result, setResult] = useState<CheckupResponse | null>(null);
  const [fastLayer, setFastLayer] = useState<GeoFeature | null>(null);
  const [showFast, setShowFast] = useState(false);
  const [fabric, setFabric] = useState<FabricResponse | null>(null);
  const [fabricCenter, setFabricCenter] = useState<LngLat>(DEFAULT_CENTER);

  const [phase, setPhase] = useState<Phase>("idle");
  const [error, setError] = useState<string | null>(null);
  const [visible, setVisible] = useState<LayerVisibility>(DEFAULT_VISIBILITY);
  const [highlightPoi, setHighlightPoi] = useState<{
    id: string;
    name: string;
    lng: number;
    lat: number;
  } | null>(null);

  // 回退键可用性：仅从「附近小区生活圈」进入（URL 带 from=nearby）时可选
  const [fromNearby, setFromNearby] = useState(false);

  const [drawerOpen, setDrawerOpen] = useState(false);

  // ---- 小区资讯框（点击区域为小区时出现）----
  const [communityInfo, setCommunityInfo] = useState<CommunityInfo | null>(null);
  const [communityDistanceM, setCommunityDistanceM] = useState<number | null>(null);
  const [infoOpen, setInfoOpen] = useState(false);

  const samplesRef = useRef<SamplePoint[]>([]);
  const runningRef = useRef(false);

  const dirty = useMemo(
    () =>
      !!applied &&
      (applied.minutes !== minutes ||
        distanceMeters(applied.center, center) > 30),
    [applied, minutes, center]
  );

  const loadFabric = useCallback(async (c: LngLat, m: number) => {
    try {
      const f = await getFabric(c, m * WALK_SPEED_M_PER_MIN * 1.7);
      setFabric(f);
      setFabricCenter(c);
    } catch {
      // 底图数据失败不阻塞主流程
    }
  }, []);

  // ---- 小区定位：轻量本地查询（SQLite + 面命中），失败静默 ----
  const refreshCommunity = useCallback(async (c: LngLat) => {
    try {
      const r = await locateCommunity(c);
      if (r.matchedBy !== "none" && r.community) {
        setCommunityInfo(r.community);
        setCommunityDistanceM(r.distanceM ?? null);
        setInfoOpen(true);
      } else {
        setCommunityInfo(null);
        setCommunityDistanceM(null);
        setInfoOpen(false);
      }
    } catch {
      // 定位失败不影响主流程
    }
  }, []);

  // ---- 状态机核心：只有这里发 checkup 请求 ----
  const runExperience = useCallback(
    async (c: LngLat, m: number, engine: string = "map-fabric") => {
      if (runningRef.current) return;
      runningRef.current = true;
      setError(null);
      setHighlightPoi(null);
      setPhase("fast");
      try {
        const fast = await postCheckup(c, m, "fast", engine);
        setFastLayer(fast.fastLayer);
        setShowFast(true);
        setPhase("full");
        const full = await postCheckup(c, m, "full", engine);
        setResult(full);
        setShowFast(false);
        setApplied({ center: c, minutes: m });
        setPhase("idle");
        setDrawerOpen(true);
        loadFabric(c, m);
        refreshCommunity(c);
        // 位置名称
        const match = samplesRef.current.find((s) => distanceMeters(s.center, c) < 150);
        if (match) {
          setLocationLabel(`${match.name} · 北京`);
        } else {
          reverseGeocode(c)
            .then((r) => setLocationLabel(r.address))
            .catch(() => setLocationLabel("自定义位置 · 北京"));
        }
      } catch (e) {
        // 失败保留上次成功图层
        setShowFast(false);
        setPhase("idle");
        setError(e instanceof Error ? e.message : "请求失败");
      } finally {
        runningRef.current = false;
      }
    },
    [loadFabric, refreshCommunity]
  );

  // ---- 初始化：URL 回填 + 样例 + 条件自动体验 ----
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const sp = new URLSearchParams(window.location.search);
      const lat = parseFloat(sp.get("lat") ?? "");
      const lng = parseFloat(sp.get("lng") ?? "");
      const mRaw = parseInt(sp.get("minutes") ?? "", 10);
      let c = DEFAULT_CENTER;
      let m = DEFAULT_MINUTES;
      let fromUrl = false;
      if (Number.isFinite(lat) && Number.isFinite(lng) && Math.abs(lat) <= 90 && Math.abs(lng) <= 180) {
        c = { lng, lat };
        fromUrl = true;
      }
      if (Number.isFinite(mRaw) && mRaw >= MIN_MINUTES && mRaw <= MAX_MINUTES) {
        m = mRaw;
      }
      // 来源标记：从附近小区页跳入 → 回退键可选
      if (sp.get("from") === "nearby") setFromNearby(true);
      setCenter(c);
      setMinutes(m);
      if (fromUrl) setLocationLabel("指定位置 · 北京");

      try {
        const samples = await getSamples();
        if (cancelled) return;
        samplesRef.current = samples;
        const match = samples.find((s) => distanceMeters(s.center, c) < 150);
        if (match) setLocationLabel(`${match.name} · 北京`);
        // 样例中心 + 默认 15 分钟 → 自动跑一次；URL 指定坐标（如附近小区详情跳转）也自动跑
        if ((match && m === DEFAULT_MINUTES) || fromUrl) {
          loadFabric(c, m);
          runExperience(c, m);
          return;
        }
      } catch {
        // samples 不可用时静默
      }
      loadFabric(c, m);
    })();
    return () => {
      cancelled = true;
    };
  }, [runExperience, loadFabric]);

  // ---- 交互回调（均不发请求）----
  const handleCenterChange = useCallback(
    (c: LngLat) => {
      setCenter(c);
      const match = samplesRef.current.find((s) => distanceMeters(s.center, c) < 150);
      setLocationLabel(match ? `${match.name} · 北京` : "自定义位置 · 北京");
      refreshCommunity(c);
    },
    [refreshCommunity]
  );

  // 回退：返回「附近小区生活圈」（其页面状态由 sessionStorage 恢复）
  const handleBackToNearby = useCallback(() => {
    router.push("/nearby");
  }, [router]);

  // 报告栏「更多信息」：地图上重新显示资讯框（已显示时按钮禁用）
  const showCommunityInfo = useCallback(() => setInfoOpen(true), []);

  const shareUrl = useMemo(() => {
    if (typeof window === "undefined") return "";
    const base = `${window.location.origin}${window.location.pathname}`;
    const c = applied?.center ?? center;
    const m = applied?.minutes ?? minutes;
    return `${base}?lat=${c.lat.toFixed(6)}&lng=${c.lng.toFixed(6)}&minutes=${m}`;
  }, [applied, center, minutes]);

  const meta = result?.meta;

  return (
    <div id="app-root" className="flex h-dvh flex-col overflow-hidden bg-slate-50">
      <TopBar
        locationLabel={locationLabel}
        minutes={minutes}
        onMinutesChange={setMinutes}
        dirty={dirty}
        phase={phase}
        onStart={() => runExperience(center, minutes)}
        methodOnly={!!meta?.methodOnly}
      />

      {/* 错误条（可恢复，保留上次图层） */}
      {error && (
        <div className="flex items-center gap-3 border-b border-red-200 bg-red-50 px-4 py-2 text-sm text-red-700 no-print">
          <span className="min-w-0 flex-1 truncate" title={error}>
            体验请求失败：{error}（已保留上次成功结果）
          </span>
          <Button
            size="sm"
            variant="danger"
            onClick={() => runExperience(center, minutes, "demo")}
            disabled={phase !== "idle"}
          >
            用演示数据
          </Button>
          <button
            type="button"
            className="text-red-400 hover:text-red-600"
            aria-label="关闭错误提示"
            onClick={() => setError(null)}
          >
            ×
          </button>
        </div>
      )}

      <main id="app-main" className="flex min-h-0 flex-1 flex-col pb-14 md:flex-row md:pb-0">
        {/* 地图 */}
        <section id="map-section" className="relative min-h-0 flex-1">
          {/* 回退键：从「附近小区生活圈」进入时可选，直接进入时不可选 */}
          <div className="absolute left-3 top-3 z-40 no-print">
            <button
              type="button"
              onClick={handleBackToNearby}
              disabled={!fromNearby}
              title={
                fromNearby
                  ? "返回附近小区生活圈（保留列表状态）"
                  : "仅从「附近小区生活圈」进入时可返回"
              }
              className="flex h-9 items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3 text-sm font-medium text-slate-700 shadow-md transition-colors duration-150 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:bg-white"
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
              返回附近小区
            </button>
          </div>
          <MapView
            center={center}
            minutes={minutes}
            anchor={fabricCenter}
            fabric={fabric}
            fastLayer={fastLayer}
            showFast={showFast}
            result={result}
            visible={visible}
            phase={phase}
            onCenterChange={handleCenterChange}
            highlightPoi={highlightPoi}
            communityInfo={infoOpen ? communityInfo : null}
            communityDistanceM={communityDistanceM}
            onCloseCommunityInfo={() => setInfoOpen(false)}
          />
          <LayerPanel
            visible={visible}
            onChange={setVisible}
            result={result}
          />
        </section>

        {/* 报告：桌面右侧栏 / 移动底部抽屉 */}
        <aside
          id="report-panel"
          className={`fixed inset-x-0 bottom-14 z-40 flex max-h-[64vh] flex-col rounded-t-2xl border-t border-slate-200 bg-white shadow-[0_-4px_16px_rgba(0,0,0,0.08)] transition-transform duration-200 md:static md:z-auto md:h-full md:max-h-none md:w-[420px] md:shrink-0 md:translate-y-0 md:rounded-none md:border-l md:border-t-0 md:shadow-none lg:w-[440px] ${
            drawerOpen ? "translate-y-0" : "translate-y-[calc(100%-3.25rem)]"
          }`}
        >
          {/* 抽屉把手（移动端） */}
          <button
            type="button"
            className="flex h-[3.25rem] w-full shrink-0 flex-col items-center justify-center gap-0.5 md:hidden no-print"
            onClick={() => setDrawerOpen((v) => !v)}
            aria-expanded={drawerOpen}
          >
            <span className="h-1 w-10 rounded-full bg-slate-300" />
            <span className="text-sm font-semibold text-slate-700">
              体检报告
              {result ? ` · ${(result.coverage.score ?? 0).toFixed(1)} 分` : ""}
              {drawerOpen ? " ▾" : " ▴"}
            </span>
          </button>
          <div id="report-scroll" className="min-h-0 flex-1 overflow-y-auto">
            <ReportPanel
              result={result}
              appliedMinutes={applied?.minutes ?? null}
              paramMinutes={minutes}
              dirty={dirty}
              phase={phase}
              shareUrl={shareUrl}
              onPoiClick={setHighlightPoi}
              communityInfo={communityInfo}
              infoOpen={infoOpen && !!communityInfo}
              onMoreInfo={showCommunityInfo}
            />
          </div>
        </aside>
      </main>

      <MobileBar
        minutes={minutes}
        onMinutesChange={setMinutes}
        dirty={dirty}
        phase={phase}
        onStart={() => runExperience(center, minutes)}
      />

      {/* 演示数据角标 */}
      {meta?.demoMode && (
        <div className="fixed right-3 top-16 z-50 rotate-3 rounded border-2 border-blue-500 bg-white px-2.5 py-1 text-sm font-bold text-blue-600 shadow-md no-print">
          演示数据
        </div>
      )}
    </div>
  );
}
