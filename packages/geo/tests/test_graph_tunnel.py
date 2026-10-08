"""硬单测：河 + 一条 tunnel=yes 的 footway 人行地道（无桥）。

圈必须经地道到达对岸，且非地道边不得穿河；穿 barrier 同理例外。
"""

from __future__ import annotations

import math

from geo.coords import LocalProjection
from geo.graph import KIND_TUNNEL, build_graph
from geo.isochrone import cutoff_dijkstra
from shapely.geometry import LineString, box

CENTER = (116.4578, 39.8846)
PROJ = LocalProjection(*CENTER)

RIVER = box(-600.0, -30.0, 600.0, 30.0)

# 北岸路 y=100、南岸路 y=-100、x=50 一条人行地道 footway 穿河（无桥）
ROADS: list[tuple[LineString, dict]] = [
    (LineString([(-200.0, 100.0), (200.0, 100.0)]), {"highway": "residential"}),
    (LineString([(-200.0, -100.0), (200.0, -100.0)]), {"highway": "residential"}),
    (
        LineString([(50.0, 100.0), (50.0, -100.0)]),
        {"highway": "footway", "tunnel": "yes", "name": "人行地道"},
    ),
]

WATER = [(RIVER, {"natural": "water", "name": "通惠河"})]


def _graph(barriers=None):
    return build_graph(ROADS, [], WATER, barriers or [], CENTER, 1500.0)


def test_tunnel_is_only_river_crossing() -> None:
    graph = _graph()
    crossings = 0
    for e in graph.edges:
        seg = LineString(e.shape)
        if seg.intersects(RIVER):
            assert e.kind == KIND_TUNNEL, "非地道边不得穿河"
            crossings += 1
    assert crossings >= 1, "地道边必须穿河成图"


def test_reach_south_bank_via_tunnel() -> None:
    graph = _graph()
    start = graph.snap(*PROJ.to_lnglat(50.0, 100.0))
    assert start is not None
    costs = cutoff_dijkstra(graph, start, 15 * 60)
    south = [
        nid
        for nid, (_x, y) in enumerate(graph.nodes)
        if y < -30.0 and not math.isinf(costs[nid])
    ]
    assert south, "必须能经地道到达南岸"
    tunnel_edges = [e for e in graph.edges if e.kind == KIND_TUNNEL]
    assert tunnel_edges
    assert all(not math.isinf(costs[e.u]) and not math.isinf(costs[e.v]) for e in tunnel_edges)


def test_without_tunnel_marker_footway_cannot_cross() -> None:
    """对照：同样的 footway 去掉 tunnel=yes，不得穿河成图。"""
    roads = [
        ROADS[0],
        ROADS[1],
        (LineString([(50.0, 100.0), (50.0, -100.0)]), {"highway": "footway"}),
    ]
    graph = build_graph(roads, [], WATER, [], CENTER, 1500.0)
    for e in graph.edges:
        assert not LineString(e.shape).intersects(RIVER), "无 tunnel/bridge 标记不得穿河"
    start = graph.snap(*PROJ.to_lnglat(-200.0, 100.0))
    assert start is not None
    costs = cutoff_dijkstra(graph, start, 15 * 60)
    assert all(
        math.isinf(costs[nid])
        for nid, (_x, y) in enumerate(graph.nodes)
        if y < -30.0
    ), "无桥无地道时南岸不可达"


def test_railway_barrier_blocks_and_tunnel_exempts() -> None:
    """铁路（护栏真实不可穿越）阻隔普通路；tunnel=yes 人行地道豁免。"""
    barrier = [(LineString([(100.0, -300.0), (100.0, 300.0)]), {"railway": "rail"})]
    roads = [
        (LineString([(-200.0, 150.0), (80.0, 150.0)]), {"highway": "residential"}),
        (LineString([(120.0, 150.0), (300.0, 150.0)]), {"highway": "residential"}),
        # 普通路穿 barrier：应被切断（不成边）
        (LineString([(80.0, 250.0), (120.0, 250.0)]), {"highway": "residential"}),
        # 地道 footway 穿 barrier：例外保留
        (
            LineString([(80.0, 150.0), (120.0, 150.0)]),
            {"highway": "footway", "tunnel": "yes"},
        ),
    ]
    graph = build_graph(roads, [], [], barrier, CENTER, 1500.0)
    barrier_line = barrier[0][0]
    for e in graph.edges:
        seg = LineString(e.shape)
        if seg.crosses(barrier_line):
            assert e.kind == KIND_TUNNEL, "非地道边不得跨越 barrier"
    start = graph.snap(*PROJ.to_lnglat(80.0, 150.0))
    assert start is not None
    costs = cutoff_dijkstra(graph, start, 15 * 60)
    east = [
        nid
        for nid, (x, _y) in enumerate(graph.nodes)
        if x > 100.0 and not math.isinf(costs[nid])
    ]
    assert east, "barrier 东侧应经地道可达"


def test_highway_barrier_crossable() -> None:
    """高速/快速路主线不阻隔步行图：交叉口以高架/地道形式被穿越，
    现实中不存在平交，几何相交即视为可穿越点（桥梁/下穿）。"""
    barrier = [(LineString([(100.0, -300.0), (100.0, 300.0)]), {"highway": "motorway"})]
    roads = [
        (LineString([(-200.0, 150.0), (80.0, 150.0)]), {"highway": "residential"}),
        (LineString([(120.0, 150.0), (300.0, 150.0)]), {"highway": "residential"}),
(LineString([(80.0, 150.0), (120.0, 150.0)]), {"highway": "residential"}),
    ]
    graph = build_graph(roads, [], [], barrier, CENTER, 1500.0)
    west = [nid for nid, (x, _y) in enumerate(graph.nodes) if x < 100.0]
    assert west, "barrier 西侧应存在节点"
    costs = cutoff_dijkstra(graph, west[0], 15 * 60)
    east = [
        nid
        for nid, (x, _y) in enumerate(graph.nodes)
        if x > 100.0 and not math.isinf(costs[nid])
    ]
    assert east, "motorway barrier 不得割裂步行图"
