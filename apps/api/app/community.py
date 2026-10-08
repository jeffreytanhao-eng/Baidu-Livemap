"""小区资讯定位：点击点 → 小区面识别（点在面内）→ 数据库小区匹配。

- 面识别：内存 fabric 的 residential 层（含 OSM 占位面）做 point-in-polygon，
  复用 MapFabricService 的 STRtree（region 本地米坐标）；
- 匹配：命中面有名字 → 名称精确/互含匹配；无名或未命中 → 面中心与点击
  坐标在 DB 找 500m 内最近小区；均不中返回 matchedBy="none"。
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

from shapely.geometry import Point

from . import db
from .engine import EngineContext
from .geojson import to_wgs84

logger = logging.getLogger("livemap.community")


def _region_for_point(ctx: EngineContext, lng: float, lat: float) -> str | None:
    """与 main.fabric 同源的区域选择：网格片名直取，否则全量 patch bbox 命中。"""
    if ctx.tiles is not None and ctx.tiles.has_tile(lng, lat):
        from geo.tiles import tile_indices, tile_name

        return tile_name(*tile_indices(lng, lat))
    for name, bundle in ctx.regions.items():
        if bundle.lite:
            continue
        loaded = ctx.service._regions.get(name)
        if loaded is None:
            continue
        x0, y0, x1, y1 = loaded.bbox_wgs84
        if x0 <= lng <= x1 and y0 <= lat <= y1:
            return name
    return None


def _hit_residential(
    ctx: EngineContext, region_name: str, lng: float, lat: float
) -> tuple[dict[str, Any], tuple[float, float]] | None:
    """点在面内命中 residential 面：返回 (props, 面中心 WGS84)。"""
    region = ctx.service._regions[region_name]
    x, y = region.projection.to_xy(lng, lat)
    pt = Point(x, y)
    tree = region.trees["residential"]
    feats = region.layers["residential"]
    # 注意：shapely STRtree 的 "contains" 语义是反向的（tree 几何作为被包含方），
    # 点查询一律用 "intersects"（与方向无关），再由 geom.contains 精确确认
    for idx in tree.query(pt, predicate="intersects"):
        geom, props = feats[int(idx)]
        if geom.contains(pt) or geom.touches(pt):
            c = geom.centroid
            return props, region.projection.to_lnglat(c.x, c.y)
    return None


def locate(ctx: EngineContext, conn: sqlite3.Connection, lng: float, lat: float) -> dict[str, Any]:
    """点击定位：返回统一三态结果（name 命中 / coord 兜底 / none 未命中）。"""
    none_result: dict[str, Any] = {"matchedBy": "none", "community": None, "hitFace": None}
    region_name = _region_for_point(ctx, lng, lat)
    if region_name is None:
        return none_result
    if region_name not in ctx.service._regions:
        try:
            ctx.service.load_region(region_name)
        except (FileNotFoundError, ValueError):
            return none_result

    hit = _hit_residential(ctx, region_name, lng, lat)
    face_props, face_center = hit if hit is not None else ({}, (lng, lat))
    face_name = str(face_props.get("name") or "").strip()

    if face_name:
        row = db.find_by_name(conn, face_name)
        if row is not None:
            return {
                "matchedBy": "name",
                "community": row,
                "hitFace": {"name": face_name or None, "placeholder": bool(face_props.get("placeholder"))},
            }

    # 坐标兜底：先命中面中心，再点击点本身
    for pt_lng, pt_lat in (face_center, (lng, lat)):
        found = db.find_nearest(conn, pt_lng, pt_lat, max_m=500.0)
        if found is not None:
            row, dist = found
            return {
                "matchedBy": "coord",
                "community": row,
                "distanceM": round(dist, 1),
                "hitFace": {"name": face_name or None, "placeholder": bool(face_props.get("placeholder"))},
            }
    return {
        "matchedBy": "none",
        "community": None,
        "hitFace": {"name": face_name or None, "placeholder": bool(face_props.get("placeholder"))},
    }


def parse_wgs(lng: float, lat: float, coord_type: str | None) -> tuple[float, float]:
    return to_wgs84(lng, lat, coord_type or "bd09ll")
