"""8 方位扇区缺口画像（SectorGapService）。

对比约束等时斑与直线圆（80 m/min x X）在 8 方位的面积，
输出每个方向的 isochroneArea / circleArea / gapRatio，并生成
指认阻隔物的白话诊断句。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry

DIRECTIONS = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")
_DIRECTION_CN = {
    "N": "北",
    "NE": "东北",
    "E": "东",
    "SE": "东南",
    "S": "南",
    "SW": "西南",
    "W": "西",
    "NW": "西北",
}
# 各方向中心方位角（自 +x 轴逆时针，y 轴为北）
_DIRECTION_ANGLE = {"E": 0.0, "NE": 45.0, "N": 90.0, "NW": 135.0, "W": 180.0, "SW": 225.0, "S": 270.0, "SE": 315.0}
_HALF_SECTOR_DEG = 22.5
_WALK_SPEED_M_MIN = 80.0


@dataclass(slots=True)
class SectorGap:
    direction: str
    isochroneArea: float
    circleArea: float
    gapRatio: float


@dataclass(slots=True)
class SectorGapReport:
    gaps: list[SectorGap] = field(default_factory=list)
    worst_direction: str | None = None
    diagnosis: str = ""


def _sector_wedge(center_xy: tuple[float, float], radius_m: float, direction: str, n: int = 16) -> Polygon:
    cx, cy = center_xy
    mid = math.radians(_DIRECTION_ANGLE[direction])
    half = math.radians(_HALF_SECTOR_DEG)
    pts = [(cx, cy)]
    for k in range(n + 1):
        ang = mid - half + (2.0 * half) * k / n
        pts.append((cx + radius_m * math.cos(ang), cy + radius_m * math.sin(ang)))
    return Polygon(pts)


def circle_radius_m(minutes: int) -> float:
    """直线对照圆半径 = 80 m/min x X。"""
    return _WALK_SPEED_M_MIN * minutes


def compute_sector_gaps(
    isochrone_geom: BaseGeometry,
    center_xy: tuple[float, float],
    minutes: int,
) -> list[SectorGap]:
    """8 方位扇面与等时多边形求交面积。

    ``isochrone_geom`` 为本地米坐标几何，``center_xy`` 一般取 (0, 0)。
    """
    radius = circle_radius_m(minutes)
    gaps: list[SectorGap] = []
    for direction in DIRECTIONS:
        wedge = _sector_wedge(center_xy, radius, direction)
        circle_area = wedge.area
        iso_area = 0.0 if isochrone_geom.is_empty else isochrone_geom.intersection(wedge).area
        ratio = 0.0 if circle_area <= 0 else min(1.0, iso_area / circle_area)
        gaps.append(
            SectorGap(
                direction=direction,
                isochroneArea=iso_area,
                circleArea=circle_area,
                gapRatio=ratio,
            )
        )
    return gaps


def diagnose_worst_sector(
    gaps: list[SectorGap],
    barrier_labels: dict[str, list[str]] | None = None,
) -> tuple[str | None, str]:
    """生成最差方向的白话诊断句。

    ``barrier_labels``：方向 -> 阻隔物标签列表（如 {"SE": ["水域", "快速路"]}）。
    含水域类标签时追加「需绕桥」。
    """
    if not gaps:
        return None, ""
    worst = min(gaps, key=lambda g: g.gapRatio)
    if worst.gapRatio >= 0.95:
        return worst.direction, "各方向可达性均衡，无明显缺口。"
    cn = _DIRECTION_CN[worst.direction]
    labels = (barrier_labels or {}).get(worst.direction, [])
    if not labels:
        return worst.direction, f"{cn}方向等时圈缺口最大（达成率 {worst.gapRatio:.0%}），建议实地核查阻隔。"
    joined = "+".join(labels)
    suffix = "，需绕桥" if any(("水" in lb or "河" in lb) for lb in labels) else ""
    return (
        worst.direction,
        f"{cn}被{joined}切断{suffix}（达成率 {worst.gapRatio:.0%}）。",
    )


def sector_gap_report(
    isochrone_geom: BaseGeometry,
    center_xy: tuple[float, float],
    minutes: int,
    barrier_labels: dict[str, list[str]] | None = None,
) -> SectorGapReport:
    gaps = compute_sector_gaps(isochrone_geom, center_xy, minutes)
    worst, sentence = diagnose_worst_sector(gaps, barrier_labels)
    return SectorGapReport(gaps=gaps, worst_direction=worst, diagnosis=sentence)
