"use client";

import { useEffect, useState } from "react";
import { loadBMapGL } from "@/lib/baiduLoader";
import BaiduMapImpl from "./BaiduMapImpl";
import SvgMapImpl from "./SvgMapImpl";
import type { MapImplProps } from "./mapTypes";

type Mode = "pending" | "baidu" | "svg";

export default function MapView(props: MapImplProps) {
  const [mode, setMode] = useState<Mode>("pending");

  useEffect(() => {
    const ak = process.env.NEXT_PUBLIC_BAIDU_JS_AK;
    if (!ak) {
      setMode("svg");
      return;
    }
    let alive = true;
    loadBMapGL(ak)
      .then(() => {
        if (alive) setMode("baidu");
      })
      .catch(() => {
        if (alive) setMode("svg");
      });
    return () => {
      alive = false;
    };
  }, []);

  return (
    <div className="relative h-full w-full">
      {mode === "baidu" && <BaiduMapImpl {...props} />}
      {mode === "svg" && <SvgMapImpl {...props} />}
      {mode === "pending" && (
        <div className="flex h-full w-full items-center justify-center bg-[#f2f1ec] text-sm text-slate-500">
          地图加载中…
        </div>
      )}

      {/* 分阶段进度（说人话） */}
      {props.phase !== "idle" && (
        <div className="pointer-events-none absolute left-1/2 top-4 z-30 -translate-x-1/2 rounded-md border border-teal-200 bg-white px-4 py-2 text-sm font-medium text-teal-800 shadow-md">
          <span className="mr-2 inline-block h-2 w-2 animate-pulse rounded-full bg-teal-500" />
          {props.phase === "fast" ? "正在快速出圈…" : "正在叠加建筑与设施检索…"}
        </div>
      )}
    </div>
  );
}
