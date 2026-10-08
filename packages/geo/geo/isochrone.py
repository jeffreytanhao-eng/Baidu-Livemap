"""等时圈：一次带截止 Dijkstra 输出全部时间分层 + 约束斑构建。

禁止「固定半径采样点连成多边形（锯齿圆）」当主结果——这里的斑由
图上可达边的几何缓冲融合而成，并 difference 让开 blocked 墙厚区与水域。
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field

from shapely.geometry import LineString, Point
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from .graph import WalkGraph

ISOCHRONE_BUFFER_M = 30.0


def cutoff_dijkstra(graph: WalkGraph, start_node: int, cutoff_seconds: float) -> list[float]:
    """纯 Python heapq 带截止 Dijkstra，一次搜索返回所有节点最短耗时（秒）。

    不可达或超过截止的节点为 math.inf。
    """
    n = graph.node_count
    costs = [math.inf] * n
    adj = graph.adj
    costs[start_node] = 0.0
    heap: list[tuple[float, int]] = [(0.0, start_node)]
    push = heapq.heappush
    pop = heapq.heappop
    while heap:
        cost, node = pop(heap)
        if cost > costs[node]:
            continue
        if cost > cutoff_seconds:
            continue
        for other, weight, _eid in adj[node]:
            new_cost = cost + weight
            if new_cost < costs[other] and new_cost <= cutoff_seconds:
                costs[other] = new_cost
                push(heap, (new_cost, other))
    return costs


def layers_from_one_run(costs: list[float], layer_minutes: list[int]) -> dict[int, dict[int, float]]:
    """从一次 Dijkstra 结果切出多个时间层（禁止每层各跑一次）。

    返回 ``{minutes: {node_id: cost_s}}``，按 minutes 升序。
    """
    layers: dict[int, dict[int, float]] = {m: {} for m in sorted(layer_minutes)}
    ordered = sorted(layer_minutes)
    for nid, cost in enumerate(costs):
        if math.isinf(cost):
            continue
        for m in ordered:
            if cost <= m * 60.0:
                layers[m][nid] = cost
    return layers


@dataclass(slots=True)
class IsochroneResult:
    """等时斑结果。``geometry``/``main_geometry`` 为本地米坐标，供扇区等内部分析。"""

    minutes: int
    polygon: dict  # 主块 GeoJSON geometry（WGS84）
    area_m2: float  # 主块 + 飞地总面积
    enclaves: list[dict] = field(default_factory=list)  # 飞地 GeoJSON（如过桥到达的对岸）
    geometry: BaseGeometry | None = None
    main_geometry: BaseGeometry | None = None


def _reachable_edges(graph: WalkGraph, costs: list[float], cutoff_s: float) -> list[LineString]:
    lines: list[LineString] = []
    for e in graph.edges:
        if min(costs[e.u], costs[e.v]) <= cutoff_s and len(e.shape) >= 2:
            lines.append(LineString(e.shape))
    return lines


def _split_main_and_enclaves(
    geom: BaseGeometry, start_xy: tuple[float, float]
) -> tuple[BaseGeometry | None, list[BaseGeometry]]:
    """保留含起点的主连通块，其它块作为飞地。"""
    if geom.is_empty:
        return None, []
    polys: list[BaseGeometry] = []
    if geom.geom_type == "Polygon":
        polys = [geom]
    elif geom.geom_type == "MultiPolygon":
        polys = list(geom.geoms)
    elif geom.geom_type == "GeometryCollection":
        polys = [g for g in geom.geoms if g.geom_type in ("Polygon", "MultiPolygon")]
        polys = [p for g in polys for p in (list(g.geoms) if g.geom_type == "MultiPolygon" else [g])]
    if not polys:
        return None, []
    start = Point(*start_xy)
    main: BaseGeometry | None = None
    enclaves: list[BaseGeometry] = []
    for p in polys:
        if main is None and p.covers(start):
            main = p
        else:
            enclaves.append(p)
    if main is None:
        # 起点被 difference 切掉时退化为面积最大块
        main = max(polys, key=lambda g: g.area)
        enclaves = [p for p in polys if p is not main]
    return main, enclaves


def _to_geojson_geometry(graph: WalkGraph, geom: BaseGeometry) -> dict:
    from shapely.geometry import mapping
    from shapely.ops import transform as shp_transform

    proj = graph.proj
    wgs = shp_transform(lambda x, y: proj.to_lnglat(x, y), geom)
    return mapping(wgs)


def _start_xy(graph: WalkGraph, costs: list[float]) -> tuple[float, float]:
    best = 0
    best_cost = math.inf
    for nid, cost in enumerate(costs):
        if cost < best_cost:
            best_cost = cost
            best = nid
    return graph.nodes[best]


def _obstacle_union(walls: list[BaseGeometry], water: list[BaseGeometry]) -> BaseGeometry | None:
    obstacles = [g for g in (*walls, *water) if g is not None and not g.is_empty]
    if not obstacles:
        return None
    if len(obstacles) == 1:
        # 调用方传入的启动期预融合并集：直接复用，避免对单几何重复 dissolve
        return obstacles[0]
    return unary_union(obstacles)


def build_isochrone(
    graph: WalkGraph,
    costs: list[float],
    minutes: int,
    walls: list[BaseGeometry],
    water: list[BaseGeometry],
) -> IsochroneResult:
    """精拍：可达边 30m 缓冲融合成斑，difference 让开 blocked 墙厚区与水域。"""
    cutoff_s = minutes * 60.0
    lines = _reachable_edges(graph, costs, cutoff_s)
    start_xy = _start_xy(graph, costs)
    if not lines:
        return IsochroneResult(minutes=minutes, polygon={}, area_m2=0.0)

    blob = unary_union([ln.buffer(ISOCHRONE_BUFFER_M, quad_segs=8) for ln in lines])
    obstacles = _obstacle_union(walls, water)
    if obstacles is not None:
        blob = blob.difference(obstacles)

    main, enclaves = _split_main_and_enclaves(blob, start_xy)
    if main is None:
        return IsochroneResult(minutes=minutes, polygon={}, area_m2=0.0)
    total_area = main.area + sum(e.area for e in enclaves)
    return IsochroneResult(
        minutes=minutes,
        polygon=_to_geojson_geometry(graph, main),
        area_m2=total_area,
        enclaves=[_to_geojson_geometry(graph, e) for e in enclaves],
        geometry=blob,
        main_geometry=main,
    )


def fast_mask(
    graph: WalkGraph,
    costs: list[float],
    minutes: int,
    walls: list[BaseGeometry],
    water: list[BaseGeometry],
) -> IsochroneResult:
    """快拍：可达边的凸包化包络（可粗），仍 difference 水域与 blocked 区。"""
    cutoff_s = minutes * 60.0
    lines = _reachable_edges(graph, costs, cutoff_s)
    start_xy = _start_xy(graph, costs)
    if not lines:
        return IsochroneResult(minutes=minutes, polygon={}, area_m2=0.0)

    hull = unary_union(lines).convex_hull
    if hull.is_empty:
        return IsochroneResult(minutes=minutes, polygon={}, area_m2=0.0)
    obstacles = _obstacle_union(walls, water)
    if obstacles is not None:
        hull = hull.difference(obstacles)

    main, enclaves = _split_main_and_enclaves(hull, start_xy)
    if main is None:
        return IsochroneResult(minutes=minutes, polygon={}, area_m2=0.0)
    total_area = main.area + sum(e.area for e in enclaves)
    return IsochroneResult(
        minutes=minutes,
        polygon=_to_geojson_geometry(graph, main),
        area_m2=total_area,
        enclaves=[_to_geojson_geometry(graph, e) for e in enclaves],
        geometry=hull,
        main_geometry=main,
    )
