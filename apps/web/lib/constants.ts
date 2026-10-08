import type { PoiCategory, SectorDirection } from "./types";

export const DEFAULT_CENTER = { lng: 116.4627, lat: 39.8842 }; // 劲松街道（OSM 真实路网路口，bd09ll）
export const DEFAULT_MINUTES = 15;
export const DEFAULT_LOCATION_LABEL = "劲松街道 · 北京";
export const WALK_SPEED_M_PER_MIN = 72; // rough estimate for radius fallback

export const MIN_MINUTES = 5;
export const MAX_MINUTES = 30;
export const MINUTE_PRESETS = [5, 15];

export const POI_CATEGORY_META: Record<
  PoiCategory,
  { name: string; color: string }
> = {
  convenience: { name: "便利生活", color: "#f97316" },
  leisure: { name: "娱乐休闲", color: "#ec4899" },
  mall: { name: "商超购物", color: "#eab308" },
  education: { name: "学习教育", color: "#3b82f6" },
  health: { name: "医疗养老", color: "#ef4444" },
  public: { name: "公共空间", color: "#16a34a" },
  transport: { name: "交通出行", color: "#8b5cf6" },
};

export const POI_CATEGORIES = Object.keys(POI_CATEGORY_META) as PoiCategory[];

export const SECTOR_DIRECTIONS: SectorDirection[] = [
  "N",
  "NE",
  "E",
  "SE",
  "S",
  "SW",
  "W",
  "NW",
];

export const SECTOR_LABEL: Record<SectorDirection, string> = {
  N: "北",
  NE: "东北",
  E: "东",
  SE: "东南",
  S: "南",
  SW: "西南",
  W: "西",
  NW: "西北",
};

/**
 * 等时分带配色（index 0 = 最内层）。
 * 刻意拉开色相（绿→蓝→琥珀→紫→粉）而非同色系深浅：
 * 分层多边形嵌套绘制、半透明互相叠染，同色系只有亮度差几乎无法一眼区分。
 * 每层描边为纯色，环带边界靠描边即可辨认。
 */
export const ISO_COLORS = ["#16a34a", "#2563eb", "#f59e0b", "#9333ea", "#ec4899"];
export function isoColor(index: number, total: number): string {
  // index 0 = smallest band (innermost)；分带数超过色板时按比例取段
  if (total <= ISO_COLORS.length) {
    return ISO_COLORS[Math.max(0, Math.min(ISO_COLORS.length - 1, index))];
  }
  return ISO_COLORS[Math.min(ISO_COLORS.length - 1, Math.floor((index / total) * ISO_COLORS.length))];
}

export const BUILDING_COLORS: Record<string, { fill: string; stroke?: string; fillOpacity?: number }> = {
  enterable: { fill: "#2dd4bf", fillOpacity: 0.55 },
  podium: { fill: "transparent", stroke: "#0d9488", fillOpacity: 0 },
  blocked: { fill: "#a8a29e", fillOpacity: 0.7 },
  open: { fill: "#bbf7d0", fillOpacity: 0.6 },
};

export const BUILDING_CATEGORY_LABEL: Record<string, string> = {
  enterable: "可进入建筑",
  podium: "裙楼(沿街可入)",
  blocked: "封闭院落",
  open: "开放空间",
};
