"""六环全量底图网格片划分（脚本切片与引擎 TileManager 共用）。

设计要点：切片时每个 tile 收录「自身 bbox 外扩 TILE_PAD_M」内的全部要素
（跨片复制、不裁剪几何）——保证片内任意点位的 20 分钟等时圈（≤1.6km+
缓冲）所需的邻域路网完整，相邻片交界处不出现截断。
"""

from __future__ import annotations

TILE_PREFIX = "b6r"

# 4km 网格（北京纬度 1e-2 度纬距 ≈ 1.11km）
TILE_SIZE_LNG = 0.047  # ≈ 3.9km @ 39.9°N
TILE_SIZE_LAT = 0.036  # ≈ 4.0km
# 网格原点取六环外接矩形西南角外，保证 r/c 从 0 起完整覆盖
TILE_ORIGIN_LNG = 115.96
TILE_ORIGIN_LAT = 39.64

# 邻域外扩：20 分钟步行 1.6km + 吸附/缓冲余量
TILE_PAD_M = 2200.0

_METERS_PER_DEG_LAT = 111_320.0


def tile_indices(lng: float, lat: float) -> tuple[int, int]:
    """点所在网格 (row, col)；row 自南向北、col 自西向东。"""
    col = int((lng - TILE_ORIGIN_LNG) / TILE_SIZE_LNG)
    row = int((lat - TILE_ORIGIN_LAT) / TILE_SIZE_LAT)
    return row, col


def tile_name(row: int, col: int) -> str:
    return f"{TILE_PREFIX}_r{row:02d}c{col:02d}"


def parse_tile_name(name: str) -> tuple[int, int] | None:
    """b6r_r03c07 -> (3, 7)；非本网格命名返回 None。"""
    if not name.startswith(f"{TILE_PREFIX}_r"):
        return None
    body = name[len(TILE_PREFIX) + 2 :]
    try:
        r, c = body.split("c")
        return int(r), int(c)
    except ValueError:
        return None


def tile_bbox(row: int, col: int, pad_m: float = 0.0) -> tuple[float, float, float, float]:
    """网格 bbox (w, s, e, n)；pad_m 为四周外扩米数。"""
    pad_deg = pad_m / _METERS_PER_DEG_LAT
    w = TILE_ORIGIN_LNG + col * TILE_SIZE_LNG - pad_deg
    s = TILE_ORIGIN_LAT + row * TILE_SIZE_LAT - pad_deg
    e = w + TILE_SIZE_LNG + 2 * pad_deg
    n = s + TILE_SIZE_LAT + 2 * pad_deg
    return w, s, e, n
