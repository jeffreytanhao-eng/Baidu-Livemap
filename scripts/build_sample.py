#!/usr/bin/env python3
"""build_sample.py — 离线生成演示快照到 data/samples/。

用真实引擎（geo 包 + data/map_fabric 底图 + 合成 POI 固定种子）跑
phase=full 完整编排，输出 5 份快照 JSON：

- jinsong_15.json（完整必交）
- jinsong_5.json / jinsong_20.json
- zhongguancun_15.json（密）
- nanyuan_15.json（稀）

重跑可复现（合成 POI 种子 = geohash5(center)|region）。
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))

from app.checkup import run_checkup
from app.config import Settings
from app.demo_data import SAMPLES
from app.engine import build_engine


def main() -> int:
    settings = Settings(
        baidu_ak=None,
        demo_mode=True,
        cache_dir=REPO_ROOT / ".cache",
        fabric_root=REPO_ROOT / "data" / "map_fabric",
        samples_dir=REPO_ROOT / "data" / "samples",
    )
    ctx = build_engine(settings)
    if not ctx.regions:
        print("[build_sample] 未找到任何 map fabric region，请先运行 build_map_fabric.py")
        return 1
    out_dir = settings.samples_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    for sample in SAMPLES:
        if sample.region not in ctx.regions:
            print(f"[build_sample] region 未加载，跳过 {sample.id}: {sample.region}")
            continue
        t0 = time.perf_counter()
        payload = asyncio.run(
            run_checkup(
                ctx,
                center_wgs=sample.center_wgs,
                minutes=sample.minutes,
                engine="map-fabric",
                include_blind_walk=True,
                phase="full",
                allow_snapshot=False,  # 生成期不回读已有快照
            )
        )
        payload["meta"]["demoMode"] = True  # 快照即演示数据
        path = out_dir / f"{sample.id}.json"
        with path.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1)
        meta = payload["meta"]
        print(
            f"[build_sample] {sample.id}: score={payload['coverage']['score']} "
            f"isoArea={meta['isochroneArea']:.0f}m² ratio={meta['areaRatio']:.2f} "
            f"elapsed={meta['elapsedMs']}ms wall={(time.perf_counter() - t0) * 1000:.0f}ms -> {path}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
