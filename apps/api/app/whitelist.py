"""城市白名单与境内判定（WGS84 经纬度框）。

白名单内完整体验；名单外允许计算但标记 methodOnly（未做设施词表校准）；
境外直接拒绝（400）。
"""

from __future__ import annotations

# city -> (min_lng, min_lat, max_lng, max_lat)
CITY_BOXES: dict[str, tuple[float, float, float, float]] = {
    "北京": (115.4, 39.4, 117.6, 41.1),
    "上海": (120.8, 30.7, 122.2, 31.9),
    "杭州": (118.3, 29.2, 120.8, 30.7),
    "成都": (103.5, 30.0, 105.0, 31.4),
    "广州": (112.9, 22.6, 113.95, 23.9),
    "深圳": (113.75, 22.35, 114.65, 22.9),
}


def whitelist_city(lng: float, lat: float) -> str | None:
    for city, (x0, y0, x1, y1) in CITY_BOXES.items():
        if x0 <= lng <= x1 and y0 <= lat <= y1:
            return city
    return None


def in_whitelist(lng: float, lat: float) -> bool:
    return whitelist_city(lng, lat) is not None


def is_overseas(lng: float, lat: float) -> bool:
    """境外判定：经度 <73 或 >135，纬度 <18 或 >54。"""
    return lng < 73.0 or lng > 135.0 or lat < 18.0 or lat > 54.0
