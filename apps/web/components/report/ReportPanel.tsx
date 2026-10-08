"use client";

import { useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import type { EChartsOption } from "echarts";
import { useECharts } from "@/hooks/useECharts";
import { API_BASE } from "@/lib/api";
import {
  POI_CATEGORY_META,
  SECTOR_DIRECTIONS,
  SECTOR_LABEL,
  WALK_SPEED_M_PER_MIN,
} from "@/lib/constants";
import type {
  CheckupResponse,
  CommunityInfo,
  Phase,
  Poi,
  PoiCategory,
  SectorDirection,
} from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";

const SECTOR_RE = /^sector:([A-Z]{1,2})\s*/;

/** POI 打标 tag -> 展示名（重点学校/三甲医院/地铁站） */
const HOT_TAG_LABEL: Record<string, string> = {
  key_school: "重点学校",
  sanjia: "三甲医院",
  metro: "地铁站",
};

function parseDiagnosis(text: string): { sector: SectorDirection | null; body: string } {
  const m = text.match(SECTOR_RE);
  if (m && (SECTOR_DIRECTIONS as string[]).includes(m[1])) {
    return { sector: m[1] as SectorDirection, body: text.slice(m[0].length) };
  }
  return { sector: null, body: text };
}

/** 类别分项得分（0-100，与雷达「类别近度分」同口径）：
 * r = 最近步行 / 目标分钟。r ≤ 1/3 满分 100；恰好 r=1（达标线）得 60；
 * 之后每超出 10% 再扣 6 分，下限 15。无设施返回 null。 */
function catProximityScore(
  nearestMinutes: number | null | undefined,
  targetMinutes: number,
): number | null {
  if (nearestMinutes == null) return null;
  const r = nearestMinutes / Math.max(1, targetMinutes);
  let v: number;
  if (r <= 1 / 3) v = 100;
  else if (r <= 1) v = 100 - 60 * (r - 1 / 3);
  else v = 60 - 60 * (r - 1);
  return Math.max(15, Math.round(v));
}

export default function ReportPanel({
  result,
  appliedMinutes,
  paramMinutes,
  dirty,
  phase,
  shareUrl,
  onPoiClick,
  communityInfo,
  infoOpen,
  onMoreInfo,
}: {
  result: CheckupResponse | null;
  appliedMinutes: number | null;
  paramMinutes: number;
  dirty: boolean;
  phase: Phase;
  shareUrl: string;
  onPoiClick?: (poi: Poi) => void;
  communityInfo?: CommunityInfo | null;
  infoOpen?: boolean;
  onMoreInfo?: () => void;
}) {
  const [copied, setCopied] = useState(false);
  const [hoverCat, setHoverCat] = useState<{ id: PoiCategory; x: number; y: number } | null>(
    null,
  );
  const openCat = (id: PoiCategory, x: number, y: number) => {
    setHoverCat({ id, x, y });
  };

  // 悬停期间的全局监听：指针移到「类别行 + 浮层」之外立即隐藏（纯悬停，点击不再固定）
  const hoverOpen = hoverCat != null;
  useEffect(() => {
    if (!hoverOpen) return;
    const inScope = (t: EventTarget | null) => {
      const el = t as Element | null;
      return !!(
        el &&
        typeof el.closest === "function" &&
        (el.closest("[data-cat-row]") || el.closest("[data-poi-hover]"))
      );
    };
    const onMove = (e: MouseEvent) => {
      if (inScope(e.target)) return;
      setHoverCat(null);
    };
    window.addEventListener("mousemove", onMove);
    return () => {
      window.removeEventListener("mousemove", onMove);
    };
  }, [hoverOpen]);

  // 新结果到达时关闭浮层
  useEffect(() => {
    setHoverCat(null);
  }, [result]);

  const displayMinutes = appliedMinutes ?? paramMinutes;

  const radarOption = useMemo<EChartsOption | null>(() => {
    if (!result) return null;
    const cats = result.coverage.categories;
    // 无设施类用 5 占位（分项得分下限 15，不会与真实分值冲突），悬浮提示显示「暂无」
    const values = cats.map((c) => catProximityScore(c.nearestMinutes, displayMinutes) ?? 5);
    return {
      tooltip: {
        formatter: (p: any) => {
          const vals = (Array.isArray(p?.value) ? p.value : []) as number[];
          const rows = cats
            .map((c, i) => `${c.name}：${vals[i] === 5 ? "暂无" : `${vals[i]}/100`}`)
            .join("<br/>");
          return `<b>${p?.name ?? "类别近度分"}</b><br/>${rows}`;
        },
      },
      radar: {
        indicator: cats.map((c) => ({ name: c.name, max: 100 })),
        radius: "64%",
        splitNumber: 4,
        axisName: { color: "#475569", fontSize: 11 },
        splitLine: { lineStyle: { color: "#e2e8f0" } },
        splitArea: { areaStyle: { color: ["#ffffff", "#f8fafc"] } },
      },
      series: [
        {
          type: "radar",
          data: [
            {
              value: values,
              name: "类别近度分",
              areaStyle: { color: "#0f766e", opacity: 0.22 },
              lineStyle: { color: "#0f766e", width: 2 },
              itemStyle: { color: "#0f766e" },
            },
          ],
        },
      ],
    };
  }, [result, displayMinutes]);

  const barOption = useMemo<EChartsOption | null>(() => {
    if (!result) return null;
    const cats = result.coverage.categories;
    const maxV = Math.max(
      displayMinutes + 4,
      ...cats.map((c) => (c.nearestMinutes ?? displayMinutes + 3) + 2)
    );
    return {
      grid: { left: 8, right: 44, top: 24, bottom: 8, containLabel: true },
      tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
      xAxis: {
        type: "value",
        max: Math.ceil(maxV),
        name: "分钟",
        nameTextStyle: { fontSize: 10, color: "#94a3b8" },
        splitLine: { lineStyle: { color: "#f1f5f9" } },
        axisLabel: { fontSize: 10, color: "#94a3b8" },
      },
      yAxis: {
        type: "category",
        inverse: true,
        data: cats.map((c) => c.name),
        axisLabel: { fontSize: 11, color: "#334155" },
        axisLine: { lineStyle: { color: "#cbd5e1" } },
        axisTick: { show: false },
      },
      series: [
        {
          type: "bar",
          barWidth: 12,
          data: cats.map((c) => ({
            value: c.nearestMinutes,
            itemStyle: {
              color: c.passed ? "#0f766e" : "#f59e0b",
              borderRadius: [0, 3, 3, 0],
            },
          })),
          label: {
            show: true,
            position: "right",
            fontSize: 10,
            color: "#64748b",
            formatter: (p: any) =>
              p.value == null ? "暂无" : `${p.value} 分钟`,
          },
          markLine: {
            symbol: "none",
            data: [{ xAxis: displayMinutes }],
            lineStyle: { color: "#ef4444", type: "dashed", width: 1.5 },
            label: {
              formatter: `${displayMinutes} 分钟线`,
              fontSize: 10,
              color: "#ef4444",
              position: "insideEndTop",
            },
          },
        },
      ],
    };
  }, [result, displayMinutes]);

  const radarRef = useECharts(radarOption);
  const barRef = useECharts(barOption);

  const downloadJson = () => {
    if (!result) return;
    const blob = new Blob([JSON.stringify(result, null, 2)], {
      type: "application/json",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `life-circle-checkup-${displayMinutes}min.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const copyShare = async () => {
    try {
      await navigator.clipboard.writeText(shareUrl);
    } catch {
      const ta = document.createElement("textarea");
      ta.value = shareUrl;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      ta.remove();
    }
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  // ---- 空态 ----
  if (!result) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 p-8 text-center">
        <div className="text-4xl font-bold text-slate-300">--</div>
        <div className="text-base font-semibold text-slate-600">
          {displayMinutes} 分钟生活圈体验
        </div>
        <p className="text-sm text-slate-500">
          看清真实步行能到哪，找出便利生活、医疗、教育的缺口
        </p>
        <p className="mt-4 rounded-md bg-slate-100 px-4 py-2 text-sm text-slate-500">
          {phase !== "idle"
            ? "正在生成体检报告…"
            : "点击「开始体验」生成体检报告"}
        </p>
      </div>
    );
  }

  const { meta, coverage, diagnosis } = result;
  const score = Number.isFinite(coverage.score) ? coverage.score : 0;

  return (
    <div className="relative flex flex-col gap-3 p-3 print:p-0">
      {/* 参数已改水印 */}
      {dirty && appliedMinutes != null && (
        <>
          <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-1.5 text-xs font-medium text-amber-800 no-print">
            以下为上次 {appliedMinutes} 分钟结果
          </div>
          <div className="pointer-events-none absolute inset-0 z-10 flex items-center justify-center overflow-hidden">
            <div className="rotate-[-18deg] select-none whitespace-nowrap text-3xl font-bold text-slate-900/[0.07]">
              以下为上次 {appliedMinutes} 分钟结果 · 以下为上次 {appliedMinutes} 分钟结果
            </div>
          </div>
        </>
      )}

      {/* 综合分：左小区照片+简介+更多信息，右得分（缩小靠右） */}
      <Card>
        <CardBody>
          <div className="flex items-center gap-3">
            <div className="min-w-0 flex-1">
              <div className="flex items-start gap-2.5">
                <ScorePhoto info={communityInfo ?? null} />
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm font-semibold text-slate-800">
                    {communityInfo?.name ?? "暂无信息"}
                  </div>
                  <p className="mt-0.5 line-clamp-2 text-xs leading-4 text-slate-500">
                    {communityInfo?.intro ||
                      (communityInfo
                        ? [communityInfo.district, communityInfo.bizcircle]
                            .filter(Boolean)
                            .join(" · ") || "暂无简介"
                        : "定位到小区后此处展示照片与简介")}
                  </p>
                  <Button
                    variant="outline"
                    size="sm"
                    className="mt-1.5 h-6 px-2 text-xs"
                    disabled={!communityInfo || !!infoOpen}
                    onClick={onMoreInfo}
                  >
                    更多信息
                  </Button>
                </div>
              </div>
            </div>
            <div className="shrink-0 text-right">
              <div className="text-xs text-slate-400">生活圈评分</div>
              <div className="mt-0.5 flex items-baseline justify-end gap-1">
                <span className="text-2xl font-bold leading-none text-brand">
                  {score.toFixed(1)}
                </span>
                <span className="text-xs text-slate-400">/ 100</span>
              </div>
            </div>
          </div>
          {(meta.demoMode || meta.degraded) && (
            <div className="mt-2 flex flex-wrap justify-center gap-1.5">
              {meta.demoMode && <Badge tone="blue">演示数据</Badge>}
              {meta.degraded && <Badge tone="yellow">已降级</Badge>}
            </div>
          )}
        </CardBody>
      </Card>

      {/* 雷达 + 柱状 */}
      <Card>
        <CardHeader title="七类设施达标雷达" />
        <CardBody>
          <div ref={radarRef} className="h-56 w-full" />
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="最近设施步行分钟" />
        <CardBody>
          <div ref={barRef} className="h-64 w-full" />
        </CardBody>
      </Card>

      {/* 最近分钟表 */}
      <Card>
        <CardHeader title="达标明细" />
        <CardBody className="p-0">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-slate-100 text-[11px] text-slate-400">
                <th className="whitespace-nowrap px-2 py-1.5 pl-3 text-left font-medium">类别</th>
                <th className="whitespace-nowrap px-2 py-1.5 text-right font-medium">数量</th>
                <th className="whitespace-nowrap px-2 py-1.5 text-right font-medium">最近步行</th>
                <th className="whitespace-nowrap px-2 py-1.5 text-right font-medium">分项得分</th>
                <th className="whitespace-nowrap px-2 py-1.5 pr-3 text-right font-medium">达标</th>
              </tr>
            </thead>
            <tbody>
              {coverage.categories.map((c) => {
                const hotTags = c.hot
                  ? [
                      c.hot.keySchool?.length ? "重点学校" : null,
                      c.hot.sanjia?.length ? "三甲医院" : null,
                      c.hot.metro?.length ? "地铁站" : null,
                    ].filter((x): x is string => !!x)
                  : [];
                return (
                  <tr
                    key={c.id}
                    data-cat-row
                    className="cursor-default border-b border-slate-50 last:border-0"
                    onMouseEnter={(e) => openCat(c.id, e.clientX, e.clientY)}
                    onMouseMove={(e) =>
                      setHoverCat((p) =>
                        p && p.id === c.id ? { ...p, x: e.clientX, y: e.clientY } : p,
                      )
                    }
                  >
                    <td className="whitespace-nowrap py-1 pl-3 pr-2 text-slate-700">
                      <span
                        className="mr-1.5 inline-block h-2 w-2 rounded-full"
                        style={{ backgroundColor: POI_CATEGORY_META[c.id]?.color }}
                      />
                      {c.name}
                      {hotTags.length > 0 && (
                        <div className="pl-3.5 text-[10px] leading-3 text-amber-600">
                          含{hotTags.join("、")}
                        </div>
                      )}
                    </td>
                    <td className="whitespace-nowrap px-2 py-1 text-right tabular-nums text-slate-600">
                      {withinCountText(c.withinCount, c.count)}
                    </td>
                    <td className="whitespace-nowrap px-2 py-1 text-right tabular-nums text-slate-600">
                      {c.nearestMinutes == null ? "暂无" : `${c.nearestMinutes} 分钟`}
                    </td>
                    <td className="whitespace-nowrap px-2 py-1 text-right tabular-nums text-slate-600">
                      {(() => {
                        const s = catProximityScore(c.nearestMinutes, displayMinutes);
                        return s == null ? "暂无" : `${s}/100`;
                      })()}
                    </td>
                    <td className="whitespace-nowrap py-1 pl-2 pr-3 text-right">
                      {c.passed ? (
                        <span className="text-base font-bold leading-none text-green-600" title="达标">
                          ✓
                        </span>
                      ) : (
                        <span className="text-base font-bold leading-none text-red-600" title="缺口">
                          ✗
                        </span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </CardBody>
      </Card>

      {/* 诊断 */}
      <Card>
        <CardHeader title={`体检诊断（${diagnosis.length} 条）`} />
        <CardBody>
          <ul className="space-y-2">
            {diagnosis.map((d, i) => {
              const { sector, body } = parseDiagnosis(d);
              return (
                <li
                  key={i}
                  className="rounded-md border border-slate-100 bg-slate-50 px-3 py-2 text-sm leading-6 text-slate-700"
                >
                  {sector && (
                    <span className="mr-1.5 inline-flex items-center rounded-full bg-teal-600 px-2 py-0.5 text-xs font-medium text-white">
                      {SECTOR_LABEL[sector]}侧
                    </span>
                  )}
                  {body}
                </li>
              );
            })}
          </ul>
        </CardBody>
      </Card>

      {/* 操作 */}
      <div className="flex gap-2 pb-2">
        <Button variant="outline" size="sm" className="flex-1" onClick={() => window.print()}>
          打印
        </Button>
        <Button variant="outline" size="sm" className="flex-1" onClick={downloadJson}>
          下载 JSON
        </Button>
        <Button variant="outline" size="sm" className="flex-1" onClick={copyShare}>
          {copied ? "已复制 ✓" : "复制分享链接"}
        </Button>
      </div>

      {/* 悬停浮层：portal 到 body，避免被滚动容器/抽屉裁剪；指针进入浮层保持显示 */}
      {result &&
        hoverCat &&
        createPortal(
          <PoiHoverCard
            catId={hoverCat.id}
            x={hoverCat.x}
            y={hoverCat.y}
            result={result}
            onPick={onPoiClick}
          />,
          document.body
        )}
    </div>
  );
}

/** 圈内数量显示：后端 withinCount 为截断前真实数，超 20 显示「20+」 */
function withinCountText(n: number | undefined, fallback: number): string {
  const v = n ?? fallback;
  return v > 20 ? "20+" : String(v);
}

/** 达标明细行悬停浮层：设施名（点击地图高亮）+ 步行距离 + 步行分钟。
 *  与「数量」「最近步行」列同口径：数量取行数据，estimated 排末尾保证首条 = 最近步行值。 */
function PoiHoverCard({
  catId,
  x,
  y,
  result,
  onPick,
}: {
  catId: PoiCategory;
  x: number;
  y: number;
  result: CheckupResponse;
  onPick?: (poi: Poi) => void;
}) {
  const cat = result.coverage.categories.find((c) => c.id === catId);
  // 所有类别只显示圈内（与「数量」列同口径）；排序已按步行分钟
  const pois = result.pois
    .filter((p) => p.category === catId && p.within)
    .sort((a, b) => {
      const ea = a.estimated ? 1 : 0;
      const eb = b.estimated ? 1 : 0;
      return ea - eb || a.walkMinutes - b.walkMinutes;
    });
  if (!cat) return null;
  const left = Math.max(8, Math.min(x + 16, window.innerWidth - 296));
  const top = Math.max(8, Math.min(y + 14, window.innerHeight - 288));
  const distText = (p: Poi) => {
    const m = Math.round(p.walkMinutes * WALK_SPEED_M_PER_MIN);
    return m >= 1000 ? `${(m / 1000).toFixed(2)} km` : `${m} m`;
  };
  return (
    <div
      data-poi-hover
      className="fixed z-[70] w-72 rounded-md border border-slate-200 bg-white shadow-lg"
      style={{ left, top }}
    >
      <div className="flex items-center justify-between border-b border-slate-100 px-3 py-1.5 text-xs font-semibold text-slate-700">
        <span>
          {cat.name} · {withinCountText(cat.withinCount, cat.count)} 处
        </span>
      </div>
      {/* 列说明表头：设施名 / 距离 / 步行时间（全部左对齐；只显示圈内，无范围列） */}
      <div className="grid grid-cols-[minmax(0,1fr)_54px_64px] gap-1.5 border-b border-slate-100 px-3 pb-1 pt-1.5 text-[10px] font-medium text-slate-400">
        <span>设施名</span>
        <span>距离</span>
        <span>步行时间</span>
      </div>
      <ul className="max-h-56 overflow-y-auto py-1">
        {pois.length === 0 && (
          <li className="px-3 py-1.5 text-xs text-slate-400">未检索到该类设施</li>
        )}
        {pois.map((p) => {
          const hotName = (p.tags ?? [])
            .map((t) => HOT_TAG_LABEL[t])
            .filter(Boolean)
            .join("·");
          return (
            <li
              key={p.id}
              className="grid grid-cols-[minmax(0,1fr)_54px_64px] items-center gap-1.5 px-3 py-1.5 text-xs text-slate-600"
            >
              <button
                type="button"
                className={`min-w-0 truncate text-left hover:underline ${
                  hotName
                    ? "font-semibold text-amber-700"
                    : "text-slate-700 hover:text-brand"
                }`}
                title={`${p.name}${hotName ? `（${hotName}）` : ""}（点击在地图上定位）`}
                onClick={() => onPick?.(p)}
              >
                {p.transportKind ? `${p.name}${p.transportKind === "metro" ? "（地铁）" : "（公交）"}` : p.name}
              </button>
              <span className="text-left tabular-nums text-slate-500">{distText(p)}</span>
              <span className="text-left tabular-nums text-slate-700">
                {p.estimated ? "约 " : ""}
                {p.walkMinutes} 分钟
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/** 综合分卡左侧小区照片：加载失败退化为灰色占位块 */
function ScorePhoto({ info }: { info: CommunityInfo | null }) {
  const [broken, setBroken] = useState(false);
  useEffect(() => setBroken(false), [info?.photoUrl]);
  if (!info?.photoUrl || broken) {
    return (
      <div className="flex h-16 w-16 shrink-0 items-center justify-center rounded-md bg-slate-100 text-[10px] text-slate-400">
        暂无照片
      </div>
    );
  }
  return (
    <img
      src={info.photoUrl.startsWith("http") ? info.photoUrl : `${API_BASE}${info.photoUrl}`}
      alt={info.name}
      className="h-16 w-16 shrink-0 rounded-md object-cover"
      onError={() => setBroken(true)}
    />
  );
}
