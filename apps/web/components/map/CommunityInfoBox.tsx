"use client";

import { forwardRef, type CSSProperties } from "react";
import type { CommunityInfo } from "@/lib/types";
import CommunityPlaceholder from "@/components/CommunityPlaceholder";

const RENT_LABELS: [keyof CommunityInfo["rent"], string][] = [
  ["l1", "一居"],
  ["l2", "两居"],
  ["l3", "三居"],
];

/** 小区资讯框：定位点右侧浮层。
 *  图片位置统一为占位效果图（版权合规，不展示实拍照片）、售卖均价、租金范围
 *  （一居/两居/三居，缺失显示「信息不足」）、简介与来源；右上角可关闭。
 *  定位由父级控制（百度模式在重绘循环里设置 left/top，SVG 模式每帧计算）。 */
const CommunityInfoBox = forwardRef<
  HTMLDivElement,
  {
    info: CommunityInfo;
    /** 坐标兜底命中的距离提示 */
    distanceM?: number;
    onClose?: () => void;
    className?: string;
    style?: CSSProperties;
  }
>(function CommunityInfoBox({ info, distanceM, onClose, className, style }, ref) {
  const rentText = (r: CommunityInfo["rent"][keyof CommunityInfo["rent"]]) =>
    r ? `${Math.round(r.min)}-${Math.round(r.max)} 元/月` : "信息不足";

  return (
    <div
      ref={ref}
      data-community-box
      className={`w-60 rounded-lg border border-slate-200 bg-white shadow-lg ${className ?? ""}`}
      style={{ display: "none", ...style }}
    >
      {/* 头部：照片 + 名称 + 关闭 */}
      <div className="relative border-b border-slate-100 p-2.5">
        <button
          type="button"
          aria-label="关闭资讯框"
          className="absolute right-2 top-2 z-10 flex h-5 w-5 items-center justify-center rounded-full bg-slate-900/50 text-xs leading-none text-white hover:bg-slate-900/70"
          onClick={onClose}
        >
          ✕
        </button>
        <CommunityPlaceholder className="h-28 w-full rounded-md" />
        <div className="mt-2 flex items-baseline justify-between gap-2">
          <span className="min-w-0 truncate text-sm font-semibold text-slate-800">
            {info.name}
          </span>
          {distanceM != null && (
            <span className="shrink-0 text-[10px] text-slate-400">约 {Math.round(distanceM)}m</span>
          )}
        </div>
      </div>

      {/* 售卖均价 */}
      <div className="flex items-center justify-between px-2.5 py-1.5 text-xs">
        <span className="text-slate-500">售卖均价</span>
        <span className="tabular-nums text-slate-700">
          {info.saleAvg != null ? `${Math.round(info.saleAvg)} 元/㎡` : "信息不足"}
        </span>
      </div>

      {/* 租金范围 */}
      {RENT_LABELS.map(([key, label]) => (
        <div key={key} className="flex items-center justify-between px-2.5 py-1.5 text-xs">
          <span className="text-slate-500">租金（{label}）</span>
          <span className="tabular-nums text-slate-700">{rentText(info.rent?.[key])}</span>
        </div>
      ))}

      {/* 简介 */}
      {info.intro && (
        <div className="border-t border-slate-100 px-2.5 py-1.5">
          <p className="text-xs leading-5 text-slate-600">{info.intro}</p>
        </div>
      )}
    </div>
  );
});

export default CommunityInfoBox;
