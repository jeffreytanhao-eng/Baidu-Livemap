#!/usr/bin/env python3
"""build_demo_routes.py — 为 demo 模式生成「百度步行对照」真实折线。

旧合成底图时代 JINSONG_DEMO_ROUTES 是手画 XY；真实路网落地后改为：
从劲松样例中心出发，调 DirectionLite 步行规划到两个真实地标
（劲松地铁站 / 潘家园旧货市场），把百度返回的真实折线转成
REGION_CENTERS["jinsong"] 本地米坐标后打印 Python 字面量，
人工粘贴进 apps/api/app/demo_data.py。

用法：
    .venv/Scripts/python scripts/build_demo_routes.py

- 需 BAIDU_SERVER_AK（自动加载根 .env），仅 2 次 geocode + 2 次 DirectionLite；
- 同时输出 data/_demo_routes_preview.geojson（WGS84）供人工核对。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))

from dotenv import load_dotenv

load_dotenv(REPO_ROOT / ".env", override=False)

from app.baidu import BaiduClient, BaiduError
from app.checkup import _parse_directionlite_path
from app.demo_data import REGION_CENTERS, SAMPLES
from geo import LocalProjection, bd09ll_to_wgs84, wgs84_to_bd09ll

# (展示名, place_search 查询词)：以样例中心圆查，取最近匹配，避免 geocode 全城歧义
DESTINATIONS: tuple[tuple[str, str], ...] = (
    ("劲松地铁站", "劲松地铁站"),
    ("首都图书馆", "首都图书馆"),
)


async def _route(
    client: BaiduClient, center_bd: tuple[float, float], query: str
) -> list:
    results = await client.place_search(query, center_bd, radius_m=2000)
    if not results:
        raise SystemExit(f"[build_demo_routes] place_search 无结果: {query}")
    for cand in results[:3]:
        loc = cand.get("location") or {}
        print(f"  candidate {cand.get('name')} -> ({loc.get('lng')}, {loc.get('lat')})", flush=True)
    loc = results[0]["location"]
    dest_bd = (float(loc["lng"]), float(loc["lat"]))
    path_bd = None
    # 配额类 status=401（并发超限）不触发客户端重试，这里自行退避重试
    for attempt in range(4):
        try:
            data = await client.walking_route(center_bd, dest_bd)
        except BaiduError as exc:
            if attempt == 3:
                raise SystemExit(f"[build_demo_routes] walking_route 失败: {query}: {exc}")
            await asyncio.sleep(1.0 * (attempt + 1))
            continue
        path_bd = _parse_directionlite_path(data)
        break
    if not path_bd or len(path_bd) < 2:
        raise SystemExit(f"[build_demo_routes] 空折线: {query}")
    return path_bd


def main() -> int:
    ak = os.environ.get("BAIDU_SERVER_AK")
    if not ak:
        print("[build_demo_routes] BAIDU_SERVER_AK 未配置")
        return 1
    center_wgs = next(s.center_wgs for s in SAMPLES if s.id == "jinsong_15")
    center_bd = wgs84_to_bd09ll(*center_wgs)
    proj = LocalProjection(*REGION_CENTERS["jinsong"])

    client = BaiduClient(ak, qps=1.0, cache_dir=REPO_ROOT / ".cache")

    async def _run() -> list[tuple[str, list]]:
        try:
            out: list[tuple[str, list]] = []
            for label, query in DESTINATIONS:
                path_bd = await _route(client, center_bd, query)
                out.append((label, path_bd))
            return out
        finally:
            await client.aclose()

    routes = asyncio.run(_run())

    preview_features = []
    print("\n# ===== 粘贴到 demo_data.py 的 JINSONG_DEMO_ROUTES =====")
    print("JINSONG_DEMO_ROUTES: tuple[tuple[str, tuple[tuple[float, float], ...]], ...] = (")
    for label, path_bd in routes:
        path_wgs = [bd09ll_to_wgs84(lng, lat) for lng, lat in path_bd]
        xy = [(round(x, 1), round(y, 1)) for x, y in (proj.to_xy(*p) for p in path_wgs)]
        print(f'    (\n        "劲松中心→{label}",')
        print(f"        {tuple(xy)},")
        print("    ),")
        preview_features.append(
            {
                "type": "Feature",
                "properties": {"name": f"劲松中心→{label}", "points": len(xy)},
                "geometry": {"type": "LineString", "coordinates": [list(p) for p in path_wgs]},
            }
        )
    print(")")
    preview = {"type": "FeatureCollection", "features": preview_features}
    out_path = REPO_ROOT / "data" / "_demo_routes_preview.geojson"
    out_path.write_text(json.dumps(preview, ensure_ascii=False), encoding="utf-8")
    print(f"\npreview -> {out_path}  api_calls={client.api_calls}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
