"use client";

import { useState } from "react";
import { BUILDING_CATEGORY_LABEL, BUILDING_COLORS, POI_CATEGORY_META, isoColor } from "@/lib/constants";
import type { CheckupResponse, LayerVisibility } from "@/lib/types";
import { Switch } from "@/components/ui/switch";

const LAYER_ITEMS: { key: keyof LayerVisibility; label: string }[] = [
  { key: "isochrone", label: "等时分层" },
  { key: "roads", label: "路网" },
  { key: "buildings", label: "建筑可进入性" },
  { key: "water", label: "水域" },
  { key: "barriers", label: "快速路/阻隔" },
  { key: "pois", label: "设施点(POI)" },
];

export default function LayerPanel({
  visible,
  onChange,
  result,
}: {
  visible: LayerVisibility;
  onChange: (v: LayerVisibility) => void;
  result: CheckupResponse | null;
}) {
  const [open, setOpen] = useState(true);

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="absolute bottom-16 left-4 z-30 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm font-medium text-slate-700 shadow-md no-print md:bottom-4"
      >
        图层 ▴
      </button>
    );
  }

  const isoLegend = result
    ? [...result.layers].sort((a, b) => a.minutes - b.minutes)
    : [];

  return (
    <div className="absolute bottom-16 left-4 z-30 max-h-[70%] w-56 overflow-y-auto rounded-lg border border-slate-200 bg-white p-3 shadow-lg no-print md:bottom-4">
      <div className="mb-2 flex items-center justify-between">
        <span className="text-sm font-semibold text-slate-800">图层</span>
        <button
          type="button"
          className="text-xs text-slate-400 hover:text-slate-600"
          onClick={() => setOpen(false)}
        >
          收起 ▾
        </button>
      </div>
      <div className="space-y-2">
        {LAYER_ITEMS.map((item) => (
          <div key={item.key} className="flex items-center justify-between gap-2">
            <label className="text-sm text-slate-700" htmlFor={`layer-${item.key}`}>
              {item.label}
            </label>
            <Switch
              id={`layer-${item.key}`}
              checked={visible[item.key]}
              onCheckedChange={(v) => onChange({ ...visible, [item.key]: v })}
            />
          </div>
        ))}
      </div>

      {isoLegend.length > 0 && (
        <div className="mt-3 border-t border-slate-100 pt-2">
          <div className="mb-1 text-xs font-medium text-slate-500">等时图例</div>
          {isoLegend.map((l, i) => (
            <div key={l.minutes} className="flex items-center gap-2 py-0.5 text-xs text-slate-600">
              <span
                className="inline-block h-3 w-3 rounded-sm"
                style={{ backgroundColor: isoColor(i, isoLegend.length) }}
              />
              {l.minutes} 分钟可达
            </div>
          ))}
        </div>
      )}

      <div className="mt-3 border-t border-slate-100 pt-2">
        <div className="mb-1 text-xs font-medium text-slate-500">建筑</div>
        {Object.entries(BUILDING_CATEGORY_LABEL).map(([k, label]) => {
          const c = BUILDING_COLORS[k];
          return (
            <div key={k} className="flex items-center gap-2 py-0.5 text-xs text-slate-600">
              <span
                className="inline-block h-3 w-3 rounded-sm border"
                style={{
                  backgroundColor: c.fill === "transparent" ? "#ffffff" : c.fill,
                  borderColor: c.stroke ?? "#d6d3d1",
                }}
              />
              {label}
            </div>
          );
        })}
        <div className="flex items-center gap-2 py-0.5 text-xs text-slate-600">
          <span
            className="inline-block h-3 w-3 rounded-sm border border-dashed"
            style={{ backgroundColor: "rgba(148, 163, 184, 0.25)", borderColor: "#64748b" }}
          />
          小区范围（OSM 未细化楼栋）
        </div>
      </div>

      {result && (
        <div className="mt-3 border-t border-slate-100 pt-2">
          <div className="mb-1 text-xs font-medium text-slate-500">设施</div>
          <div className="grid grid-cols-2 gap-x-2">
            {Object.entries(POI_CATEGORY_META).map(([k, m]) => (
              <div key={k} className="flex items-center gap-1.5 py-0.5 text-xs text-slate-600">
                <span className="inline-block h-2.5 w-2.5 rounded-full" style={{ backgroundColor: m.color }} />
                {m.name}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
