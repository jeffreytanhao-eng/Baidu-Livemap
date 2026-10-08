#!/usr/bin/env python3
"""build_map_fabric.py — 生成 map fabric 数据包（离线确定性合成底图）。

输出 ``data/map_fabric/<region>/`` 下 4 个 GeoJSON（WGS84 坐标）：
roads / buildings / water / barriers。

内置 region 预设（``PRESETS``）：
- ``jinsong``       劲松街道（中心约 39.8846, 116.4578）：200m 方格网 + 通惠河（2 桥）
                    + 东四环 motorway（1 座人行天桥），商场/底商办公/公园各 1；
- ``zhongguancun``  中关村（密，中心约 39.983, 116.316）：150m 密网 + 万泉河（2 桥）
                    + 北四环 motorway（2 座人行天桥），2 商场 + 底商办公；
- ``nanyuan``       南苑（稀，中心约 39.812, 116.386）：300m 疏网 + 小龙河（1 桥）
                    + 南中轴路 motorway（1 座人行天桥），1 商场。

每个 region 均为确定性伪随机（预设 seed），重跑输出不变。

``--from-osm`` 尝试用 osmnx 拉真实 OSM 数据（可选路径，拉不到就提示并用合成）。
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

EARTH_RADIUS_M = 6371008.8
M_PER_DEG_LAT = math.pi / 180.0 * EARTH_RADIUS_M


@dataclass(frozen=True)
class RiverSpec:
    """东西向河流：y0 南岸、y1 北岸，bridge_xs 为跨河桥所在的南北路 x。"""

    y0: float
    y1: float
    bridge_xs: tuple[float, ...]
    name: str


@dataclass(frozen=True)
class MotorwaySpec:
    """硬阻隔快速路：axis='x' 为南北向（x=pos），axis='y' 为东西向（y=pos）。"""

    axis: str
    pos: float
    name: str


@dataclass(frozen=True)
class RegionPreset:
    name: str
    center_lng: float
    center_lat: float
    grid_min: int = -1400
    grid_max: int = 1400
    grid_step: int = 200
    river: RiverSpec | None = None
    secondary_x: float | None = None
    secondary_name: str = ""
    primary_y: float | None = None
    primary_name: str = ""
    motorway: MotorwaySpec | None = None
    # 人行天桥：((x0, y0), (x1, y1), 名称)，bridge=yes footway，跨 motorway
    footbridges: tuple[tuple[tuple[float, float], tuple[float, float], str], ...] = ()
    # 额外线段：(坐标序列, properties)，如商场入口步道、公园步道
    extra_roads: tuple[tuple[tuple[tuple[float, float], ...], dict[str, Any]], ...] = ()
    # (x0, y0, x1, y1, 名称, 关联 POI 名)
    malls: tuple[tuple[float, float, float, float, str, tuple[str, ...]], ...] = ()
    podiums: tuple[tuple[float, float, float, float, str, tuple[str, ...]], ...] = ()
    # (x0, y0, x1, y1, 名称)：无底商办公（blocked）
    offices: tuple[tuple[float, float, float, float, str], ...] = ()
    parks: tuple[tuple[float, float, float, float, str], ...] = ()
    building_prob: float = 0.45
    seed: int = 42
    name_prefix: str = "小区"
    residential_names: dict[str, str] = field(default_factory=dict)


def jinsong_preset() -> RegionPreset:
    return RegionPreset(
        name="jinsong",
        center_lng=116.4578,
        center_lat=39.8846,
        river=RiverSpec(y0=-730.0, y1=-670.0, bridge_xs=(-200.0, 400.0), name="通惠河"),
        secondary_x=400.0,
        secondary_name="劲松中街",
        primary_y=200.0,
        primary_name="劲松路",
        motorway=MotorwaySpec(axis="x", pos=1100.0, name="东四环路"),
        footbridges=(((1000.0, 500.0), (1200.0, 500.0), "劲松东口人行天桥"),),
        extra_roads=(
            (((200.0, 285.0), (215.0, 285.0)), {"highway": "footway"}),
            (((320.0, 200.0), (320.0, 240.0), (275.0, 240.0)), {"highway": "footway"}),
            (((-600.0, 670.0), (-585.0, 670.0)), {"highway": "footway"}),
            (((-1000.0, -400.0), (-800.0, -400.0)), {"highway": "footway"}),
            (((-900.0, -340.0), (-900.0, -460.0)), {"highway": "footway"}),
            # 通惠河人行地道（tunnel=yes footway，连接 x=-600 南北向路在河两岸的断点）
            (
                ((-600.0, -665.0), (-600.0, -735.0)),
                {"highway": "footway", "tunnel": "yes", "name": "通惠河人行地道"},
            ),
        ),
        malls=((215.0, 240.0, 335.0, 330.0, "劲松购物中心", ("劲松购物中心", "物美超市")),),
        podiums=((-585.0, 640.0, -505.0, 700.0, "劲松大厦", ("瑞幸咖啡", "便利蜂")),),
        offices=((640.0, -260.0, 720.0, -180.0, "农光里办公楼"),),
        parks=((-950.0, -460.0, -800.0, -340.0, "劲松口袋公园"),),
        building_prob=0.45,
        seed=42,
        name_prefix="劲松",
    )


def zhongguancun_preset() -> RegionPreset:
    """中关村（密）：150m 密路网、建筑概率高、2 商场，北四环 2 座天桥。"""
    return RegionPreset(
        name="zhongguancun",
        center_lng=116.316,
        center_lat=39.983,
        grid_min=-1500,
        grid_max=1500,
        grid_step=150,
        river=RiverSpec(y0=950.0, y1=1010.0, bridge_xs=(-300.0, 150.0), name="万泉河"),
        secondary_x=0.0,
        secondary_name="中关村大街",
        primary_y=0.0,
        primary_name="中关村南路",
        motorway=MotorwaySpec(axis="y", pos=-1100.0, name="北四环路"),
        footbridges=(
            ((-150.0, -1200.0), (-150.0, -1000.0), "中关村南人行天桥"),
            ((450.0, -1200.0), (450.0, -1000.0), "中关村东人行天桥"),
        ),
        extra_roads=(
            (((0.0, 105.0), (60.0, 105.0)), {"highway": "footway"}),
            (((120.0, 0.0), (120.0, 60.0)), {"highway": "footway"}),
            (((-600.0, 285.0), (-630.0, 285.0)), {"highway": "footway"}),
            (((-690.0, 150.0), (-690.0, 240.0)), {"highway": "footway"}),
            (((340.0, -300.0), (340.0, -390.0)), {"highway": "footway"}),
            (((-1200.0, 525.0), (-1050.0, 525.0)), {"highway": "footway"}),
            (((-975.0, 600.0), (-975.0, 450.0)), {"highway": "footway"}),
        ),
        malls=(
            (60.0, 60.0, 180.0, 150.0, "中关村购物中心", ("中关村购物中心", "家乐福")),
            (-750.0, 240.0, -630.0, 330.0, "新中关购物中心", ("新中关购物中心", "物美超市")),
        ),
        podiums=((300.0, -450.0, 380.0, -390.0, "中关村大厦", ("瑞幸咖啡", "便利蜂")),),
        offices=((-750.0, -150.0, -660.0, -60.0, "中钢大厦"),),
        parks=((-1050.0, 450.0, -900.0, 600.0, "中关村口袋公园"),),
        building_prob=0.58,
        seed=7,
        name_prefix="中关村",
    )


def nanyuan_preset() -> RegionPreset:
    """南苑（稀）：300m 疏路网、建筑稀疏、1 商场，小龙河仅 1 桥。"""
    return RegionPreset(
        name="nanyuan",
        center_lng=116.386,
        center_lat=39.812,
        grid_min=-1500,
        grid_max=1500,
        grid_step=300,
        river=RiverSpec(y0=700.0, y1=760.0, bridge_xs=(300.0,), name="小龙河"),
        secondary_x=0.0,
        secondary_name="南苑中路",
        primary_y=0.0,
        primary_name="南苑大街",
        motorway=MotorwaySpec(axis="x", pos=1350.0, name="南中轴路"),
        footbridges=(((1050.0, -300.0), (1500.0, -300.0), "南苑东人行天桥"),),
        extra_roads=(
            (((-300.0, -90.0), (-260.0, -90.0)), {"highway": "footway"}),
            (((-1050.0, -540.0), (-900.0, -540.0)), {"highway": "footway"}),
        ),
        malls=((-260.0, -140.0, -140.0, -40.0, "南苑购物中心", ("南苑购物中心", "首航超市")),),
        podiums=(),
        offices=((600.0, 300.0, 680.0, 360.0, "南苑办公楼"),),
        parks=((-900.0, -600.0, -750.0, -480.0, "南苑口袋公园"),),
        building_prob=0.28,
        seed=11,
        name_prefix="南苑",
    )


PRESETS: dict[str, RegionPreset] = {
    p.name: p for p in (jinsong_preset(), zhongguancun_preset(), nanyuan_preset())
}


class FabricBuilder:
    """按 region 预设生成 4 层 GeoJSON（WGS84）。"""

    def __init__(self, preset: RegionPreset) -> None:
        self.p = preset
        self.m_per_deg_lng = M_PER_DEG_LAT * math.cos(math.radians(preset.center_lat))

    def to_lnglat(self, x: float, y: float) -> list[float]:
        return [self.p.center_lng + x / self.m_per_deg_lng, self.p.center_lat + y / M_PER_DEG_LAT]

    def line_feature(self, coords_xy: list[tuple[float, float]], props: dict[str, Any]) -> dict:
        return {
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": [self.to_lnglat(x, y) for x, y in coords_xy],
            },
            "properties": props,
        }

    def rect_feature(self, x0: float, y0: float, x1: float, y1: float, props: dict[str, Any]) -> dict:
        ring = [
            self.to_lnglat(x0, y0),
            self.to_lnglat(x1, y0),
            self.to_lnglat(x1, y1),
            self.to_lnglat(x0, y1),
            self.to_lnglat(x0, y0),
        ]
        return {
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [ring]},
            "properties": props,
        }

    def build_roads(self) -> list[dict]:
        p = self.p
        roads: list[dict] = []
        river = p.river
        gap_north = river.y1 + 5.0 if river else 0.0  # 路北端点（桥北头）
        gap_south = river.y0 - 5.0 if river else 0.0  # 路南端点（桥南头）

        # 南北向道路
        for x in range(p.grid_min, p.grid_max + 1, p.grid_step):
            xf = float(x)
            if p.secondary_x is not None and xf == p.secondary_x:
                props: dict[str, Any] = {"highway": "secondary", "name": p.secondary_name}
            else:
                props = {"highway": "residential"}
            if river is None:
                roads.append(
                    self.line_feature([(xf, float(p.grid_min)), (xf, float(p.grid_max))], props)
                )
            elif xf in river.bridge_xs:
                roads.append(
                    self.line_feature([(xf, gap_north), (xf, float(p.grid_max))], dict(props))
                )
                roads.append(
                    self.line_feature(
                        [(xf, gap_south), (xf, gap_north)],
                        {**props, "bridge": "yes", "name": props.get("name", "") or "跨河桥"},
                    )
                )
                roads.append(
                    self.line_feature([(xf, float(p.grid_min)), (xf, gap_south)], dict(props))
                )
            else:
                # 非桥道路在河前断开（北侧到河北岸，南侧到河南岸）
                roads.append(
                    self.line_feature([(xf, gap_north), (xf, float(p.grid_max))], dict(props))
                )
                roads.append(
                    self.line_feature([(xf, float(p.grid_min)), (xf, gap_south)], dict(props))
                )

        # 东西向道路
        for y in range(p.grid_min, p.grid_max + 1, p.grid_step):
            yf = float(y)
            if river is not None and river.y0 <= yf <= river.y1:
                continue  # 河带内不布路
            if p.primary_y is not None and yf == p.primary_y:
                props = {"highway": "primary", "sidewalk": "yes", "name": p.primary_name}
            else:
                props = {"highway": "residential"}
            roads.append(
                self.line_feature(
                    [(float(p.grid_min), yf), (float(p.grid_max), yf)], props
                )
            )

        # 人行天桥：跨越 motorway（bridge=yes footway）
        for a, b, name in p.footbridges:
            roads.append(
                self.line_feature([a, b], {"highway": "footway", "bridge": "yes", "name": name})
            )
        # 额外线段（商场入口步道、底商前步道、公园内步道等）
        for coords, props in p.extra_roads:
            roads.append(self.line_feature(list(coords), dict(props)))
        return roads

    def build_buildings(self) -> list[dict]:
        p = self.p
        buildings: list[dict] = []

        specials: list[tuple[float, float, float, float]] = []
        for x0, y0, x1, y1, name, pois in p.malls:
            specials.append((x0, y0, x1, y1))
            buildings.append(
                self.rect_feature(
                    x0,
                    y0,
                    x1,
                    y1,
                    {"building": "retail", "shop": "mall", "name": name, "pois": list(pois)},
                )
            )
        for x0, y0, x1, y1, name, pois in p.podiums:
            specials.append((x0, y0, x1, y1))
            buildings.append(
                self.rect_feature(
                    x0, y0, x1, y1, {"building": "office", "name": name, "podium_pois": list(pois)}
                )
            )
        for x0, y0, x1, y1, name in p.offices:
            specials.append((x0, y0, x1, y1))
            buildings.append(
                self.rect_feature(x0, y0, x1, y1, {"building": "office", "name": name})
            )
        for x0, y0, x1, y1, name in p.parks:
            specials.append((x0, y0, x1, y1))
            buildings.append(
                self.rect_feature(x0, y0, x1, y1, {"leisure": "park", "name": name})
            )

        # 住宅楼（blocked，确定性伪随机布满网格内部）
        rng = random.Random(p.seed)
        river = p.river
        step = p.grid_step
        # 人行天桥落点格子跳过
        landing_cells: set[tuple[int, int]] = set()
        for a, b, _name in p.footbridges:
            for px, py in (a, b):
                gx = math.floor((px - p.grid_min) / step) * step + p.grid_min
                gy = math.floor((py - p.grid_min) / step) * step + p.grid_min
                landing_cells.add((gx, gy))
        count = 0
        for gx in range(p.grid_min, p.grid_max, step):
            for gy in range(p.grid_min, p.grid_max, step):
                # 河带内不布楼
                if river is not None and gy < p.grid_max and not (
                    gy + step <= river.y0 or gy >= river.y1
                ):
                    continue
                if (gx, gy) in landing_cells:
                    continue  # 天桥落点
                cx = gx + step / 2.0
                cy = gy + step / 2.0
                if rng.random() > p.building_prob:
                    continue
                w = rng.uniform(50.0, 80.0)
                h = rng.uniform(35.0, 60.0)
                x0, y0 = cx - w / 2.0, cy - h / 2.0
                x1, y1 = cx + w / 2.0, cy + h / 2.0
                overlap = False
                for sx0, sy0, sx1, sy1 in specials:
                    if x0 < sx1 and x1 > sx0 and y0 < sy1 and y1 > sy0:
                        overlap = True
                        break
                if overlap:
                    continue
                count += 1
                buildings.append(
                    self.rect_feature(
                        x0,
                        y0,
                        x1,
                        y1,
                        {"building": "apartments", "name": f"{p.name_prefix}{count}区"},
                    )
                )
        return buildings

    def build_water(self) -> list[dict]:
        p = self.p
        if p.river is None:
            return []
        return [
            self.rect_feature(
                float(p.grid_min - 200),
                p.river.y0,
                float(p.grid_max + 200),
                p.river.y1,
                {"natural": "water", "water": "river", "name": p.river.name},
            )
        ]

    def build_barriers(self) -> list[dict]:
        p = self.p
        if p.motorway is None:
            return []
        lo, hi = float(p.grid_min - 200), float(p.grid_max + 200)
        if p.motorway.axis == "x":
            coords = [(p.motorway.pos, lo), (p.motorway.pos, hi)]
        else:
            coords = [(lo, p.motorway.pos), (hi, p.motorway.pos)]
        return [
            self.line_feature(coords, {"highway": "motorway", "name": p.motorway.name})
        ]


def feature_collection(features: list[dict]) -> dict:
    return {"type": "FeatureCollection", "features": features}


def write_layer(out_dir: Path, name: str, features: list[dict]) -> None:
    path = out_dir / f"{name}.geojson"
    with path.open("w", encoding="utf-8") as fh:
        json.dump(feature_collection(features), fh, ensure_ascii=False, indent=1)
    print(f"  {name}: {len(features)} features -> {path}")


def build_synthetic(preset: RegionPreset, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"[build_map_fabric] deterministic synthetic fabric ({preset.name}) -> {out_dir}")
    builder = FabricBuilder(preset)
    write_layer(out_dir, "roads", builder.build_roads())
    write_layer(out_dir, "buildings", builder.build_buildings())
    write_layer(out_dir, "water", builder.build_water())
    write_layer(out_dir, "barriers", builder.build_barriers())


def build_from_osm(preset: RegionPreset, out_dir: Path) -> bool:
    """可选路径：尝试 osmnx/overpass 拉真实数据；失败返回 False（调用方回退合成）。"""
    try:
        import osmnx as ox  # type: ignore[import-not-found]
    except ImportError:
        print("[build_map_fabric] osmnx 未安装，无法 --from-osm；请使用默认合成模式。")
        return False
    try:  # pragma: no cover - 需要网络，离线环境不覆盖
        center = (preset.center_lat, preset.center_lng)
        tags_road = {"highway": True}
        graph = ox.graph_from_point(center, dist=1500, network_type="walk", simplify=True)
        _ = graph
        buildings = ox.features_from_point(center, dist=1500, tags={"building": True})
        water = ox.features_from_point(center, dist=1500, tags={"natural": "water"})
        _ = (tags_road, buildings, water, out_dir)
        print("[build_map_fabric] --from-osm 成功（此处仅演示拉取，正式裁切管线见 Task 3.1）")
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"[build_map_fabric] --from-osm 拉取失败（{exc}）；回退到合成数据。")
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Build map fabric package")
    parser.add_argument(
        "--region",
        default="jinsong",
        choices=sorted(PRESETS),
        help="region name (default: jinsong)",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="output dir (default: <repo>/data/map_fabric/<region>)",
    )
    parser.add_argument(
        "--from-osm",
        action="store_true",
        help="try osmnx/overpass for real data; falls back to synthetic on failure",
    )
    args = parser.parse_args()

    preset = PRESETS[args.region]
    repo_root = Path(__file__).resolve().parents[1]
    out_dir = Path(args.out) if args.out else repo_root / "data" / "map_fabric" / preset.name

    if args.from_osm and build_from_osm(preset, out_dir):
        return 0
    build_synthetic(preset, out_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
