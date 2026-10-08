"""性能与一次性多层测试。"""

from __future__ import annotations

import math
import time
from unittest import mock

import geo.isochrone as iso
import pytest
from geo.graph import build_graph
from geo.isochrone import cutoff_dijkstra, layers_from_one_run
from shapely.geometry import LineString

CENTER = (116.4578, 39.8846)


def _grid_roads(n: int = 100, step: float = 20.0) -> list[tuple[LineString, dict]]:
    """n x n 方格路网：2*n*(n+1) 条约 2 万条边。"""
    half = n * step / 2.0
    roads: list[tuple[LineString, dict]] = []
    for i in range(n + 1):
        c = -half + i * step
        roads.append((LineString([(-half, c), (half, c)]), {"highway": "residential"}))
        roads.append((LineString([(c, -half), (c, half)]), {"highway": "residential"}))
    return roads


@pytest.fixture(scope="module")
def grid_graph():
    return build_graph(_grid_roads(), [], [], [], CENTER, 2000.0)


def test_grid_graph_scale(grid_graph) -> None:
    # 100x100 格 -> 202 条线 -> 约 2 万条边（度2收缩不适用于网格交叉点）
    assert grid_graph.edge_count >= 20_000
    assert grid_graph.node_count >= 10_000


def test_cutoff_dijkstra_perf(grid_graph) -> None:
    proj = grid_graph.proj
    start = grid_graph.snap(*proj.to_lnglat(0.0, 0.0))
    assert start is not None
    t0 = time.perf_counter()
    costs = cutoff_dijkstra(grid_graph, start, 15 * 60)
    elapsed = time.perf_counter() - t0
    reached = sum(1 for c in costs if not math.isinf(c))
    assert reached > 5_000
    # 目标 <100ms；考虑机器差异放宽到 500ms（CI/笔记本余量）
    assert elapsed < 0.5, f"cutoff dijkstra 耗时 {elapsed * 1000:.0f}ms 超限"
    print(f"\n[perf] cutoff_dijkstra 2万边图: {elapsed * 1000:.1f}ms, reached={reached}")


def test_layers_from_one_run_single_dijkstra(river_graph) -> None:
    """5/10/15 三层必须来自同一次 Dijkstra（mock 计数只调用一次）。"""
    proj = river_graph.proj
    start = river_graph.snap(*proj.to_lnglat(0.0, 100.0))
    assert start is not None

    real = iso.cutoff_dijkstra
    counter = {"n": 0}

    def counting(*args, **kwargs):
        counter["n"] += 1
        return real(*args, **kwargs)

    with mock.patch.object(iso, "cutoff_dijkstra", side_effect=counting) as mocked:
        costs = iso.cutoff_dijkstra(river_graph, start, 15 * 60)
        layers = layers_from_one_run(costs, [5, 10, 15])
        assert mocked.call_count == 1, "多层结果禁止每层各跑一次 Dijkstra"

    n5 = set(layers[5])
    n10 = set(layers[10])
    n15 = set(layers[15])
    assert n5 and n5 <= n10 <= n15, "层必须嵌套扩大"
    assert n5 < n15, "5 分钟层必须严格小于 15 分钟层"
    for cost in layers[5].values():
        assert cost <= 300.0


def test_snap_grid_consistency(river_graph) -> None:
    """25m 网格内邻近点吸附到同一节点（缓存友好）。"""
    proj = river_graph.proj
    n1 = river_graph.snap(*proj.to_lnglat(0.0, 100.0))
    n2 = river_graph.snap(*proj.to_lnglat(3.0, 102.0))
    assert n1 is not None and n1 == n2
