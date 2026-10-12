"""等时圈内「服务盲区」面：逐格判「直线 1km 内无题面设施组」。

赛题口径（菜市场/药店/小学）落成三组设施，按名称收窄匹配：
- market   缺菜市场/超市：convenience/mall 类名称含 菜场|菜市场|农贸市场|生鲜|超市
- pharmacy 缺药店/医院：  health 类名称含 药店|药房|医院|诊所|社区卫生服务
- school   缺小学：      education 类名称含 小学

网格 100m；判定域为传入的等时圈面（domain，WGS84 GeoJSON 几何）——
域内每个格心各自判「自身直线 1km 内有无该组设施」；未传 domain 时
退化为以中心为圆心的 1km 圆。逐类独立 4-连通聚合成面
（properties.missing 恒为单标签），长宽比 >3 的带状区域标记 corridor=true。
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from shapely.geometry import Point, mapping, shape
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

# 复合类名（API blindFilter 口径）→ 题面设施组；mall 归入 market 组
CATEGORY_TO_GROUP = {
    CATEGORY_CONVENIENCE: "market",
    CATEGORY_HEALTH: "pharmacy",
    CATEGORY_EDUCATION: "school",
}
GROUP_TO_CATEGORY = {g: c for c, g in CATEGORY_TO_GROUP.items()}

_MARKET_RE = re.compile(r"菜场|菜市场|农贸市场|生鲜|超市")
_PHARMACY_RE = re.compile(r"药店|药房|医院|诊所|社区卫生服务")
_SCHOOL_RE = re.compile(r"小学")


def blind_group(category: str, name: str | None) -> str | None:
    """题面设施组归类：market/pharmacy/school；名称不匹配返回 None。"""
    n = name or ""
    if category in (CATEGORY_CONVENIENCE, "mall") and _MARKET_RE.search(n):
        return "market"
    if category == CATEGORY_HEALTH and _PHARMACY_RE.search(n):
        return "pharmacy"
    if category == CATEGORY_EDUCATION and _SCHOOL_RE.search(n):
        return "school"
    return None


@dataclass(slots=True)
class BlindspotPoi:
    category: str
    lng: float
    lat: float
    name: str | None = None


def _cell_polygon(cx: float, cy: float, half: float) -> BaseGeometry:
    from shapely.geometry import box

    return box(cx - half, cy - half, cx + half, cy + half)


def _domain_local(domain: dict | None, proj: LocalProjection) -> BaseGeometry | None:
    """等时圈几何（WGS84）转本地米空间；空/非法输入按无域处理。"""
    if not domain:
        return None
    try:
        geom = shape(domain)
    except (ValueError, TypeError):
        return None
    if geom.is_empty:
        return None
    return shp_transform(lambda x, y: proj.to_xy(x, y), geom)


def _missing_cells(
    center: tuple[float, float],
    pois: list[BlindspotPoi | dict],
    categories: tuple[str, ...],
    domain: dict | None = None,
) -> tuple[
    LocalProjection,
    dict[tuple[int, int], tuple[float, float]],
    dict[tuple[int, int], set[str]],
]:
    """公共前置：POI 归一化投影 + 网格生成（等时圈域/1km 圆）+ 逐格判缺。

    返回 (投影, 格心表 {(ix,iy): (cx,cy) 米}, 缺格表 {(ix,iy): {缺失类别}})。
    """
    proj = LocalProjection(*center)
    norm_pois: list[tuple[str, float, float]] = []
    for poi in pois:
        if isinstance(poi, dict):
            cat = str(poi.get("category") or "")
            name = poi.get("name")
            lng = float(poi["lng"])
            lat = float(poi["lat"])
        else:
            cat, name, lng, lat = poi.category, poi.name, poi.lng, poi.lat
        group = blind_group(cat, name)
        if group is None:
            continue  # 非题面设施（便利店/养老院/幼儿园等）不参与判缺
        cat_key = GROUP_TO_CATEGORY[group]  # mall → convenience 键
        if cat_key not in categories:
            continue
        x, y = proj.to_xy(lng, lat)
        norm_pois.append((cat_key, x, y))

    # 1) 生成网格：有等时圈域时覆盖域 bbox（格心在域内），否则退化 1km 圆
    domain_geom = _domain_local(domain, proj)
    cells: dict[tuple[int, int], tuple[float, float]] = {}
    if domain_geom is not None:
        minx, miny, maxx, maxy = domain_geom.bounds
        ix0, ix1 = math.floor(minx / GRID_CELL_M), math.ceil(maxx / GRID_CELL_M)
        iy0, iy1 = math.floor(miny / GRID_CELL_M), math.ceil(maxy / GRID_CELL_M)
        for ix in range(ix0, ix1 + 1):
            for iy in range(iy0, iy1 + 1):
                cx, cy = ix * GRID_CELL_M, iy * GRID_CELL_M
                if domain_geom.contains(Point(cx, cy)):
                    cells[(ix, iy)] = (cx, cy)
    else:
        n = math.ceil(BLINDSPOT_RADIUS_M / GRID_CELL_M)
        for ix in range(-n, n + 1):
            for iy in range(-n, n + 1):
                cx, cy = ix * GRID_CELL_M, iy * GRID_CELL_M
                if math.hypot(cx, cy) <= BLINDSPOT_RADIUS_M:
                    cells[(ix, iy)] = (cx, cy)

    # 2) 每格判缺：该格中心直线 1km 内无该类题面设施
    r2 = BLINDSPOT_RADIUS_M * BLINDSPOT_RADIUS_M
    missing: dict[tuple[int, int], set[str]] = {}
    for key, (cx, cy) in cells.items():
        miss: set[str] = set()
        for cat in categories:
            available = False
            for pcat, px, py in norm_pois:
                if pcat != cat:
                    continue
                if (px - cx) ** 2 + (py - cy) ** 2 > r2:
                    continue
                available = True
                break
            if not available:
                miss.add(cat)
        if miss:
            missing[key] = miss
    return proj, cells, missing


def blindspots(
    center: tuple[float, float],
    pois: list[BlindspotPoi | dict],
    categories: tuple[str, ...] = DEFAULT_CATEGORIES,
    domain: dict | None = None,
) -> dict:
    """计算服务盲区面，输出 GeoJSON FeatureCollection。

    - 判定域为 ``domain``（等时圈，WGS84 几何）内的格；缺省为中心 1km 圆；
    - 每格判定「该格直线 1km 内无该类题面设施（按名称收窄）」为缺；
    - 逐类 4-连通聚合缺格为区域（properties.missing 恒为单标签），
      长宽比 >3 标记 corridor=true。
    """
    proj, cells, missing = _missing_cells(center, pois, categories, domain)
    half = GRID_CELL_M / 2.0

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
    return {"type": "FeatureCollection", "totalCells": len(cells), "features": features}


def blindspots_from_geojson(
    center: tuple[float, float],
    poi_geojson: dict,
    categories: tuple[str, ...] = DEFAULT_CATEGORIES,
    domain: dict | None = None,
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
                name=props.get("name"),
            )
        )
    return blindspots(center, pois, categories=categories, domain=domain)
