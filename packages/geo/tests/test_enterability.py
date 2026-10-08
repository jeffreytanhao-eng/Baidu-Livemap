"""建筑可进入性分类单测。"""

from __future__ import annotations

from geo.enterability import (
    CATEGORY_BLOCKED,
    CATEGORY_ENTERABLE,
    CATEGORY_OPEN,
    CATEGORY_PODIUM,
    WALL_BUFFER_M,
    classify_building,
    classify_buildings,
    podium_corridor_ring,
)
from shapely.geometry import box


def test_enterable_by_tags() -> None:
    assert classify_building({"shop": "mall", "building": "retail"}) == CATEGORY_ENTERABLE
    assert classify_building({"building": "retail"}) == CATEGORY_ENTERABLE
    assert classify_building({"shop": "supermarket"}) == CATEGORY_ENTERABLE
    assert classify_building({"amenity": "marketplace"}) == CATEGORY_ENTERABLE


def test_enterable_by_name_or_poi() -> None:
    assert classify_building({"building": "yes", "name": "劲松购物中心"}) == CATEGORY_ENTERABLE
    assert classify_building({"building": "yes", "name": "幸福超市"}) == CATEGORY_ENTERABLE
    assert classify_building({"building": "yes", "pois": ["便民菜市场"]}) == CATEGORY_ENTERABLE
    assert classify_building({"building": "yes", "name": "XX商场XX"}) == CATEGORY_ENTERABLE


def test_podium_office_with_ground_retail() -> None:
    assert (
        classify_building({"building": "office", "podium_pois": ["咖啡店"]}) == CATEGORY_PODIUM
    )
    assert (
        classify_building({"building": "office", "ground_floor_retail": "yes"})
        == CATEGORY_PODIUM
    )


def test_blocked_defaults() -> None:
    assert classify_building({"building": "apartments"}) == CATEGORY_BLOCKED
    assert classify_building({"building": "residential"}) == CATEGORY_BLOCKED
    assert classify_building({"building": "office"}) == CATEGORY_BLOCKED  # 无底商
    assert classify_building({"building": "yes"}) == CATEGORY_BLOCKED  # 未知默认保守
    assert classify_building({}) == CATEGORY_BLOCKED


def test_open_ground() -> None:
    assert classify_building({"leisure": "park", "name": "口袋公园"}) == CATEGORY_OPEN
    assert classify_building({"highway": "pedestrian"}) == CATEGORY_OPEN


def test_blocked_wall_buffer() -> None:
    building = box(0.0, 0.0, 40.0, 40.0)
    (result,) = classify_buildings([(building, {"building": "apartments"})])
    assert result.category == CATEGORY_BLOCKED
    assert result.wall is not None
    # 墙厚区 = 外扩 1.5m，包含本体且面积更大
    assert result.wall.covers(building)
    assert result.wall.area > building.area
    # 外扩约 1.5m：质心 (20,20) 到墙边界距离 = 半边长 20 + 墙厚 1.5
    assert abs(result.wall.boundary.distance(building.centroid) - (20.0 + WALL_BUFFER_M)) < 0.01
    # enterable 不生成墙
    (mall,) = classify_buildings([(building, {"shop": "mall"})])
    assert mall.wall is None


def test_podium_corridor_ring() -> None:
    building = box(0.0, 0.0, 80.0, 60.0)
    ring = podium_corridor_ring(building)
    assert ring is not None
    assert ring.area > 0.0
    # 环带在建筑内，且贴着外墙内侧
    assert building.covers(ring)
    # 太小的建筑没有 8m 进深，返回 None
    tiny = box(0.0, 0.0, 10.0, 10.0)
    assert podium_corridor_ring(tiny) is None
