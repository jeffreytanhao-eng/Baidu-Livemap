"""MapFabricService：约束步行底图服务。

启动时一次性把 ``data/map_fabric/<region>/`` 下的 6 个 GeoJSON
（roads / buildings / residential / water / green / barriers，WGS84 坐标）载入内存，
转本地米投影并建立 STRtree 空间索引；提供按中心+半径裁切与覆盖判断。
请求期内不再解析 GeoJSON。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from shapely import STRtree
from shapely.geometry import mapping, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform as shp_transform

from .coords import LocalProjection

LAYER_ROADS = "roads"
LAYER_BUILDINGS = "buildings"
LAYER_RESIDENTIAL = "residential"
LAYER_WATER = "water"
LAYER_GREEN = "green"
LAYER_BARRIERS = "barriers"
LAYER_FILES = {
    LAYER_ROADS: "roads.geojson",
    LAYER_BUILDINGS: "buildings.geojson",
    LAYER_RESIDENTIAL: "residential.geojson",
    LAYER_WATER: "water.geojson",
    LAYER_GREEN: "green.geojson",
    LAYER_BARRIERS: "barriers.geojson",
}


@dataclass(slots=True)
class FabricLayers:
    """一次裁切的结果（几何均为以 center 为原点的本地米坐标）。"""

    center: tuple[float, float]
    radius_m: float
    roads: list[tuple[BaseGeometry, dict]] = field(default_factory=list)
    buildings: list[tuple[BaseGeometry, dict]] = field(default_factory=list)
    residential: list[tuple[BaseGeometry, dict]] = field(default_factory=list)
    water: list[tuple[BaseGeometry, dict]] = field(default_factory=list)
    green: list[tuple[BaseGeometry, dict]] = field(default_factory=list)
    barriers: list[tuple[BaseGeometry, dict]] = field(default_factory=list)


@dataclass
class _Region:
    name: str
    projection: LocalProjection
    bbox_wgs84: tuple[float, float, float, float]  # min_lng, min_lat, max_lng, max_lat
    layers: dict[str, list[tuple[BaseGeometry, dict]]]
    trees: dict[str, STRtree]


def _iter_features(path: Path) -> list[tuple[BaseGeometry, dict]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as fh:
        data: dict[str, Any] = json.load(fh)
    out: list[tuple[BaseGeometry, dict]] = []
    for feat in data.get("features", []):
        geom = feat.get("geometry")
        if not geom:
            continue
        props = dict(feat.get("properties") or {})
        out.append((shape(geom), props))
    return out


def _to_local(geom: BaseGeometry, proj: LocalProjection) -> BaseGeometry:
    return shp_transform(lambda x, y: proj.to_xy(x, y), geom)


class MapFabricService:
    """约束步行底图：多 region 内存驻留 + 空间索引 + 半径裁切。"""

    def __init__(self, fabric_root: str | Path) -> None:
        self.fabric_root = Path(fabric_root)
        self._regions: dict[str, _Region] = {}

    @property
    def regions(self) -> list[str]:
        return sorted(self._regions)

    def load_region(self, region: str) -> None:
        """把 ``<fabric_root>/<region>/`` 下的 4 层 GeoJSON 一次载入内存。"""
        region_dir = self.fabric_root / region
        if not region_dir.is_dir():
            raise FileNotFoundError(f"map fabric region not found: {region_dir}")

        wgs_layers: dict[str, list[tuple[BaseGeometry, dict]]] = {}
        min_lng = min_lat = float("inf")
        max_lng = max_lat = float("-inf")
        for layer, filename in LAYER_FILES.items():
            feats = _iter_features(region_dir / filename)
            wgs_layers[layer] = feats
            for geom, _ in feats:
                x0, y0, x1, y1 = geom.bounds
                min_lng, min_lat = min(min_lng, x0), min(min_lat, y0)
                max_lng, max_lat = max(max_lng, x1), max(max_lat, y1)
        if min_lng is float("inf"):
            raise ValueError(f"map fabric region empty: {region_dir}")

        proj = LocalProjection((min_lng + max_lng) / 2.0, (min_lat + max_lat) / 2.0)
        local_layers: dict[str, list[tuple[BaseGeometry, dict]]] = {}
        trees: dict[str, STRtree] = {}
        for layer, feats in wgs_layers.items():
            local = [(_to_local(g, proj), p) for g, p in feats]
            local_layers[layer] = local
            trees[layer] = STRtree([g for g, _ in local]) if local else STRtree([])

        self._regions[region] = _Region(
            name=region,
            projection=proj,
            bbox_wgs84=(min_lng, min_lat, max_lng, max_lat),
            layers=local_layers,
            trees=trees,
        )

    def covers(self, lng: float, lat: float) -> bool:
        """某点是否落在任一已加载 region 的包内（bbox 判定）。"""
        for region in self._regions.values():
            x0, y0, x1, y1 = region.bbox_wgs84
            if x0 <= lng <= x1 and y0 <= lat <= y1:
                return True
        return False

    def _pick_region(self, lng: float, lat: float) -> _Region | None:
        for region in self._regions.values():
            x0, y0, x1, y1 = region.bbox_wgs84
            if x0 <= lng <= x1 and y0 <= lat <= y1:
                return region
        return None

    def clip_region(
        self, region: str, center_lnglat: tuple[float, float], radius_m: float
    ) -> FabricLayers:
        """对指定 region 按中心（WGS84）+ 半径（米）裁切，几何转到以中心为原点。

        与 :meth:`clip` 的区别：不按中心点 bbox 猜 region，直接用调用方指定的
        region。网格片bbox因水系/快速路可能很宽，按点猜会命中别的片
        （通州点裁到西城路网事故的根因），建图路径必须用本方法。
        """
        lng, lat = center_lnglat
        out = FabricLayers(center=center_lnglat, radius_m=radius_m)
        loaded = self._regions.get(region)
        if loaded is None:
            return out

        cx, cy = loaded.projection.to_xy(lng, lat)
        window = _circle_polygon(cx, cy, radius_m)

        def shift(geom: BaseGeometry) -> BaseGeometry:
            return shp_transform(lambda x, y: (x - cx, y - cy), geom)

        for layer in LAYER_FILES:
            feats = loaded.layers[layer]
            tree = loaded.trees[layer]
            hits: list[tuple[BaseGeometry, dict]] = []
            for idx in tree.query(window, predicate="intersects"):
                geom, props = feats[int(idx)]
                inter = geom.intersection(window)
                if inter.is_empty:
                    continue
                hits.append((shift(inter), props))
            getattr(out, layer).extend(hits)
        return out

    def clip(self, center_lnglat: tuple[float, float], radius_m: float) -> FabricLayers:
        """按中心（WGS84）+ 半径（米）裁切各层，几何转到以中心为原点的米坐标。"""
        lng, lat = center_lnglat
        out = FabricLayers(center=center_lnglat, radius_m=radius_m)
        region = self._pick_region(lng, lat)
        if region is None:
            return out

        # 裁切窗口：在该 region 投影下的圆形缓冲（稍放大一圈防边缘丢失）
        cx, cy = region.projection.to_xy(lng, lat)
        window = _circle_polygon(cx, cy, radius_m)

        # 把命中几何从 region 原点平移到请求中心原点
        off_x, off_y = region.projection.to_xy(lng, lat)

        def shift(geom: BaseGeometry) -> BaseGeometry:
            return shp_transform(lambda x, y: (x - off_x, y - off_y), geom)

        for layer in LAYER_FILES:
            feats = region.layers[layer]
            tree = region.trees[layer]
            hits: list[tuple[BaseGeometry, dict]] = []
            for idx in tree.query(window, predicate="intersects"):
                geom, props = feats[int(idx)]
                inter = geom.intersection(window)
                if inter.is_empty:
                    continue
                hits.append((shift(inter), props))
            getattr(out, layer).extend(hits)
        return out


def _circle_polygon(cx: float, cy: float, r: float, n: int = 64) -> BaseGeometry:
    from shapely.geometry import Point

    return Point(cx, cy).buffer(r, quad_segs=n // 4)


def layers_to_geojson(layers: FabricLayers) -> dict[str, Any]:
    """调试用：把裁切结果转回 WGS84 GeoJSON FeatureCollection。"""
    proj = LocalProjection(*layers.center)
    feats: list[dict[str, Any]] = []
    for layer in LAYER_FILES:
        for geom, props in getattr(layers, layer):
            wgs = shp_transform(lambda x, y: proj.to_lnglat(x, y), geom)
            p = dict(props)
            p["_layer"] = layer
            feats.append({"type": "Feature", "geometry": mapping(wgs), "properties": p})
    return {"type": "FeatureCollection", "features": feats}
