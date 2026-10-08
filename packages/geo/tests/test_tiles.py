"""geo.tiles 网格数学：索引、命名、bbox 外扩。"""

from __future__ import annotations

from geo.tiles import (
    TILE_SIZE_LAT,
    TILE_SIZE_LNG,
    parse_tile_name,
    tile_bbox,
    tile_indices,
    tile_name,
)


def test_indices_and_name_roundtrip():
    row, col = tile_indices(116.4707, 40.0043)  # 瑞悦府
    name = tile_name(row, col)
    assert parse_tile_name(name) == (row, col)


def test_parse_tile_name():
    assert parse_tile_name("b6r_r03c07") == (3, 7)
    assert parse_tile_name("jinsong") is None
    assert parse_tile_name("b6r_bogus") is None


def test_tile_bbox_no_pad():
    row, col = tile_indices(116.4707, 40.0043)
    w, s, e, n = tile_bbox(row, col)
    assert abs((e - w) - TILE_SIZE_LNG) < 1e-9
    assert abs((n - s) - TILE_SIZE_LAT) < 1e-9
    # 点落在片内
    assert w <= 116.4707 < e and s <= 40.0043 < n


def test_tile_bbox_pad_expands():
    row, col = tile_indices(116.4707, 40.0043)
    w, _s, e, _n = tile_bbox(row, col, pad_m=2200.0)
    # 2200m ≈ 0.01976 度纬距
    assert round((e - w) - TILE_SIZE_LNG, 5) == round(2 * 2200 / 111320.0, 5)
