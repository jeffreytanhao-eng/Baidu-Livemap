"""白名单 / 境内判定纯函数单测。"""

from __future__ import annotations

from app.whitelist import in_whitelist, is_overseas, whitelist_city


def test_whitelist_cities():
    assert whitelist_city(116.40, 39.90) == "北京"
    assert whitelist_city(121.47, 31.23) == "上海"
    assert whitelist_city(120.15, 30.27) == "杭州"
    assert whitelist_city(104.07, 30.66) == "成都"
    assert whitelist_city(113.26, 23.13) == "广州"
    assert whitelist_city(114.06, 22.54) == "深圳"


def test_outside_whitelist():
    assert whitelist_city(114.51, 38.04) is None  # 石家庄
    assert in_whitelist(114.51, 38.04) is False
    assert in_whitelist(116.40, 39.90) is True


def test_overseas():
    assert is_overseas(139.7, 35.7) is True  # 东京（经度 >135）
    assert is_overseas(100.0, 8.0) is True  # 纬度 <18
    assert is_overseas(60.0, 40.0) is True  # 经度 <73
    assert is_overseas(116.4, 39.9) is False
