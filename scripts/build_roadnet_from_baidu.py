"""用百度 DirectionLite 步行路线逆向采样真实路网，生成 roads.geojson。

原理：在区域网格上撒采样点，对相邻点（右/下邻居，可选对角）两两调步行
路线；百度返回的折线天然沿真实路网、不穿水/不穿楼。合并全部折线并用
unary_union 在交叉口打断（noding），即得真实步行路网。

用法：
    .venv/Scripts/python scripts/build_roadnet_from_baidu.py \
        --region jinsong --center 116.4578,39.8846 --radius 1700 --spacing 200

说明：
- 需 BAIDU_SERVER_AK（自动加载根 .env）；默认 2 QPS，612 对约 6 分钟；
- 输出 data/map_fabric/{region}/roads.geojson（WGS84）；
- 同时把 buildings/barriers 置空：百度路线天然遵守真实阻隔，约束层冗余，
  旧合成建筑若叠加会切断真实路网。water.geojson 不动（另行手绘替换）；
- 结果缓存复用 .cache：同参数重跑零 API 调用；
- 已知限制：只覆盖被采样 OD 对走过的路段，小巷可能缺失（可加 --diagonals
  或减小 --spacing 补采样，缓存命中部分不会重复计费）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))

from dotenv import load_dotenv

load_dotenv(REPO_ROOT / ".env", override=False)

from app.baidu import BaiduClient, BaiduError
from app.checkup import _parse_directionlite_path
from geo import LocalProjection, bd09ll_to_wgs84, wgs84_to_bd09ll
from shapely.geometry import LineString, MultiLineString
from shapely.ops import unary_union

EMPTY_FC = {"type": "FeatureCollection", "features": []}


def _grid_points(
    proj: LocalProjection, radius: float, spacing: float, jitter: float, rng: random.Random
) -> list[list[tuple[float, float]]]:
    """以区域中心为原点的本地米坐标网格（含抖动），返回 [row][col] = (x, y)。"""
    n = round(2 * radius / spacing)
    xs = [-radius + i * spacing for i in range(n + 1)]
    amp = spacing * jitter
    grid: list[list[tuple[float, float]]] = []
    for y in xs:
        row: list[tuple[float, float]] = []
        for x in xs:
            jx = x + rng.uniform(-amp, amp)
            jy = y + rng.uniform(-amp, amp)
            row.append((jx, jy))
        grid.append(row)
    return grid


def _neighbor_pairs(
    grid: list[list[tuple[float, float]]], diagonals: bool
) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    rows = len(grid)
    cols = len(grid[0])
    pairs: list[tuple[tuple[float, float], tuple[float, float]]] = []
    for r in range(rows):
        for c in range(cols):
            if c + 1 < cols:
                pairs.append((grid[r][c], grid[r][c + 1]))
            if r + 1 < rows:
                pairs.append((grid[r][c], grid[r + 1][c]))
            if diagonals:
                if r + 1 < rows and c + 1 < cols:
                    pairs.append((grid[r][c], grid[r + 1][c + 1]))
                if r + 1 < rows and c - 1 >= 0:
                    pairs.append((grid[r][c], grid[r + 1][c - 1]))
    return pairs


async def _sample(
    client: BaiduClient,
    proj: LocalProjection,
    pairs: list[tuple[tuple[float, float], tuple[float, float]]],
) -> list[list[tuple[float, float]]]:
    """逐对调 DirectionLite，返回本地米坐标折线列表。"""
    polylines: list[list[tuple[float, float]]] = []
    total = len(pairs)
    ok = fail = 0
    t0 = time.monotonic()
    for idx, (a_xy, b_xy) in enumerate(pairs, 1):
        a_bd = wgs84_to_bd09ll(*proj.to_lnglat(*a_xy))
        b_bd = wgs84_to_bd09ll(*proj.to_lnglat(*b_xy))
        path_bd: list[tuple[float, float]] | None = None
        # 配额类 status=401（并发超限）不触发客户端重试，这里自行退避重试
        for attempt in range(4):
            try:
                data = await client.walking_route(a_bd, b_bd)
            except BaiduError as exc:
                if attempt == 3:
                    print(f"  [warn] pair {idx}/{total} failed: {exc}", flush=True)
                else:
                    await asyncio.sleep(1.0 * (attempt + 1))
                continue
            path_bd = _parse_directionlite_path(data)
            break
        if path_bd:
            pts_xy = [proj.to_xy(*bd09ll_to_wgs84(lng, lat)) for lng, lat in path_bd]
            polylines.append(pts_xy)
            ok += 1
        else:
            fail += 1
        if idx % 20 == 0 or idx == total:
            elapsed = time.monotonic() - t0
            eta = elapsed / idx * (total - idx)
            print(
                f"[{idx}/{total}] ok={ok} fail={fail} "
                f"elapsed={elapsed:.0f}s eta={eta:.0f}s",
                flush=True,
            )
    return polylines


def _node_and_write(
    polylines: list[list[tuple[float, float]]],
    proj: LocalProjection,
    out_dir: Path,
) -> None:
    """交叉口打断（unary_union noding）→ WGS84 GeoJSON；buildings/barriers 置空。"""
    lines: list[LineString] = []
    for pts in polylines:
        rounded = [(round(x * 2) / 2, round(y * 2) / 2) for x, y in pts]
        dedup = [p for i, p in enumerate(rounded) if i == 0 or p != rounded[i - 1]]
        if len(dedup) >= 2:
            line = LineString(dedup)
            if line.length >= 1.0:
                lines.append(line)
    raw_km = sum(l.length for l in lines) / 1000.0
    print(f"polylines={len(lines)} raw_length={raw_km:.1f}km, noding...", flush=True)

    merged = unary_union(lines)
    segments: list[LineString] = []
    if isinstance(merged, LineString):
        segments = [merged]
    elif isinstance(merged, MultiLineString):
        segments = list(merged.geoms)
    else:  # GeometryCollection
        segments = [g for g in getattr(merged, "geoms", []) if isinstance(g, LineString)]

    features = []
    total_km = 0.0
    for seg in segments:
        if seg.length < 0.5:
            continue
        total_km += seg.length / 1000.0
        coords = [list(proj.to_lnglat(x, y)) for x, y in seg.coords]
        features.append(
            {
                "type": "Feature",
                "properties": {"highway": "residential", "source": "baidu_directionlite"},
                "geometry": {"type": "LineString", "coordinates": coords},
            }
        )
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "roads.geojson").open("w", encoding="utf-8") as fh:
        json.dump({"type": "FeatureCollection", "features": features}, fh, ensure_ascii=False)
    for layer in ("buildings", "barriers"):
        with (out_dir / f"{layer}.geojson").open("w", encoding="utf-8") as fh:
            json.dump(EMPTY_FC, fh, ensure_ascii=False)
    print(
        f"done: segments={len(features)} total={total_km:.1f}km -> {out_dir}/roads.geojson "
        f"(buildings/barriers 已置空)",
        flush=True,
    )


async def _main(args: argparse.Namespace) -> None:
    lng0, lat0 = (float(v) for v in args.center.split(","))
    proj = LocalProjection(lng0, lat0)
    rng = random.Random(args.seed)
    grid = _grid_points(proj, args.radius, args.spacing, args.jitter, rng)
    pairs = _neighbor_pairs(grid, args.diagonals)
    print(
        f"region={args.region} center=({lng0},{lat0}) grid={len(grid)}x{len(grid[0])} "
        f"pairs={len(pairs)} qps={args.qps}",
        flush=True,
    )
    client = BaiduClient(
        os.environ.get("BAIDU_SERVER_AK"),
        qps=args.qps,
        cache_dir=REPO_ROOT / ".cache",
    )
    if not client.enabled:
        raise SystemExit("BAIDU_SERVER_AK 未配置")
    try:
        polylines = await _sample(client, proj, pairs)
    finally:
        await client.aclose()
    print(f"api_calls={client.api_calls} polylines={len(polylines)}", flush=True)
    if not polylines:
        raise SystemExit("采样失败：没有任何折线返回")
    _node_and_write(polylines, proj, Path(args.out_dir or REPO_ROOT / "data" / "map_fabric" / args.region))


def main() -> None:
    ap = argparse.ArgumentParser(description="百度 DirectionLite 路网逆向采样")
    ap.add_argument("--region", default="jinsong")
    ap.add_argument("--center", default="116.4578,39.8846", help="WGS84 lng,lat")
    ap.add_argument("--radius", type=float, default=1700.0, help="采样半径（米）")
    ap.add_argument("--spacing", type=float, default=200.0, help="网格间距（米）")
    ap.add_argument("--jitter", type=float, default=0.25, help="抖动幅度（间距倍数）")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--diagonals", action="store_true", help="追加对角 OD 对（调用量约为原来的 1.85 倍）")
    ap.add_argument("--qps", type=float, default=1.0, help="默认 1.0：该 AK 步行路线并发配额为 1")
    ap.add_argument("--out-dir", default=None)
    args = ap.parse_args()
    asyncio.run(_main(args))


if __name__ == "__main__":
    main()
