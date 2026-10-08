"""建筑可进入性分类（Enterability）。

四类：
- ``enterable`` 可进入（商场/零售/含商场超市菜场 POI），内部按 1.1x 步行速度穿行；
- ``podium``   底商可进楼上不可（办公 + 贴边底商 POI），只生成沿外墙内侧 8m 廊道；
- ``blocked``  不可进入（住宅/无底商办公/未知建筑，多边形即墙），外扩 1.5m 墙厚；
- ``open``     开放场地（有 highway/foot 标签的公园广场），按路网处理，不是墙。

未知建筑默认保守归为 ``blocked``。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry

CATEGORY_ENTERABLE = "enterable"
CATEGORY_PODIUM = "podium"
CATEGORY_BLOCKED = "blocked"
CATEGORY_OPEN = "open"

WALL_BUFFER_M = 1.5
PODIUM_CORRIDOR_OFFSET_M = 8.0
ENTERABLE_SPEED_FACTOR = 1.1

ENTERABLE_NAME_KEYWORDS = ("商场", "购物中心", "超市", "菜市场", "shopping", "mall")

_OPEN_LEISURE_TAGS = {"park", "garden", "playground", "pitch", "common"}
_OPEN_HIGHWAY_TAGS = {"pedestrian", "footway", "path", "steps", "living_street"}


@dataclass(slots=True)
class ClassifiedBuilding:
    """分类后的建筑（几何为本地米坐标系下的 shapely 对象）。"""

    geometry: BaseGeometry
    category: str
    properties: dict = field(default_factory=dict)
    wall: BaseGeometry | None = None  # 仅 blocked：多边形外扩 1.5m 的墙厚区


def _name_text(properties: dict) -> str:
    parts: list[str] = [str(properties.get("name") or "")]
    for key in ("pois", "poi_names"):
        values = properties.get(key)
        if isinstance(values, list):
            parts.extend(str(v) for v in values)
        elif values:
            parts.append(str(values))
    return " ".join(parts).lower()


def _has_podium_poi(properties: dict) -> bool:
    """办公楼主楼下的底商 POI 标记（合成数据/预处理管线注入）。"""
    pois = properties.get("podium_pois")
    if isinstance(pois, (list, tuple)) and len(pois) > 0:
        return True
    if properties.get("ground_floor_retail") in ("yes", "true", True, 1):
        return True
    retail = properties.get("retail:ground_floor")
    return retail in ("yes", "true", True, 1)


def classify_building(properties: dict) -> str:
    """按 OSM 标签/名称/POI 判定单个建筑的可进入性类别。"""
    shop = str(properties.get("shop") or "")
    building = str(properties.get("building") or "")
    leisure = str(properties.get("leisure") or "")
    highway = str(properties.get("highway") or "")
    amenity = str(properties.get("amenity") or "")

    # 开放场地：公园/广场等带 highway/foot 标签的面，按路网处理
    if leisure in _OPEN_LEISURE_TAGS or highway in _OPEN_HIGHWAY_TAGS:
        return CATEGORY_OPEN

    # 可进入：商场/零售，或名称/POI 命中关键词
    if shop == "mall" or building == "retail" or shop in ("supermarket", "department_store"):
        return CATEGORY_ENTERABLE
    if amenity == "marketplace":
        return CATEGORY_ENTERABLE
    name = _name_text(properties)
    if any(kw in name for kw in ENTERABLE_NAME_KEYWORDS):
        return CATEGORY_ENTERABLE

    # 底商办公：只走外墙内侧廊道
    if building == "office" and _has_podium_poi(properties):
        return CATEGORY_PODIUM

    # 住宅、无底商办公、未知建筑：默认保守不可穿
    return CATEGORY_BLOCKED


def classify_buildings(
    features: list[tuple[BaseGeometry, dict]],
) -> list[ClassifiedBuilding]:
    """批量分类建筑。

    参数 ``features`` 为 ``(米投影几何, properties)`` 列表；
    blocked 建筑自动外扩 1.5m 生成墙厚区 ``wall``。
    """
    out: list[ClassifiedBuilding] = []
    for geom, props in features:
        category = classify_building(props)
        wall: BaseGeometry | None = None
        if category == CATEGORY_BLOCKED:
            wall = geom.buffer(WALL_BUFFER_M, quad_segs=8)
        out.append(
            ClassifiedBuilding(geometry=geom, category=category, properties=dict(props), wall=wall)
        )
    return out


def podium_corridor_ring(geom: BaseGeometry) -> Polygon | None:
    """生成 podium 建筑沿外墙内侧 8m 的廊道环带（米投影下计算）。

    返回轮廓向内缓冲 8m 与外墙之间的环带面；面太小（<8m 进深）时返回 None。
    """
    if geom.is_empty:
        return None
    inner = geom.buffer(-PODIUM_CORRIDOR_OFFSET_M, quad_segs=8)
    if inner.is_empty:
        return None
    ring = geom.difference(inner)
    if ring.is_empty:
        return None
    return ring  # type: ignore[return-value]
