"use client";

import { MINUTE_PRESETS } from "@/lib/constants";
import type { Phase } from "@/lib/types";
import { Button } from "@/components/ui/button";

/** 移动端固定底栏：分钟档 + 开始体验 */
export default function MobileBar({
  minutes,
  onMinutesChange,
  dirty,
  phase,
  onStart,
}: {
  minutes: number;
  onMinutesChange: (m: number) => void;
  dirty: boolean;
  phase: Phase;
  onStart: () => void;
}) {
  const running = phase !== "idle";
  return (
    <div className="fixed inset-x-0 bottom-0 z-40 flex h-14 items-center gap-2 border-t border-slate-200 bg-white px-3 no-print md:hidden">
      <div className="flex overflow-hidden rounded-lg border border-slate-300">
        {MINUTE_PRESETS.map((m) => (
          <button
            key={m}
            type="button"
            onClick={() => onMinutesChange(m)}
            className={`h-9 w-10 text-sm font-medium ${
              minutes === m ? "bg-brand text-white" : "bg-white text-slate-600"
            }`}
          >
            {m}
          </button>
        ))}
      </div>
      {!MINUTE_PRESETS.includes(minutes) && (
        <span className="rounded-lg bg-brand px-2 py-1 text-sm font-medium text-white">
          {minutes} 分
        </span>
      )}
      {dirty && (
        <span className="flex items-center gap-1 text-xs font-medium text-amber-600">
          <span className="inline-block h-2 w-2 rounded-full bg-amber-400" />
          未体验
        </span>
      )}
      <div className="flex-1" />
      <Button size="md" onClick={onStart} disabled={running} className="min-w-28">
        {running ? "体验中…" : "开始体验"}
      </Button>
    </div>
  );
}
