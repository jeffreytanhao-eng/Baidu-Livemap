"""坐标工具单测：haversine、WGS84<->BD09LL 往返、本地投影、geohash5。"""

from __future__ import annotations

import math

from geo.coords import (
    LocalProjection,
    bd09ll_to_wgs84,
    gcj02_to_wgs84,
    geohash5,
    haversine_m,
    wgs84_to_bd09ll,
    wgs84_to_gcj02,
)

JINSONG = (116.4578, 39.8846)


def test_haversine_known_distance() -> None:
    d = haversine_m(116.0, 39.0, 116.0, 40.0)  # 1 个纬度
    assert 110_900.0 < d < 111_400.0
    assert haversine_m(116.0, 39.0, 116.0, 39.0) == 0.0


def test_wgs84_gcj02_roundtrip() -> None:
    lng, lat = JINSONG
    g_lng, g_lat = wgs84_to_gcj02(lng, lat)
    # 国内坐标偏移应非零且有限（几十到几百米量级）
    assert (g_lng, g_lat) != (lng, lat)
    back_lng, back_lat = gcj02_to_wgs84(g_lng, g_lat)
    err = haversine_m(lng, lat, back_lng, back_lat)
    assert err < 1.0, f"gcj02 往返误差 {err}m 应 < 1m"


def test_wgs84_bd09ll_roundtrip() -> None:
    lng, lat = JINSONG
    b_lng, b_lat = wgs84_to_bd09ll(lng, lat)
    assert (b_lng, b_lat) != (lng, lat)
    back_lng, back_lat = bd09ll_to_wgs84(b_lng, b_lat)
    err = haversine_m(lng, lat, back_lng, back_lat)
    assert err < 1.0, f"bd09ll 往返误差 {err}m 应 < 1m"


def test_out_of_china_passthrough() -> None:
    lng, lat = 139.6917, 35.6895  # 东京
    assert wgs84_to_gcj02(lng, lat) == (lng, lat)


def test_local_projection_roundtrip() -> None:
    proj = LocalProjection(*JINSONG)
    x, y = proj.to_xy(116.4678, 39.8946)
    assert x > 0 and y > 0
    lng, lat = proj.to_lnglat(x, y)
    assert abs(lng - 116.4678) < 1e-9
    assert abs(lat - 39.8946) < 1e-9
    # 米制尺度 sanity：1 个纬度约 111km
    _, y1 = proj.to_xy(JINSONG[0], JINSONG[1] + 1.0)
    assert 110_900.0 < y1 < 111_400.0


def test_geohash5() -> None:
    h = geohash5(*JINSONG)
    assert len(h) == 5
    assert h == geohash5(*JINSONG)  # 确定性
    # 标准 geohash 参考：(-5.6, 42.6) -> ezs42
    assert geohash5(-5.6, 42.6) == "ezs42"
    # 劲松格值（已用标准算法校验）
    assert h == "wx4ff"
    # 相邻点同格或邻格，不应报错
    h2 = geohash5(116.4588, 39.8856)
    assert len(h2) == 5


def test_geohash5_edge_points() -> None:
    assert len(geohash5(0.0, 0.0)) == 5
    assert len(geohash5(-179.9, -89.9)) == 5
    assert len(geohash5(179.9, 89.9)) == 5
    assert not math.isnan(geohash5(1.0, 1.0).__len__() * 1.0)
