"""对比报告取数脚本：对 5 个样例各跑一次真实编排（跳过快照），输出 JSON 供
docs/comparison-report.md 引用。

用法：.venv/Scripts/python scripts/compare_report.py  （Windows）
      .venv/bin/python scripts/compare_report.py       （macOS/Linux）

口径：约束步行底图等时圈（引擎层一次截止 Dijkstra）vs 同分钟直线对照圆；
POI 为无 AK 时的确定性合成设施，步行分钟取图上 Dijkstra 耗时；
10 个 POI 抽查给出「图上耗时 vs 直线/80m/min」对照。
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))

# 强制无 AK 口径（与文档声明一致）：pop 之后还要置空——load_dotenv 不会覆盖
# 已存在的环境变量，否则本机 .env 里的 BAIDU_SERVER_AK 会被重新注入。
os.environ.pop("BAIDU_SERVER_AK", None)
os.environ["BAIDU_SERVER_AK"] = ""
os.environ["DEMO_MODE"] = "false"
os.environ.setdefault("CACHE_DIR", str(REPO_ROOT / ".cache" / "compare-report"))

from app.checkup import run_checkup
from app.config import load_settings
from app.demo_data import REGION_CENTERS, SAMPLES
from app.engine import build_engine
from geo import (
    bd09ll_to_wgs84,
    circle_radius_m,
    cutoff_dijkstra,
    haversine_m,
)

CASES = [
    ("jinsong", 5),
    ("jinsong", 15),
    ("jinsong", 20),
    ("zhongguancun", 15),
    ("nanyuan", 15),
]

DIR_LABEL = {
    "N": "北", "NE": "东北", "E": "东", "SE": "东南",
    "S": "南", "SW": "西南", "W": "西", "NW": "西北",
}

ESTIMATE_SPEED_M_MIN = 80.0


def _summarize(region: str, minutes: int, body: dict, elapsed_wall_ms: int) -> dict:
    meta = body["meta"]
    coverage = body["coverage"]
    by_cat = {c["id"]: c for c in coverage["categories"]}
    anchor3 = {
        cat: {
            "nearestMinutes": by_cat[cat]["nearestMinutes"],
            "passed": by_cat[cat]["passed"],
            "count": by_cat[cat]["count"],
        }
        for cat in ("convenience", "health", "education")
    }
    worst = min(body["sectorGaps"], key=lambda g: g["gapRatio"])
    return {
        "region": region,
        "minutes": minutes,
        "score": coverage["score"],
        "anchor3Passed": coverage["anchor3Passed"],
        "anchor3": anchor3,
        "isochroneArea": round(meta["isochroneArea"]),
        "circleArea": round(meta["circleArea"]),
        "areaRatio": round(meta["areaRatio"], 4),
        "layers": {str(l["minutes"]): round(l["areaM2"]) for l in body["layers"]},
        "enclaves": len(body["enclaves"]),
        "blindSummary": {k: round(v, 4) for k, v in body["blindSummary"].items()},
        "worstSector": {
            "direction": worst["direction"],
            "label": DIR_LABEL[worst["direction"]],
            "gapRatio": round(worst["gapRatio"], 4),
            "barrierNote": worst.get("barrierNote"),
        },
        "sectorGaps": [
            {
                "direction": g["direction"],
                "gapRatio": round(g["gapRatio"], 4),
                "barrierNote": g.get("barrierNote"),
            }
            for g in body["sectorGaps"]
        ],
        "engineElapsedMs": meta["elapsedMs"],
        "wallElapsedMs": elapsed_wall_ms,
    }


def _nearest_node_scan(graph, lng: float, lat: float) -> tuple[int, float]:
    """全表扫描最近图节点（度 2 收缩后节点只在路口，故放宽 30m 吸附限制）。"""
    x, y = graph.proj.to_xy(lng, lat)
    best, best_d2 = 0, math.inf
    for nid, (nx, ny) in enumerate(graph.nodes):
        d2 = (nx - x) ** 2 + (ny - y) ** 2
        if d2 < best_d2:
            best, best_d2 = nid, d2
    return best, math.sqrt(best_d2)


def _poi_spotcheck(
    graph, costs: list[float], body: dict, center_wgs: tuple[float, float], limit: int = 10
) -> list[dict]:
    """10 个 POI 抽查：同一次 Dijkstra 的图上耗时 vs 直线/80m/min 估算。"""
    out = []
    for poi in body["pois"]:
        if len(out) >= limit:
            break
        w_lng, w_lat = bd09ll_to_wgs84(poi["lng"], poi["lat"])
        nid, snap_m = _nearest_node_scan(graph, w_lng, w_lat)
        if math.isinf(costs[nid]):
            continue  # 截止内图上不可达
        straight_m = haversine_m(center_wgs[0], center_wgs[1], w_lng, w_lat)
        straight_min = straight_m / ESTIMATE_SPEED_M_MIN
        graph_min = costs[nid] / 60.0
        out.append(
            {
                "name": poi["name"],
                "category": poi["category"],
                "graphMinutes": round(graph_min, 1),
                "straightLineMinutes": round(straight_min, 1),
                "detourRatio": round(graph_min / straight_min, 2) if straight_min > 0 else None,
                "snapToNodeM": round(snap_m),
            }
        )
    return out


async def main() -> None:
    settings = load_settings()
    t0 = time.perf_counter()
    ctx = build_engine(settings)
    warmup_ms = round((time.perf_counter() - t0) * 1000)
    graph = {
        name: {"nodes": b.graph.node_count, "edges": b.graph.edge_count}
        for name, b in ctx.regions.items()
    }

    sample_centers = {s.region: s.center_wgs for s in SAMPLES}
    summaries = []
    spotcheck = None
    for region, minutes in CASES:
        center = sample_centers.get(region) or REGION_CENTERS[region]
        t1 = time.perf_counter()
        body = await run_checkup(
            ctx,
            center_wgs=center,
            minutes=minutes,
            engine="map-fabric",
            include_blind_walk=True,
            phase="full",
            allow_snapshot=False,
        )
        wall_ms = round((time.perf_counter() - t1) * 1000)
        summaries.append(_summarize(region, minutes, body, wall_ms))
        if region == "jinsong" and minutes == 15:
            bundle = ctx.regions["jinsong"]
            g = bundle.graph
            node = g.snap(*center)
            if node is None:
                node, _ = _nearest_node_scan(g, *center)
            costs = cutoff_dijkstra(g, node, 20 * 60.0)  # 放宽截止便于抽查更远 POI
            spotcheck = _poi_spotcheck(g, costs, body, center)

    await ctx.baidu.aclose()
    print(
        json.dumps(
            {
                "warmupMs": warmup_ms,
                "graphs": graph,
                "cases": summaries,
                "poiSpotcheckJinsong15": spotcheck,
                "circleRadiusM": {str(m): circle_radius_m(m) for m in (5, 15, 20)},
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
