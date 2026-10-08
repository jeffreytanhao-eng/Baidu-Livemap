#!/usr/bin/env python3
"""tile_fabric.py — 把全量底图六层 GeoJSON 切成带邻域 pad 的 4km 网格片。

每个 tile 收录「自身 bbox 外扩 TILE_PAD_M」相交的全部要素。roads/buildings
要素天然短小，跨片整条复制不裁剪；water/green/barriers/residential（河流、
快速路、公园绿地、大型居住区面）会横穿全城，必须按 padded bbox 裁剪几何后
再写片——否则整条河被复制进沿途每张片，load_region 的 region bbox 被拉到
几十公里宽，引擎 _pick_region 会按中心点选错片（通州点裁到西城路网的根因），
且建图半径爆炸。

用法（源 = import_osm_fabric.py 全量裁片，如六环 tmp_full6r）：
    .venv/Scripts/python scripts/tile_fabric.py \
        --source data/map_fabric/tmp_full6r --out-root data/map_fabric
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

import shapely
from shapely import STRtree
from shapely.geometry import mapping, shape

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "packages" / "geo"))

from geo.tiles import (
    TILE_PAD_M,
    tile_bbox,
    tile_indices,
    tile_name,
)

LAYERS = ("roads", "buildings", "residential", "water", "green", "barriers")
# 长线/大面层：要素可横穿全城，写片前必须按 padded bbox 裁剪几何
CLIP_LAYERS = frozenset({"water", "barriers", "residential", "green"})


def main() -> int:
    ap = argparse.ArgumentParser(description="全量底图 → 4km 邻域 pad 网格片")
    ap.add_argument("--source", required=True, help="全量裁片目录（四层 geojson）")
    ap.add_argument("--out-root", default=str(REPO_ROOT / "data" / "map_fabric"))
    ap.add_argument(
        "--tiles", default="",
        help="只切指定 tile（逗号分隔 r,c 对，如 10,12;11,12），默认全部",
    )
    args = ap.parse_args()

    src = Path(args.source)
    if not src.is_dir():
        print(f"[tile] 源目录不存在: {src}")
        return 1
    out_root = Path(args.out_root)

    wanted: set[tuple[int, int]] | None = None
    if args.tiles:
        wanted = {tuple(int(v) for v in pair.split(",")) for pair in args.tiles.split(";")}

    # 逐层装载 → STRtree，按 padded tile bbox 批量查询归属（bbox 相交即收录）
    per_tile: dict[tuple[int, int], dict[str, list]] = defaultdict(lambda: {l: [] for l in LAYERS})
    for layer in LAYERS:
        fc = json.loads((src / f"{layer}.geojson").read_text(encoding="utf-8"))
        feats = fc.get("features", [])
        geoms = [shape(f["geometry"]) for f in feats]
        if not geoms:
            continue
        tree = STRtree(geoms)
        # 六环外接矩形覆盖的网格范围 + pad 余量
        r0, c0 = tile_indices(116.02, 39.66)
        r1, c1 = tile_indices(116.76, 40.22)
        n_assigned = 0
        for row in range(r0, r1 + 1):
            for col in range(c0, c1 + 1):
                if wanted is not None and (row, col) not in wanted:
                    continue
                w, s, e, n = tile_bbox(row, col, pad_m=TILE_PAD_M)
                padded = shapely.box(w, s, e, n)
                for idx in tree.query(padded):
                    if layer in CLIP_LAYERS:
                        clipped = geoms[idx].intersection(padded)
                        if clipped.is_empty:
                            continue
                        per_tile[(row, col)][layer].append(
                            {
                                "type": "Feature",
                                "geometry": mapping(clipped),
                                "properties": feats[idx].get("properties") or {},
                            }
                        )
                    else:
                        per_tile[(row, col)][layer].append(feats[idx])
                    n_assigned += 1
        print(f"[tile] {layer}: {len(feats)} 要素，pad 归属 {n_assigned} 次")

    total_mb = 0.0
    max_roads = 0
    written = 0
    for (row, col), layers in sorted(per_tile.items()):
        if not any(layers.values()):
            continue
        tdir = out_root / tile_name(row, col)
        tdir.mkdir(parents=True, exist_ok=True)
        tile_mb = 0.0
        for layer in LAYERS:
            fc = {
                "type": "FeatureCollection",
                "features": layers[layer],
            }
            p = tdir / f"{layer}.geojson"
            p.write_text(json.dumps(fc, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            tile_mb += p.stat().st_size / 1e6
        total_mb += tile_mb
        max_roads = max(max_roads, len(layers["roads"]))
        written += 1

    print(
        f"[tile] 写出 {written} 片，合计 {total_mb:.0f}MB，"
        f"单片最大 roads={max_roads} 条；pad={TILE_PAD_M:.0f}m"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
