"use client";

import { forwardRef, useState, type CSSProperties } from "react";
import { API_BASE } from "@/lib/api";
import type { CommunityInfo } from "@/lib/types";

const RENT_LABELS: [keyof CommunityInfo["rent"], string][] = [
  ["l1", "一居"],
  ["l2", "两居"],
  ["l3", "三居"],
];

/** 小区资讯框：定位点右侧浮层。
 *  照片（单张，加载失败显示占位灰块）、售卖均价、租金范围（一居/两居/三居，
 *  缺失显示「信息不足」）、简介与来源；右上角可关闭。
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
  const [imgFailed, setImgFailed] = useState(false);
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
        {info.photoUrl && !imgFailed ? (
          /* eslint-disable-next-line @next/next/no-img-element */
          <img
            src={info.photoUrl.startsWith("http") ? info.photoUrl : `${API_BASE}${info.photoUrl}`}
            alt={info.name}
            className="h-28 w-full rounded-md object-cover"
            onError={() => setImgFailed(true)}
          />
        ) : (
          <div className="flex h-28 w-full items-center justify-center rounded-md bg-slate-100 text-xs text-slate-400">
            暂无照片
          </div>
        )}
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
