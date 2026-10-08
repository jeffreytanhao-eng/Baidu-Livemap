"""硬单测：河 + 桥 + 河边住宅(blocked) + 河边商场(enterable)。

等时圈必须：过桥到达对岸、进入商场轮廓、不穿住宅墙厚区、不淌河。
"""

from __future__ import annotations

import math

from geo.graph import KIND_BRIDGE
from geo.isochrone import build_isochrone, cutoff_dijkstra, fast_mask
from shapely.geometry import LineString, Point


def _start_node(graph) -> int:
    proj = graph.proj
    lng, lat = proj.to_lnglat(0.0, 100.0)  # 北岸路上一点
    node = graph.snap(lng, lat)
    assert node is not None, "中心应能吸附到可走路节点"
    return node


def test_bridge_is_only_river_crossing(river_graph, river_poly) -> None:
    """图上跨河的边只能是 bridge 边。"""
    crossings = 0
    for e in river_graph.edges:
        seg = LineString(e.shape)
        if seg.intersects(river_poly):
            assert e.kind == KIND_BRIDGE, "非桥边不得与河面相交"
            crossings += 1
    assert crossings >= 1, "至少存在一条桥边跨河"


def test_reach_south_bank_via_bridge(river_graph) -> None:
    start = _start_node(river_graph)
    costs = cutoff_dijkstra(river_graph, start, 15 * 60)
    # 南岸点 (0, -100) 附近的节点可达
    south_reachable = [
        nid
        for nid, (_x, y) in enumerate(river_graph.nodes)
        if y < -35 and not math.isinf(costs[nid])
    ]
    assert south_reachable, "必须能过桥到达南岸"
    # 桥边两端都可达
    bridge_edges = [e for e in river_graph.edges if e.kind == KIND_BRIDGE]
    assert bridge_edges, "应存在桥边"
    assert any(not math.isinf(costs[e.u]) and not math.isinf(costs[e.v]) for e in bridge_edges)


def test_isochrone_constraints(river_graph, river_walls, river_water, river_poly, residential_poly, mall_poly) -> None:
    start = _start_node(river_graph)
    costs = cutoff_dijkstra(river_graph, start, 15 * 60)
    result = build_isochrone(river_graph, costs, 15, river_walls, river_water)

    assert result.area_m2 > 0.0
    assert result.geometry is not None and result.main_geometry is not None

    # 不淌河：结果与河面无交叠
    assert result.geometry.intersection(river_poly).area < 1e-6
    # 不穿住宅：结果与住宅墙厚区（外扩 1.5m）及住宅本体均无交叠
    wall = river_walls[0]
    assert wall is not None
    assert result.geometry.intersection(wall).area < 1e-6
    assert result.geometry.intersection(residential_poly).area < 1e-6

    # 进入商场：商场轮廓大部被覆盖（廊道 + 临近道路缓冲）
    assert result.geometry.intersection(mall_poly).area > 0.5 * mall_poly.area

    # 过桥到达对岸：南岸点被覆盖；且河把斑切成主块 + 飞地
    south_pt = Point(0.0, -100.0)
    assert result.geometry.covers(south_pt), "南岸应被斑覆盖"
    assert result.enclaves, "过河部分应作为飞地单独返回"
    assert result.main_geometry.covers(Point(0.0, 100.0)), "主块应含中心侧"


def test_fast_mask_constraints(river_graph, river_walls, river_water, river_poly) -> None:
    start = _start_node(river_graph)
    costs = cutoff_dijkstra(river_graph, start, 15 * 60)
    result = fast_mask(river_graph, costs, 15, river_walls, river_water)
    assert result.area_m2 > 0.0
    assert result.geometry is not None
    # 快拍可以粗，但仍不得穿河穿楼
    assert result.geometry.intersection(river_poly).area < 1e-6
    assert result.geometry.intersection(river_walls[0]).area < 1e-6


def test_unreachable_when_snap_too_far(river_graph) -> None:
    proj = river_graph.proj
    lng, lat = proj.to_lnglat(500.0, 500.0)  # 远离任何路
    assert river_graph.snap(lng, lat) is None
