"""POI 确定性合成生成器（无 AK 降级用）。

以「中心 + 种子」生成 7 类生活圈 POI：
- 位置优先落在图边（道路）附近 ±15m，保证图上可吸附；
- 便利生活 60% 概率放在 enterable 建筑（商场）附近；
- transport 特殊：首 0-1 个为地铁站（transportKind=metro），其余为公交站；
- 同中心同种子输出完全一致（快照可复现）。

类别常量与清洗规则集中在 app.coverage。
"""

from __future__ import annotations

import math
import random
from typing import Any

from geo import cutoff_dijkstra
from geo.graph import WalkGraph
from shapely.geometry import LineString
from shapely.geometry.base import BaseGeometry

from .coverage import CATEGORIES, CATEGORY_CONVENIENCE

_NAME_POOLS: dict[str, list[str]] = {
    "convenience": ["便民菜场", "绿源生鲜超市", "邻里菜市", "优品生活超市", "玲玲理发", "洁丰洗衣", "快客便利店"],
    "leisure": ["横店电影城", "大剧院", "社区体育中心", "康体游泳馆", "新城展览馆", "金逸影城", "乐刻健身", "舒活洗浴"],
    "mall": ["万达广场", "京客隆超市", "物美超市", "山姆会员商店", "永辉超市", "华联商场"],
    "education": ["春蕾幼儿园", "实验小学", "阳光中学", "育才学校", "小太阳幼儿园", "城市学院"],
    "health": ["同仁堂药店", "社区卫生服务中心", "仁心诊所", "康泰药店", "幸福养老院", "夕阳红养老照料中心"],
    "public": ["社区公园", "街心花园", "公共绿地", "市民广场", "街道图书馆"],
    "transport": ["地铁站", "公交车站", "公交站", "公交枢纽"],
}

_COUNT_RANGE: dict[str, tuple[int, int]] = {
    "convenience": (4, 7),
    "leisure": (2, 4),
    "mall": (1, 3),
    "education": (2, 4),
    "health": (3, 5),
    "public": (2, 4),
    "transport": (4, 7),
}


def _edge_point(rng: random.Random, line: LineString) -> tuple[float, float]:
    t = rng.random()
    pt = line.interpolate(t, normalized=True)
    return pt.x + rng.uniform(-15.0, 15.0), pt.y + rng.uniform(-15.0, 15.0)


def synth_pois(
    center_wgs: tuple[float, float],
    *,
    seed: str,
    graph: WalkGraph,
    enterable: list[BaseGeometry] | None = None,
    density: float = 1.0,
    radius_m: float = 1500.0,
    walk_budget_s: float | None = None,
) -> list[dict[str, Any]]:
    """确定性合成 7 类 POI（WGS84 输出）。

    ``seed`` 通常取 geohash5(center)，同中心同 radius 输出完全一致。
    transport 前 0-1 个为地铁站（transportKind=metro），其余为公交站。
    ``walk_budget_s`` 非空时：候选点必须路网可达且步行成本 ≤ 该值
    （等时圈内）——圈内选取口径下合成 POI 不再落在圈外虚占召回。
    """
    rng = random.Random(f"poi|{seed}")
    proj = graph.proj
    cx, cy = proj.to_xy(*center_wgs)
    lines = [LineString(e.shape) for e in graph.edges if len(e.shape) >= 2]
    if not lines:
        return []
    costs: list[float] | None = None
    if walk_budget_s is not None:
        node = graph.snap(*center_wgs)
        if node is None:
            return []
        costs = cutoff_dijkstra(graph, node, walk_budget_s)

    pois: list[dict[str, Any]] = []
    for cat in CATEGORIES:
        pool = _NAME_POOLS[cat][:]
        rng.shuffle(pool)
        lo, hi = _COUNT_RANGE[cat]
        count = max(1, round(rng.uniform(lo, hi) * density))
        if cat == "transport":
            # 地铁 0-1 站（70% 概率有），其余公交：演示一票制的「公交兜底」口径
            metro_n = 1 if rng.random() < 0.7 else 0
        else:
            metro_n = 0
        made = 0
        attempts = 0
        while made < count and attempts < count * 30:
            attempts += 1
            if cat == CATEGORY_CONVENIENCE and enterable and rng.random() < 0.6:
                geom = rng.choice(enterable)
                c = geom.centroid
                px = c.x + rng.uniform(-40.0, 40.0)
                py = c.y + rng.uniform(-40.0, 40.0)
            else:
                px, py = _edge_point(rng, rng.choice(lines))
            if math.hypot(px - cx, py - cy) > radius_m:
                continue
            if costs is not None:
                # 等时圈过滤：吸不上路网或超出步行预算的候选点直接放弃
                lng_xy, lat_xy = proj.to_lnglat(px, py)
                nid = graph.snap(lng_xy, lat_xy)
                if nid is None or math.isinf(costs[nid]):
                    continue
            name = pool[made % len(pool)]
            if made >= len(pool):
                name = f"{name}{made // len(pool) + 1}号"
            lng, lat = proj.to_lnglat(px, py)
            uid = f"syn-{cat}-{round(px)}x{round(py)}"
            poi: dict[str, Any] = {
                "id": uid,
                "uid": uid,
                "name": name,
                "category": cat,
                "lng": lng,
                "lat": lat,
            }
            if cat == "transport":
                poi["transportKind"] = "metro" if made < metro_n else "bus"
                poi["name"] = "地铁站" if made < metro_n else ("公交车站" if made % 2 else "公交站")
            pois.append(poi)
            made += 1
    return pois
