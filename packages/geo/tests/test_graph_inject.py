"""百度步行折线注入：折线拆边上图、source=baidu、权更高优先、吸附/新建节点。"""

from __future__ import annotations

import math

from geo.coords import LocalProjection
from geo.graph import (
    SOURCE_BAIDU,
    SOURCE_OSM,
    SPEED_WALK_M_MIN,
    build_graph,
    inject_baidu_polyline,
)
from geo.isochrone import cutoff_dijkstra
from shapely.geometry import LineString

CENTER = (116.4578, 39.8846)
PROJ = LocalProjection(*CENTER)

# L 形路网：(0,0)->(200,0)->(200,200)，无任何建筑/水/阻隔
ROADS: list[tuple[LineString, dict]] = [
    (LineString([(0.0, 0.0), (200.0, 0.0)]), {"highway": "residential"}),
    (LineString([(200.0, 0.0), (200.0, 200.0)]), {"highway": "residential"}),
]


def _ll(x: float, y: float) -> tuple[float, float]:
    return PROJ.to_lnglat(x, y)


def _graph():
    return build_graph(ROADS, [], [], [], CENTER, 1000.0)


def test_existing_edges_default_source_osm() -> None:
    graph = _graph()
    assert graph.edge_count > 0
    assert {e.source for e in graph.edges} == {SOURCE_OSM}


def test_inject_diagonal_creates_baidu_edges_and_shortcut() -> None:
    graph = _graph()
    base_edges = graph.edge_count
    base_nodes = graph.node_count

    # 对角线 (0,0)->(200,200)：远离 L 形路网（除两端），中段新建节点
    path = [_ll(0.0, 0.0), _ll(100.0, 100.0), _ll(200.0, 200.0)]
    injected = inject_baidu_polyline(graph, path)
    assert injected == 2  # 两段折线 = 两条边

    assert graph.edge_count == base_edges + 2
    assert graph.node_count > base_nodes  # (100,100) 离路网 >30m，新建节点

    baidu_edges = [e for e in graph.edges if e.source == SOURCE_BAIDU]
    assert len(baidu_edges) == 2
    # 权 = 长度/速度（与同长 OSM 边同速，忠实百度实测耗时）
    for e in baidu_edges:
        assert math.isclose(
            e.weight_s, e.length_m / SPEED_WALK_M_MIN * 60.0, rel_tol=1e-6
        )

    # Dijkstra：对角捷径耗时 < 沿路网 L 形绕行
    start = graph.snap(*_ll(0.0, 0.0))
    end = graph.snap(*_ll(200.0, 200.0))
    assert start is not None and end is not None
    costs = cutoff_dijkstra(graph, start, 15 * 60)
    diag_len = 2 * math.hypot(100.0, 100.0)
    road_len = 400.0
    assert costs[end] <= diag_len / SPEED_WALK_M_MIN * 60.0 + 1.0
    assert costs[end] < road_len / SPEED_WALK_M_MIN * 60.0


def test_inject_snaps_to_existing_node_within_30m() -> None:
    graph = _graph()
    base_nodes = graph.node_count
    # 顶点 (3,3)/(197,197) 距两端路口节点 <30m，应吸附而非新建
    path = [_ll(3.0, 3.0), _ll(197.0, 197.0)]
    injected = inject_baidu_polyline(graph, path)
    assert injected == 1
    assert graph.node_count == base_nodes  # 两端都吸附到既有节点
    edge = next(e for e in graph.edges if e.source == SOURCE_BAIDU)
    assert {edge.u, edge.v} <= set(range(base_nodes))


def test_inject_trivial_and_idempotent_shape() -> None:
    graph = _graph()
    assert inject_baidu_polyline(graph, []) == 0
    assert inject_baidu_polyline(graph, [_ll(50.0, 50.0)]) == 0
    # 注入后既有 OSM 边不受影响
    assert all(e.source == SOURCE_OSM for e in graph.edges)
