"""真实 AK 对比取数脚本：劲松 5/15/20 用百度真实 POI（Place API + 距离矩阵校准）
跑全管线，与 README「五·5」合成 POI 模拟比对同口径对照。

用法：.venv/Scripts/python scripts/compare_real_ak.py   （Windows）
      .venv/bin/python scripts/compare_real_ak.py       （macOS/Linux）

前置：.env 中配置 BAIDU_SERVER_AK（本脚本不覆盖、不注入演示口径）。
调用量：每档约 9 次 place_search + 距离矩阵校准，全部走统一缓存管线落盘
（.cache/compare-real-ak，复跑零配额消耗）。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))

# 真实 AK 口径：保留 .env 的 BAIDU_SERVER_AK，显式关掉演示模式；
# 缓存目录独立于主服务与模拟比对（compare_report.py），互不污染
os.environ["DEMO_MODE"] = "false"
os.environ.setdefault("CACHE_DIR", str(REPO_ROOT / ".cache" / "compare-real-ak"))

from app.checkup import run_checkup
from app.config import load_settings
from app.demo_data import SAMPLES
from app.engine import build_engine

CASES = [("jinsong", 5), ("jinsong", 15), ("jinsong", 20)]

DIR_LABEL = {
    "N": "北", "NE": "东北", "E": "东", "SE": "东南",
    "S": "南", "SW": "西南", "W": "西", "NW": "西北",
}


def _summarize(region: str, minutes: int, body: dict, elapsed_wall_ms: int) -> dict:
    meta = body["meta"]
    coverage = body["coverage"]
    worst = min(body["sectorGaps"], key=lambda g: g["gapRatio"])
    return {
        "region": region,
        "minutes": minutes,
        "score": coverage["score"],
        "anchor3Passed": coverage["anchor3Passed"],
        "categories": [
            {
                "id": c["id"],
                "name": c["name"],
                "count": c["count"],
                "withinCount": c["withinCount"],
                "nearestMinutes": c["nearestMinutes"],
                "passed": c["passed"],
            }
            for c in coverage["categories"]
        ],
        "isochroneArea": round(meta["isochroneArea"]),
        "circleArea": round(meta["circleArea"]),
        "areaRatio": round(meta["areaRatio"], 4),
        "enclaves": len(body["enclaves"]),
        "blindSummary": {k: round(v, 4) for k, v in body["blindSummary"].items()},
        "worstSector": {
            "direction": worst["direction"],
            "label": DIR_LABEL[worst["direction"]],
            "gapRatio": round(worst["gapRatio"], 4),
            "barrierNote": worst.get("barrierNote"),
        },
        "poiTotal": len(body["pois"]),
        "engineElapsedMs": meta["elapsedMs"],
        "wallElapsedMs": elapsed_wall_ms,
    }


async def main() -> None:
    settings = load_settings()
    if not settings.baidu_ak:
        print(json.dumps({"error": "BAIDU_SERVER_AK 未配置，无法跑真实口径"}, ensure_ascii=False))
        return
    t0 = time.perf_counter()
    ctx = build_engine(settings)
    warmup_ms = round((time.perf_counter() - t0) * 1000)
    graphs = {
        name: {"nodes": b.graph.node_count, "edges": b.graph.edge_count}
        for name, b in ctx.regions.items()
        if name == "jinsong"
    }
    api_calls_start = ctx.baidu.api_calls

    sample_centers = {s.region: s.center_wgs for s in SAMPLES}
    summaries = []
    for region, minutes in CASES:
        center = sample_centers[region]
        t1 = time.perf_counter()
        body = await run_checkup(
            ctx,
            center_wgs=center,
            minutes=minutes,
            engine="map-fabric",
            phase="full",
            allow_snapshot=False,
        )
        wall_ms = round((time.perf_counter() - t1) * 1000)
        summaries.append(_summarize(region, minutes, body, wall_ms))

    await ctx.baidu.aclose()
    print(
        json.dumps(
            {
                "warmupMs": warmup_ms,
                "graphs": graphs,
                "apiCalls": ctx.baidu.api_calls - api_calls_start,
                "cases": summaries,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
