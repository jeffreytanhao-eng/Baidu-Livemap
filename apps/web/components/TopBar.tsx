"use client";

import { MINUTE_PRESETS } from "@/lib/constants";
import type { Phase } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

export default function TopBar({
  locationLabel,
  minutes,
  onMinutesChange,
  dirty,
  phase,
  onStart,
  methodOnly,
}: {
  locationLabel: string;
  minutes: number;
  onMinutesChange: (m: number) => void;
  dirty: boolean;
  phase: Phase;
  onStart: () => void;
  methodOnly: boolean;
}) {
  const running = phase !== "idle";

  return (
    <header className="relative z-40 border-b border-slate-200 bg-white no-print">
      <div className="flex h-14 items-center gap-3 px-4">
        {/* 品牌 */}
        <div className="flex items-baseline gap-2">
            <span className="text-lg font-bold text-brand">百度地图15分钟生活圈</span>
            <span className="hidden text-xs font-medium tracking-wide text-slate-400 sm:inline">
              Livemap（版本支持北京六环内区域高精度结果）
            </span>
          </div>

        {/* 当前位置 */}
        <div className="hidden items-center gap-1.5 text-sm text-slate-600 md:flex">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden>
            <path
              d="M12 21s-7-5.1-7-11a7 7 0 1 1 14 0c0 5.9-7 11-7 11z"
              stroke="#0f766e"
              strokeWidth="2"
            />
            <circle cx="12" cy="10" r="2.6" fill="#0f766e" />
          </svg>
          <span className="max-w-56 truncate font-medium">{locationLabel}</span>
        </div>

        <div className="flex-1" />

        {/* 分钟分段控件（移动端隐藏，由底部栏承担） */}
        <div className="hidden items-center gap-2 md:flex">
          <div className="flex overflow-hidden rounded-lg border border-slate-300">
            {MINUTE_PRESETS.map((m) => (
              <button
                key={m}
                type="button"
                onClick={() => onMinutesChange(m)}
                className={`h-9 w-12 text-sm font-medium transition-colors duration-150 ${
                  minutes === m
                    ? "bg-brand text-white"
                    : "bg-white text-slate-600 hover:bg-slate-50"
                } ${m === 15 ? "border-x border-slate-300" : ""}`}
                title={`${m} 分钟${m === 15 ? "（默认）" : ""}`}
              >
                {m}
                {m === 15 && <span className="ml-0.5 text-[10px] opacity-70">*</span>}
              </button>
            ))}
          </div>
        </div>

        {methodOnly && (
          <Badge tone="yellow" className="hidden lg:inline-flex">
            未做设施词表校准，仅验证方法
          </Badge>
        )}

        {/* 状态 + 主按钮 */}
        <div className="hidden items-center gap-2 md:flex">
          {dirty && (
            <span className="flex items-center gap-1.5 text-xs font-medium text-amber-600">
              <span className="inline-block h-2 w-2 rounded-full bg-amber-400" />
              参数已改，尚未体验
            </span>
          )}
          <Button size="lg" onClick={onStart} disabled={running}>
            {running ? "体验中…" : "开始体验"}
          </Button>
        </div>

        {/* 移动端仅保留主按钮占位提示 */}
        <div className="flex items-center md:hidden">
          {dirty && (
            <span className="mr-2 flex items-center gap-1 text-xs font-medium text-amber-600">
              <span className="inline-block h-2 w-2 rounded-full bg-amber-400" />
              参数已改
            </span>
          )}
        </div>
      </div>
    </header>
  );
}
