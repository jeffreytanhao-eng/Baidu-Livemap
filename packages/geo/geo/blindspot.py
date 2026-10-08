"""1 km 三类（便利生活/医疗保障/学习教育）盲区灰块。

网格半径写死 1000m、格宽 100m，不随 X 缩放；直线口径判缺，
增强开关「步行 >X 分钟不可达」才跟 X 走。逐类独立 4-连通聚合成区域
（每类 missing 单标签，避免跨类并集裹挟），
长宽比 >3 的带状区域标记 corridor=true（灰色走廊）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from shapely.geometry import mapping, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform as shp_transform
from shapely.ops import unary_union

from .coords import LocalProjection

BLINDSPOT_RADIUS_M = 1000.0  # 写死 1km，不随 X
GRID_CELL_M = 100.0
CORRIDOR_ASPECT = 3.0

CATEGORY_CONVENIENCE = "convenience"
CATEGORY_HEALTH = "health"
CATEGORY_EDUCATION = "education"
DEFAULT_CATEGORIES = (CATEGORY_CONVENIENCE, CATEGORY_HEALTH, CATEGORY_EDUCATION)


@dataclass(slots=True)
class BlindspotPoi:
    category: str
    lng: float
    lat: float
    walk_minutes: float | None = None  # 增强模式用


def _cell_polygon(cx: float, cy: float, half: float) -> BaseGeometry:
    from shapely.geometry import box

    return box(cx - half, cy - half, cx + half, cy + half)


def blindspots(
    center: tuple[float, float],
    pois: list[BlindspotPoi | dict],
    minutes: int = 15,
    enhanced: bool = False,
    categories: tuple[str, ...] = DEFAULT_CATEGORIES,
) -> dict:
    """计算 1km 盲区灰块，输出 GeoJSON FeatureCollection。

    - 网格范围写死 1000m（不随 ``minutes``）；
    - 每格判定「直线 1km 内无该类 POI」为缺；
    - ``enhanced=True`` 时，提供了 walk_minutes 的 POI 还需满足 <= minutes 才算可达；
    - 逐类 4-连通聚合缺格为区域（properties.missing 恒为单标签），
      长宽比 >3 标记 corridor=true。
    """
    proj = LocalProjection(*center)
    norm_pois: list[tuple[str, float, float, float | None]] = []
    for poi in pois:
        if isinstance(poi, dict):
            cat = str(poi.get("category") or "")
            lng = float(poi["lng"])
            lat = float(poi["lat"])
            walk = poi.get("walk_minutes")
        else:
            cat, lng, lat, walk = poi.category, poi.lng, poi.lat, poi.walk_minutes
        if cat not in categories:
            continue
        x, y = proj.to_xy(lng, lat)
        norm_pois.append((cat, x, y, None if walk is None else float(walk)))

    # 1) 生成覆盖 1km 圆的 100m 网格（范围与 minutes 无关）
    n = math.ceil(BLINDSPOT_RADIUS_M / GRID_CELL_M)
    half = GRID_CELL_M / 2.0
    cells: dict[tuple[int, int], tuple[float, float]] = {}
    for ix in range(-n, n + 1):
        for iy in range(-n, n + 1):
            cx, cy = ix * GRID_CELL_M, iy * GRID_CELL_M
            if math.hypot(cx, cy) <= BLINDSPOT_RADIUS_M:
                cells[(ix, iy)] = (cx, cy)

    # 2) 每格判缺
    r2 = BLINDSPOT_RADIUS_M * BLINDSPOT_RADIUS_M
    missing: dict[tuple[int, int], set[str]] = {}
    for key, (cx, cy) in cells.items():
        miss: set[str] = set()
        for cat in categories:
            available = False
            for pcat, px, py, walk in norm_pois:
                if pcat != cat:
                    continue
                if (px - cx) ** 2 + (py - cy) ** 2 > r2:
                    continue
                if enhanced and walk is not None and walk > minutes:
                    continue  # 步行 >X 分钟不可达
                available = True
                break
            if not available:
                miss.add(cat)
        if miss:
            missing[key] = miss

    # 3) 逐类独立 4-连通聚合。
    # 不能把三类缺格混在一起聚合：某类全缺时会把整片连通成大区域，
    # missing 取并集会把邻近其它类（明明有达标设施）裹挟进标签，
    # 导致「小学 2.9 分钟达标」却被计为 100% 盲区的假象。
    features: list[dict] = []
    for cat in categories:
        cat_missing = {k for k, m in missing.items() if cat in m}
        if not cat_missing:
            continue
        seen: set[tuple[int, int]] = set()
        for key in cat_missing:
            if key in seen:
                continue
            stack = [key]
            seen.add(key)
            region: list[tuple[int, int]] = []
            while stack:
                cur = stack.pop()
                region.append(cur)
                ix, iy = cur
                for nb in ((ix + 1, iy), (ix - 1, iy), (ix, iy + 1), (ix, iy - 1)):
                    if nb in cat_missing and nb not in seen:
                        seen.add(nb)
                        stack.append(nb)

            polys = [_cell_polygon(*cells[k], half) for k in region]
            region_geom = unary_union(polys)
            xs = [k[0] for k in region]
            ys = [k[1] for k in region]
            span_x = max(xs) - min(xs) + 1
            span_y = max(ys) - min(ys) + 1
            lo = max(1, min(span_x, span_y))
            aspect = max(span_x, span_y) / lo
            corridor = aspect > CORRIDOR_ASPECT

            wgs_geom = shp_transform(lambda x, y: proj.to_lnglat(x, y), region_geom)
            features.append(
                {
                    "type": "Feature",
                    "geometry": mapping(wgs_geom),
                    "properties": {
                        "missing": [cat],
                        "corridor": corridor,
                        "cells": len(region),
                    },
                }
            )
    return {"type": "FeatureCollection", "features": features}


def blindspots_from_geojson(
    center: tuple[float, float],
    poi_geojson: dict,
    minutes: int = 15,
    enhanced: bool = False,
    categories: tuple[str, ...] = DEFAULT_CATEGORIES,
) -> dict:
    """便捷入口：POI 以 GeoJSON FeatureCollection 传入。"""
    pois: list[BlindspotPoi] = []
    for feat in poi_geojson.get("features", []):
        props = feat.get("properties") or {}
        geom = feat.get("geometry") or {}
        if geom.get("type") != "Point":
            continue
        lng, lat = geom["coordinates"][:2]
        pois.append(
            BlindspotPoi(
                category=str(props.get("category") or ""),
                lng=float(lng),
                lat=float(lat),
                walk_minutes=props.get("walk_minutes"),
            )
        )
    _ = shape  # 保留接口对称性
    return blindspots(center, pois, minutes=minutes, enhanced=enhanced, categories=categories)
