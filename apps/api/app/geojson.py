"""GeoJSON 坐标递归变换 + 请求坐标系归一（内部统一 WGS84，对外统一 BD09LL）。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from geo import bd09ll_to_wgs84, gcj02_to_wgs84, wgs84_to_bd09ll

CoordFn = Callable[[float, float], tuple[float, float]]


def _transform_coords(coords: Any, fn: CoordFn) -> Any:
    if not isinstance(coords, (list, tuple)):
        return coords
    if coords and isinstance(coords[0], (int, float)):
        x, y = fn(float(coords[0]), float(coords[1]))
        return [x, y]
    return [_transform_coords(c, fn) for c in coords]


def transform_geometry(geom: dict, fn: CoordFn) -> dict:
    """递归变换 GeoJSON geometry 的坐标（Point/Line/Polygon/Multi*/GeometryCollection）。"""
    if not geom:
        return geom
    if geom.get("type") == "GeometryCollection":
        return {
            **geom,
            "geometries": [transform_geometry(g, fn) for g in geom.get("geometries", [])],
        }
    return {**geom, "coordinates": _transform_coords(geom.get("coordinates", []), fn)}


def geometry_wgs84_to_bd09ll(geom: dict) -> dict:
    return transform_geometry(geom, wgs84_to_bd09ll)


def feature(geometry: dict, properties: dict | None = None) -> dict:
    return {"type": "Feature", "geometry": geometry, "properties": properties or {}}


def to_wgs84(lng: float, lat: float, coord_type: str | None) -> tuple[float, float]:
    """按请求坐标系把输入点归一到 WGS84；未知坐标系抛 ValueError。"""
    ct = (coord_type or "bd09ll").strip().lower()
    if ct == "wgs84":
        return (float(lng), float(lat))
    if ct == "gcj02":
        return gcj02_to_wgs84(float(lng), float(lat))
    if ct == "bd09ll":
        return bd09ll_to_wgs84(float(lng), float(lat))
    raise ValueError(f"不支持的坐标系 coordType: {coord_type}")
