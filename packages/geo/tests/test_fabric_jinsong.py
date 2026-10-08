"""劲松合成底图端到端冒烟：fabric 载入 -> 分类 -> 建图 -> 出圈。

验证全部规则在合成包上演示：桥过河、barrier 阻断、天桥跨越、
商场廊道、住宅让位、水域让位、河南岸成飞地。

注意：jinsong 已切换为百度 DirectionLite 采样的真实路网（无合成建筑/水域），
本用例固定针对保留的合成副本 ``jinsong_synth``，继续覆盖引擎规则。
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest
from geo.coords import LocalProjection
from geo.enterability import (
    CATEGORY_BLOCKED,
    CATEGORY_ENTERABLE,
    CATEGORY_OPEN,
    CATEGORY_PODIUM,
    classify_buildings,
)
from geo.fabric import MapFabricService
from geo.graph import KIND_BRIDGE, KIND_CORRIDOR, KIND_PODIUM, KIND_TUNNEL, build_graph
from geo.isochrone import build_isochrone, cutoff_dijkstra, fast_mask
from shapely.geometry import Point

CENTER = (116.4578, 39.8846)
FABRIC_ROOT = Path(__file__).resolve().parents[3] / "data" / "map_fabric"

SYNTH_REGION = "jinsong_synth"

pytestmark = pytest.mark.skipif(
    not (FABRIC_ROOT / SYNTH_REGION / "roads.geojson").exists(),
    reason="jinsong_synth fabric not built (run scripts/build_map_fabric.py)",
)


@pytest.fixture(scope="module")
def fabric() -> MapFabricService:
    svc = MapFabricService(FABRIC_ROOT)
    svc.load_region(SYNTH_REGION)
    return svc


@pytest.fixture(scope="module")
def layers(fabric):
    return fabric.clip(CENTER, 1500.0)


@pytest.fixture(scope="module")
def classified(layers):
    return classify_buildings(layers.buildings)


@pytest.fixture(scope="module")
def graph(layers, classified):
    return build_graph(layers.roads, classified, layers.water, layers.barriers, CENTER, 1500.0)


def test_fabric_load_and_covers(fabric, layers) -> None:
    assert SYNTH_REGION in fabric.regions
    assert fabric.covers(*CENTER)
    assert not fabric.covers(0.0, 0.0)
    assert layers.roads and layers.buildings and layers.water and layers.barriers


def test_classification_mix(classified) -> None:
    cats = {b.category for b in classified}
    assert CATEGORY_ENTERABLE in cats, "应有商场"
    assert CATEGORY_PODIUM in cats, "应有底商办公"
    assert CATEGORY_BLOCKED in cats, "应有住宅"
    assert CATEGORY_OPEN in cats, "应有公园"
    names = [b.properties.get("name") for b in classified]
    assert "劲松购物中心" in names
    assert "劲松大厦" in names


def test_graph_rules(graph) -> None:
    assert graph.node_count > 100
    assert graph.edge_count > 100
    kinds = {e.kind for e in graph.edges}
    assert KIND_BRIDGE in kinds, "桥上边应保留"
    assert KIND_CORRIDOR in kinds, "商场应生成廊道边"
    assert KIND_PODIUM in kinds, "底商办公应生成 8m 廊道边"

    # 没有非桥/非地道边跨河（河带 y∈[-730,-670] 内的边必须是桥或人行地道）
    for e in graph.edges:
        x_mid = (e.shape[0][0] + e.shape[-1][0]) / 2.0
        y_mid = (e.shape[0][1] + e.shape[-1][1]) / 2.0
        if -730.0 < y_mid < -670.0 and abs(x_mid) < 1500.0:
            assert e.kind in (KIND_BRIDGE, KIND_TUNNEL), (
                f"河带内的边必须是桥或人行地道: {e.kind}"
            )
    assert any(e.kind == KIND_TUNNEL for e in graph.edges), "合成底图应含人行地道边"


def test_motorway_not_blocking(graph) -> None:
    """高速/快速路不阻隔步行图：东西向普通路直接跨越 x=1100（立体交叉）。"""
    plain = [
        e
        for e in graph.edges
        if min(p[0] for p in e.shape) < 1100.0 < max(p[0] for p in e.shape)
        and e.kind not in (KIND_BRIDGE, KIND_TUNNEL)
    ]
    assert plain, "普通路应可直接跨越 motorway（交叉口高架/地道）"


def test_isochrone_end_to_end(graph, classified, layers) -> None:
    start = graph.snap(*CENTER)
    assert start is not None
    costs = cutoff_dijkstra(graph, start, 15 * 60)

    walls = [b.wall for b in classified if b.wall is not None]
    water = [g for g, _ in layers.water]
    result = build_isochrone(graph, costs, 15, walls, water)
    assert result.area_m2 > 100_000.0
    assert result.geometry is not None

    # 斑让开水域与住宅墙厚区
    for w in water:
        assert result.geometry.intersection(w).area < 1e-6
    for b in classified:
        if b.wall is not None:
            assert result.geometry.intersection(b.wall).area < 1e-6

    # 商场轮廓进入斑内
    mall = next(b for b in classified if b.properties.get("name") == "劲松购物中心")
    assert result.geometry.intersection(mall.geometry).area > 0.3 * mall.geometry.area

    # 河南岸只经桥可达 -> 成为飞地
    assert result.enclaves, "跨河到达的南岸应作为飞地返回"

    # 快拍也不穿河穿楼
    fast = fast_mask(graph, costs, 15, walls, water)
    assert fast.geometry is not None
    for w in water:
        assert fast.geometry.intersection(w).area < 1e-6


def test_motorway_east_reachable(graph) -> None:
    """motorway 东侧可达：普通路直连为主，人行天桥仍保留为桥边。"""
    proj = LocalProjection(*CENTER)
    start = graph.snap(*proj.to_lnglat(1000.0, 500.0))  # 天桥西侧路口出发
    assert start is not None
    costs = cutoff_dijkstra(graph, start, 15 * 60)
    east = [
        nid
        for nid, (x, _y) in enumerate(graph.nodes)
        if x > 1100.0 and not math.isinf(costs[nid])
    ]
    assert east, "东侧应可达（普通路跨越 + 天桥）"
    # 天桥边保留 KIND_BRIDGE 语义
    fb = [
        e
        for e in graph.edges
        if e.kind == KIND_BRIDGE and min(p[0] for p in e.shape) < 1100.0 < max(p[0] for p in e.shape)
    ]
    assert fb, "应存在跨 motorway 的天桥边"
    assert all(not math.isinf(costs[e.u]) and not math.isinf(costs[e.v]) for e in fb)


def test_snap_center(graph) -> None:
    assert graph.snap(*CENTER) is not None
    proj = LocalProjection(*CENTER)
    far = proj.to_lnglat(5000.0, 5000.0)
    assert graph.snap(*far) is None
    _ = Point(0, 0)
