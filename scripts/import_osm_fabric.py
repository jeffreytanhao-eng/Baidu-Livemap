#!/usr/bin/env python3
"""import_osm_fabric.py — 从 Geofabrik OSM PBF 裁出指定 bbox 的 6 层真实底图。

图层规则（与 packages/geo 引擎语义对齐）：
- roads.geojson     ：可步行 highway（排除 motorway/trunk 主线、车行隧道、foot/access 禁行），
                      保留 highway/name/bridge/tunnel/layer 属性
- buildings.geojson ：building=* 面（含 multipolygon relation），保留分类器消费的标签
- residential.geojson：OSM 建筑空洞占位面——landuse=residential 大院面中
                      建筑覆盖率 < RESIDENTIAL_COVER_MIN 且面积达标的「未细化
                      小区」，properties.placeholder=True；不参与可进入性判定，
                      仅作展示占位（方案 B：OSM 底图色块占位）
- water.geojson     ：natural=water / waterway=riverbank 面
- green.geojson     ：公园/绿地面（leisure=park/garden/playground/common/pitch、
                      natural=wood/scrub、landuse=forest）——设施「面感知边缘
                      到达」用，与等时圈 OSM 几何同源
- barriers.geojson  ：motorway/trunk 主线 + 地表 railway=rail（不得跨越的硬阻隔）

可选参数：
- --ring6 ：用内置「北京六环路以内」粗边界多边形（近似，~18 顶点）代替 --bbox 裁剪，
            与跨越边界的完整 way 相交即保留（保证边界处路网连通）；
- --lite  ：精简档（大范围底图用）——跳过 buildings 层（等时圈只沿路网走，
            建筑只影响建筑进入性展示层）、roads/water 几何 Douglas-Peucker 简化
            （容差 ~3m / ~5m）、坐标精度降到 6 位小数；water/barriers 语义完整保留。

用法：
    .venv/Scripts/python scripts/import_osm_fabric.py \
        --pbf data/beijing-260927.osm.pbf --region jinsong \
        --bbox 116.436,39.868,116.480,39.901

    # 北京六环内精简档（全域 lite 底图）
    .venv/Scripts/python scripts/import_osm_fabric.py \
        --pbf data/beijing-260927.osm.pbf --region beijing6r --ring6 --lite
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import osmium
import shapely.wkb
from shapely.geometry import LineString, MultiPolygon, Polygon, box

REPO_ROOT = Path(__file__).resolve().parents[1]

# 可步行 highway 类型（ motorway/trunk 主线进 barriers；link 匝道除 motorway_link 外可走）
WALKABLE_HIGHWAY = {
    "primary", "secondary", "tertiary", "unclassified", "residential",
    "living_street", "pedestrian", "footway", "path", "steps", "cycleway",
    "track", "service", "corridor", "road", "bridleway",
    "primary_link", "secondary_link", "tertiary_link", "unclassified_link",
    "residential_link", "trunk_link",
}
BARRIER_HIGHWAY = {"motorway", "trunk"}
# 建筑分类器（geo.enterability）消费的标签键
BUILDING_PROPS = ("building", "name", "amenity", "shop", "leisure", "highway", "office")

# lite 档几何简化容差（度）：1e-5 度 ≈ 1.1m；barriers 不简化（正确性优先）
_LITE_ROAD_TOLERANCE = 3e-5  # ≈ 3.3m
_LITE_WATER_TOLERANCE = 5e-5  # ≈ 5m
_LITE_NDIGITS = 6

# residential 占位判定：大院面内建筑（footprint）面积占比低于该值视为「OSM 未细化」
RESIDENTIAL_COVER_MIN = 0.10
# 小于该面积（km²）的 residential 面不生成占位（零散宅基地/图斑无展示意义）
RESIDENTIAL_MIN_KM2 = 0.004  # 4000 m²

# green 公园/绿地面层标签（面感知边缘到达用）
GREEN_LEISURE = {"park", "garden", "playground", "common", "pitch"}
GREEN_NATURAL = {"wood", "scrub"}
# 度² → km² 转换（本脚本既有口径：111.32² × cos(39.9°)≈0.59）
_DEG2_TO_KM2 = 111.32**2 * 0.59

# 六环粗边界与引擎共享同一份定义（packages/geo/geo/ring6.py）
from geo.ring6 import RING6_POLYGON

_TRUTHY = {"yes", "true", "1", True}


def _round_coords(geom, ndigits: int = 7):
    from shapely.ops import transform

    return transform(lambda x, y, z=None: (round(x, ndigits), round(y, ndigits)), geom)


class FabricCollector(osmium.SimpleHandler):
    """单次调用收集 6 层要素：way() 收 roads/barriers，area() 收 buildings/water/green/residential。

    pyosmium SimpleHandler 检测到 area() 回调后会自动做两遍扫描并装配
    multipolygon relation（见 osmium/simple_handler.py 注释）。

    filter_geom 为 bbox 或 ring6 多边形（prepared 加速海量相交判断）；
    lite=True 时跳过 buildings、简化 roads/water 几何。
    """

    def __init__(self, filter_geom, lite: bool = False) -> None:
        super().__init__()
        import shapely

        self.filter = filter_geom
        shapely.prepare(self.filter)  # 海量 way 的 intersects 预检提速
        self.lite = lite
        self.wkbfab = osmium.geom.WKBFactory()
        self.roads: list[tuple[LineString, dict]] = []
        self.barriers: list[tuple[LineString, dict]] = []
        self.buildings: list[tuple[Polygon | MultiPolygon, dict]] = []
        self.water: list[tuple[Polygon | MultiPolygon, dict]] = []
        self.residential: list[tuple[Polygon | MultiPolygon, dict]] = []
        self.green: list[tuple[Polygon | MultiPolygon, dict]] = []

    def way(self, w) -> None:
        tags = w.tags
        highway = tags.get("highway", "")
        railway = tags.get("railway", "")
        if not highway and railway != "rail":
            return
        try:
            wkb = self.wkbfab.create_linestring(w)
        except osmium.InvalidLocationError:
            return
        geom = shapely.wkb.loads(wkb, hex=True)
        if geom.is_empty or not geom.intersects(self.filter):
            return
        if railway == "rail":
            # 地表铁路为硬阻隔；地下段（隧道/地铁化）不算
            if tags.get("tunnel") in _TRUTHY:
                return
            self.barriers.append((geom, {"railway": "rail", "name": tags.get("name", "")}))
            return
        if highway in BARRIER_HIGHWAY:
            # 桥上的主线仍是阻隔（行人本就走不上去）；辅路/匝道另算
            self.barriers.append((geom, {"highway": highway, "name": tags.get("name", "")}))
            return
        if highway not in WALKABLE_HIGHWAY:
            return
        if tags.get("foot") in ("no", "private") or tags.get("access") in ("no", "private"):
            return
        if self.lite:
            # DP 简化（保留拓扑）；太短/退化则丢弃
            geom = geom.simplify(_LITE_ROAD_TOLERANCE, preserve_topology=True)
            if geom.is_empty or geom.length < _LITE_ROAD_TOLERANCE:
                return
        props = {
            "highway": highway,
            "name": tags.get("name", ""),
            "bridge": tags.get("bridge", ""),
            "tunnel": tags.get("tunnel", ""),
            "layer": tags.get("layer", ""),
            "source": "osm",
        }
        self.roads.append((geom, props))

    def area(self, a) -> None:
        tags = a.tags
        is_building = tags.get("building") not in (None, "no") and not self.lite
        is_water = tags.get("natural") == "water" or tags.get("waterway") == "riverbank"
        # residential 大院面：full 档提取，后续统一按建筑覆盖率判定占位
        is_residential = tags.get("landuse") == "residential" and not self.lite
        # 公园/绿地面：full/lite 都提取（面感知边缘到达依赖该层）
        is_green = (
            tags.get("leisure") in GREEN_LEISURE
            or tags.get("natural") in GREEN_NATURAL
            or tags.get("landuse") == "forest"
        )
        if not is_building and not is_water and not is_residential and not is_green:
            return
        try:
            wkb = self.wkbfab.create_multipolygon(a)
        except (osmium.InvalidLocationError, RuntimeError):
            # 坏环/自相交等无效 area 跳过
            return
        geom = shapely.wkb.loads(wkb, hex=True)
        if geom.is_empty or not geom.intersects(self.filter):
            return
        if is_building:
            props = {k: tags[k] for k in BUILDING_PROPS if k in tags}
            props["source"] = "osm"
            self.buildings.append((geom, props))
        elif is_water:
            if self.lite:
                geom = geom.simplify(_LITE_WATER_TOLERANCE, preserve_topology=True)
                if geom.is_empty or geom.area < _LITE_WATER_TOLERANCE**2:
                    return
            self.water.append(
                (
                    geom,
                    {
                        "name": tags.get("name", ""),
                        "natural": tags.get("natural", ""),
                        "waterway": tags.get("waterway", ""),
                        "water": tags.get("water", ""),
                        "source": "osm",
                    },
                )
            )
        elif is_green:
            if self.lite:
                geom = geom.simplify(_LITE_WATER_TOLERANCE, preserve_topology=True)
                if geom.is_empty or geom.area < _LITE_WATER_TOLERANCE**2:
                    return
            self.green.append(
                (
                    geom,
                    {
                        "name": tags.get("name", ""),
                        "leisure": tags.get("leisure", ""),
                        "natural": tags.get("natural", ""),
                        "landuse": tags.get("landuse", ""),
                        "source": "osm",
                    },
                )
            )
        else:
            self.residential.append(
                (
                    geom,
                    {
                        "name": tags.get("name", ""),
                        "residential": "yes",
                        "source": "osm",
                    },
                )
            )


def _mark_residential_placeholders(col: FabricCollector) -> None:
    """按建筑覆盖率筛出「OSM 未细化小区」：面内建筑 footprint 占比 < 10%。

    只保留占位面（覆盖率达标的大院无展示价值），原地改写 col.residential。
    """
    import shapely
    from shapely import STRtree

    if not col.residential:
        return
    kept: list[tuple[Polygon | MultiPolygon, dict]] = []
    if col.buildings:
        bgeoms = [g for g, _ in col.buildings]
        tree = STRtree(bgeoms)
    for geom, props in col.residential:
        area_km2 = geom.area * _DEG2_TO_KM2
        if area_km2 < RESIDENTIAL_MIN_KM2:
            continue
        if col.buildings:
            covered = 0.0
            for idx in tree.query(geom, predicate="intersects"):
                inter = bgeoms[int(idx)].intersection(geom)
                if not inter.is_empty:
                    covered += inter.area
            if covered / geom.area >= RESIDENTIAL_COVER_MIN:
                continue
        props["placeholder"] = True
        kept.append((geom, props))
    col.residential = kept


def _write_fc(path: Path, items: list[tuple], layer: str, ndigits: int = 7) -> None:
    features = []
    for geom, props in items:
        geom = _round_coords(geom, ndigits)
        features.append(
            {
                "type": "Feature",
                "properties": props,
                "geometry": json.loads(shapely.to_geojson(geom)),
            }
        )
    fc = {"type": "FeatureCollection", "features": features}
    path.write_text(json.dumps(fc, ensure_ascii=False), encoding="utf-8")
    print(f"  {layer}: {len(features)} features -> {path}")


def main() -> int:
    ap = argparse.ArgumentParser(description="从 OSM PBF 裁出 region 4 层真实底图")
    ap.add_argument("--pbf", required=True, help="Geofabrik .osm.pbf 路径")
    ap.add_argument("--region", required=True, help="输出到 data/map_fabric/<region>")
    ap.add_argument("--bbox", help="W,S,E,N（WGS84 经度,纬度）；与 --ring6 二选一")
    ap.add_argument(
        "--ring6", action="store_true",
        help="用内置北京六环以内粗边界（近似）代替 --bbox 裁剪",
    )
    ap.add_argument(
        "--lite", action="store_true",
        help="精简档：跳过 buildings、roads/water 几何简化、坐标 6 位小数",
    )
    args = ap.parse_args()

    if bool(args.bbox) == args.ring6:
        ap.error("--bbox 与 --ring6 必须且只能提供一个")

    pbf = Path(args.pbf)
    if not pbf.is_file():
        print(f"[import] PBF 不存在: {pbf}")
        return 1
    if args.ring6:
        filter_geom = RING6_POLYGON
    else:
        w, s, e, n = (float(v) for v in args.bbox.split(","))
        filter_geom = box(w, s, e, n)
    out_dir = REPO_ROOT / "data" / "map_fabric" / args.region
    out_dir.mkdir(parents=True, exist_ok=True)

    # 单次调用：SimpleHandler 检测到 area() 回调后自动两遍扫描（线+面一次完成）
    col = FabricCollector(filter_geom, lite=args.lite)
    col.apply_file(str(pbf), locations=True, idx="flex_mem")
    _mark_residential_placeholders(col)

    ndigits = _LITE_NDIGITS if args.lite else 7
    _write_fc(out_dir / "roads.geojson", col.roads, "roads", ndigits)
    _write_fc(out_dir / "buildings.geojson", col.buildings, "buildings", ndigits)
    _write_fc(out_dir / "residential.geojson", col.residential, "residential", ndigits)
    _write_fc(out_dir / "water.geojson", col.water, "water", ndigits)
    _write_fc(out_dir / "green.geojson", col.green, "green", ndigits)
    _write_fc(out_dir / "barriers.geojson", col.barriers, "barriers", ndigits)

    road_km = sum(g.length for g, _ in col.roads) * 111.32
    # 度²面积 × (111.32 km/度)² × cos(lat)≈0.59 → km²（直接就是 km²，勿再除 1e6）
    bldg_km2 = sum(g.area for g, _ in col.buildings) * 111.32**2 * 0.59
    water_km2 = sum(g.area for g, _ in col.water) * 111.32**2 * 0.59
    green_km2 = sum(g.area for g, _ in col.green) * 111.32**2 * 0.59
    mode = "lite" if args.lite else "full"
    print(
        f"[import] mode={mode}  roads≈{road_km:.1f}km  buildings≈{bldg_km2:.2f}km²  "
        f"water≈{water_km2:.3f}km²  green≈{green_km2:.2f}km²({len(col.green)}面)  "
        f"barriers={len(col.barriers)}条"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
