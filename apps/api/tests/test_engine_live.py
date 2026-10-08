"""现场计算路径（绕过快照）：分层规则、分母随 X、吸附兜底、fast<300ms、不穿越水域。

直接调 run_checkup(allow_snapshot=False)，等价于无快照时的真实引擎路径；
demo 模式下 HTTP 层对非 5/15/20 分钟会回退 15 分钟快照（见 test_demo_mode）。
"""

from __future__ import annotations

import asyncio
import time

from app.checkup import run_checkup
from app.demo_data import SAMPLES
from geo import cutoff_dijkstra, fast_mask
from shapely.geometry import shape
from shapely.ops import transform as shp_transform
from shapely.ops import unary_union

JINSONG_CENTER_WGS = SAMPLES[0].center_wgs


def _run(ctx, minutes, phase="full", center=None):
    return asyncio.run(
        run_checkup(
            ctx,
            center_wgs=center or JINSONG_CENTER_WGS,
            minutes=minutes,
            engine="map-fabric",
            include_blind_walk=True,
            phase=phase,
            allow_snapshot=False,
        )
    )


def test_layers_rule(ctx):
    cases = {5: [3, 5], 10: [5, 10], 15: [5, 10, 15], 20: [5, 10, 15, 20]}
    for minutes, expected in cases.items():
        body = _run(ctx, minutes)
        assert [layer["minutes"] for layer in body["layers"]] == expected
    # 非预设档：2-4 层且必含 X
    mins = [layer["minutes"] for layer in _run(ctx, 12)["layers"]]
    assert 2 <= len(mins) <= 4 and mins[-1] == 12


def test_score_denominator_follows_minutes(ctx):
    # 同中心同 POI（合成器固定种子）：10 与 20 分母不同，分数必须不同。
    # 注意不断言单调性：综合分含面积比项（随 X 增大而下降），
    # 分母变大并不保证总分严格上升。
    s10 = _run(ctx, 10)["coverage"]["score"]
    s20 = _run(ctx, 20)["coverage"]["score"]
    assert s10 != s20


def test_snap_fallback_note(ctx):
    # 路网外（本地坐标 (5000, 5000)，距最近图节点 >3km）→ 兜底吸附并注明
    bundle = ctx.regions["jinsong"]
    proj = bundle.graph.proj
    lng, lat = proj.to_lnglat(5000.0, 5000.0)
    body = _run(ctx, 12, center=(lng, lat))
    assert "吸附" in body["meta"].get("snapNote", "")


def test_fast_under_300ms_and_no_water_crossing(ctx):
    bundle = ctx.regions["jinsong"]
    graph = bundle.graph
    t0 = time.perf_counter()
    body = _run(ctx, 15, phase="fast")
    wall_ms = (time.perf_counter() - t0) * 1000
    assert set(body) == {"meta", "fastLayer"}
    assert body["meta"]["elapsedMs"] < 300
    assert wall_ms < 1000

    # fastLayer 不得穿越通惠河：在引擎本地米坐标下与水域多边形交集面积 ≈ 0
    # （响应里的 BD09LL 是投放用坐标，round-trip 会有亚米级误差，故在原生坐标断言）
    node = graph.snap(*JINSONG_CENTER_WGS)
    assert node is not None
    costs = cutoff_dijkstra(graph, node, 15 * 60.0)
    res = fast_mask(graph, costs, 15, bundle.walls, bundle.water)
    poly_local = shp_transform(
        lambda x, y: graph.proj.to_xy(x, y), shape(res.polygon)
    )
    water = unary_union(bundle.water)
    leak = poly_local.intersection(water).area
    assert leak < 0.01, f"fastLayer 侵入水域 {leak:.4f} m²"


def test_live_demo_pois_cover_7_categories(ctx):
    body = _run(ctx, 15)
    assert body["pois"], "现场计算应有合成 POI"
    assert {p["category"] for p in body["pois"]} == {
        "convenience",
        "leisure",
        "mall",
        "education",
        "health",
        "public",
        "transport",
    }


def test_full_under_3s(ctx):
    t0 = time.perf_counter()
    body = _run(ctx, 13)
    wall_ms = (time.perf_counter() - t0) * 1000
    assert body["meta"]["elapsedMs"] < 3000
    print(f"\n[perf] live full elapsedMs={body['meta']['elapsedMs']} wall={wall_ms:.0f}ms")
