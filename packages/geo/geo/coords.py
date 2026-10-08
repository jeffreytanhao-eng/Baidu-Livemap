"""坐标工具：haversine 距离、WGS84<->GCJ02<->BD09LL 转换、本地等距圆柱投影、geohash5。

全部为纯函数，不访问网络。坐标系转换使用公开标准近似公式，
往返一致性在数十米量级内（满足工程近似需求）。
"""

from __future__ import annotations

import math

EARTH_RADIUS_M = 6371008.8

# GCJ02 椭球参数
_GCJ_A = 6378245.0
_GCJ_EE = 0.00669342162296594323

# BD09 偏移常量
_BD_X_PI = math.pi * 3000.0 / 180.0

_GEOHASH_ALPHABET = "0123456789bcdefghjkmnpqrstuvwxyz"


def haversine_m(lng1: float, lat1: float, lng2: float, lat2: float) -> float:
    """两 WGS84 经纬度点之间的大圆距离（米）。"""
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2.0) ** 2
    return 2.0 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(a)))


def _out_of_china(lng: float, lat: float) -> bool:
    return not (73.66 < lng < 135.05 and 3.86 < lat < 53.55)


def _transform_lat(lng: float, lat: float) -> float:
    ret = -100.0 + 2.0 * lng + 3.0 * lat + 0.2 * lat * lat + 0.1 * lng * lat + 0.2 * math.sqrt(abs(lng))
    ret += (20.0 * math.sin(6.0 * lng * math.pi) + 20.0 * math.sin(2.0 * lng * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(lat * math.pi) + 40.0 * math.sin(lat / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (160.0 * math.sin(lat / 12.0 * math.pi) + 320.0 * math.sin(lat * math.pi / 30.0)) * 2.0 / 3.0
    return ret


def _transform_lng(lng: float, lat: float) -> float:
    ret = 300.0 + lng + 2.0 * lat + 0.1 * lng * lng + 0.1 * lng * lat + 0.1 * math.sqrt(abs(lng))
    ret += (20.0 * math.sin(6.0 * lng * math.pi) + 20.0 * math.sin(2.0 * lng * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(lng * math.pi) + 40.0 * math.sin(lng / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (150.0 * math.sin(lng / 12.0 * math.pi) + 300.0 * math.sin(lng / 30.0 * math.pi)) * 2.0 / 3.0
    return ret


def wgs84_to_gcj02(lng: float, lat: float) -> tuple[float, float]:
    """WGS84 -> GCJ02（火星坐标系）。国外坐标原样返回。"""
    if _out_of_china(lng, lat):
        return lng, lat
    dlat = _transform_lat(lng - 105.0, lat - 35.0)
    dlng = _transform_lng(lng - 105.0, lat - 35.0)
    radlat = lat / 180.0 * math.pi
    magic = math.sin(radlat)
    magic = 1.0 - _GCJ_EE * magic * magic
    sqrtmagic = math.sqrt(magic)
    dlat = (dlat * 180.0) / ((_GCJ_A * (1.0 - _GCJ_EE)) / (magic * sqrtmagic) * math.pi)
    dlng = (dlng * 180.0) / (_GCJ_A / sqrtmagic * math.cos(radlat) * math.pi)
    return lng + dlng, lat + dlat


def gcj02_to_wgs84(lng: float, lat: float) -> tuple[float, float]:
    """GCJ02 -> WGS84（迭代求精，3 次迭代足够工程精度）。"""
    if _out_of_china(lng, lat):
        return lng, lat
    wgs_lng, wgs_lat = lng, lat
    for _ in range(3):
        g_lng, g_lat = wgs84_to_gcj02(wgs_lng, wgs_lat)
        wgs_lng += lng - g_lng
        wgs_lat += lat - g_lat
    return wgs_lng, wgs_lat


def gcj02_to_bd09ll(lng: float, lat: float) -> tuple[float, float]:
    """GCJ02 -> BD09LL（百度经纬度坐标）。"""
    z = math.sqrt(lng * lng + lat * lat) + 0.00002 * math.sin(lat * _BD_X_PI)
    theta = math.atan2(lat, lng) + 0.000003 * math.cos(lng * _BD_X_PI)
    bd_lng = z * math.cos(theta) + 0.0065
    bd_lat = z * math.sin(theta) + 0.006
    return bd_lng, bd_lat


def bd09ll_to_gcj02(lng: float, lat: float) -> tuple[float, float]:
    """BD09LL -> GCJ02。"""
    x = lng - 0.0065
    y = lat - 0.006
    z = math.sqrt(x * x + y * y) - 0.00002 * math.sin(y * _BD_X_PI)
    theta = math.atan2(y, x) - 0.000003 * math.cos(x * _BD_X_PI)
    return z * math.cos(theta), z * math.sin(theta)


def wgs84_to_bd09ll(lng: float, lat: float) -> tuple[float, float]:
    """WGS84 -> BD09LL（经 GCJ02 中转）。"""
    g_lng, g_lat = wgs84_to_gcj02(lng, lat)
    return gcj02_to_bd09ll(g_lng, g_lat)


def bd09ll_to_wgs84(lng: float, lat: float) -> tuple[float, float]:
    """BD09LL -> WGS84（经 GCJ02 中转）。"""
    g_lng, g_lat = bd09ll_to_gcj02(lng, lat)
    return gcj02_to_wgs84(g_lng, g_lat)


class LocalProjection:
    """以某中心点为原点的等距圆柱投影：lng/lat <-> 本地米坐标 (x=东, y=北)。

    用于建图、缓冲、面积等米制计算。小范围（数公里）内误差可忽略。
    """

    __slots__ = ("lat0", "lng0", "m_per_deg_lat", "m_per_deg_lng")

    def __init__(self, lng0: float, lat0: float) -> None:
        self.lng0 = float(lng0)
        self.lat0 = float(lat0)
        self.m_per_deg_lat = math.pi / 180.0 * EARTH_RADIUS_M
        self.m_per_deg_lng = math.pi / 180.0 * EARTH_RADIUS_M * math.cos(math.radians(lat0))

    def to_xy(self, lng: float, lat: float) -> tuple[float, float]:
        """WGS84 经纬度 -> 本地米坐标。"""
        return ((lng - self.lng0) * self.m_per_deg_lng, (lat - self.lat0) * self.m_per_deg_lat)

    def to_lnglat(self, x: float, y: float) -> tuple[float, float]:
        """本地米坐标 -> WGS84 经纬度。"""
        return (x / self.m_per_deg_lng + self.lng0, y / self.m_per_deg_lat + self.lat0)


def geohash5(lng: float, lat: float) -> str:
    """计算 5 位 geohash（约 4.9km x 4.9km 网格），用于缓存键。

    标准 base32 geohash 编码，自实现不依赖第三方库。
    """
    lat_lo, lat_hi = -90.0, 90.0
    lng_lo, lng_hi = -180.0, 180.0
    bits: list[int] = []
    even = True
    while len(bits) < 5 * 5:
        if even:
            mid = (lng_lo + lng_hi) / 2.0
            if lng >= mid:
                bits.append(1)
                lng_lo = mid
            else:
                bits.append(0)
                lng_hi = mid
        else:
            mid = (lat_lo + lat_hi) / 2.0
            if lat >= mid:
                bits.append(1)
                lat_lo = mid
            else:
                bits.append(0)
                lat_hi = mid
        even = not even
    chars: list[str] = []
    for i in range(0, len(bits), 5):
        value = 0
        for b in bits[i : i + 5]:
            value = (value << 1) | b
        chars.append(_GEOHASH_ALPHABET[value])
    return "".join(chars)
