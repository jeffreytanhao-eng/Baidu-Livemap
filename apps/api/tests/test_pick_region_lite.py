"""pick_region 覆盖优先级：正式部署（有 AK）六环网格片优先 → 全量 patch（bbox）
→ 挂载 lite（兼容回退）→ 最近 patch 降级；demo 部署（无 AK/DEMO_MODE）保持
「patch bbox 优先」（demo 对照折线注入绑定 jinsong patch）。

TileManager 分支在 ctx.tiles=None（如测试 fake）时跳过，走 patch bbox 与挂载
lite 兼容回退；LRU/卸载行为在 test_tile_manager.py 覆盖。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from app.engine import RegionBundle, pick_region

# 瑞悦府一带：六环内、三个 patch bbox 外
RUIYUEFU = (116.4707, 40.0043)
# 六环 bbox 内但 ring6 多边形外（西北山地方向）
NW_MOUNTAIN = (116.10, 40.10)


@dataclass
class _Loaded:
    bbox_wgs84: tuple[float, float, float, float]


@dataclass
class _Service:
    _regions: dict


@dataclass
class _Ctx:
    service: _Service
    regions: dict
    tiles: object | None = None


def _bundle(name: str, center: tuple[float, float], lite: bool = False) -> RegionBundle:
    return RegionBundle(
        name=name,
        center_wgs=center,
        graph=None,  # pick_region 不触碰图
        walls=[],
        water=[],
        water_labels=[],
        barrier_labels=[],
        classified=[],
        enterable=[],
        lite=lite,
    )


def _ctx(tiles: object | None = None) -> _Ctx:
    patch_jinsong = _bundle("jinsong", (116.458, 39.8845))
    patch_zgc = _bundle("zhongguancun", (116.3165, 39.981))
    lite6r = _bundle("beijing6r", (116.385, 39.93), lite=True)
    bboxes = {
        "jinsong": (116.436, 39.868, 116.480, 39.901),
        "zhongguancun": (116.29, 39.96, 116.35, 40.01),
        # lite bbox 是六环外接矩形，比 ring6 多边形大（西北角超出）
        "beijing6r": (116.05, 39.68, 116.72, 40.18),
    }
    service = _Service({k: _Loaded(v) for k, v in bboxes.items()})
    return _Ctx(
        service=service,
        regions={"jinsong": patch_jinsong, "zhongguancun": patch_zgc, "beijing6r": lite6r},
        tiles=tiles,
    )


def _pick(lng: float, lat: int | float, tiles: object | None = None):
    return asyncio.run(pick_region(_ctx(tiles=tiles), lng, lat))


def test_tile_wins_over_patch_bbox():
    """有 TileManager 且切片存在：六环内点位优先网格片，不被 patch bbox 拦截。

    遗留样例 patch bbox 远大于其样例路网覆盖，bbox 优先会把覆盖缺口内的
    点位交给稀疏图（POI 大面积吸附失败），故切片必须优先。
    """

    class _FakeTiles:
        async def ensure(self, lng: float, lat: float):
            return _bundle("b6r_r06c10", (lng, lat))

    bundle, degraded = _pick(116.458, 39.884, tiles=_FakeTiles())  # 劲松内
    assert bundle.name == "b6r_r06c10"
    assert degraded is False


def test_tile_missing_falls_back_to_patch_bbox():
    """有 TileManager 但切片缺失：回退 patch bbox，不误降级。"""

    class _NoTiles:
        async def ensure(self, lng: float, lat: float):
            return None

    bundle, degraded = _pick(116.458, 39.884, tiles=_NoTiles())  # 劲松内
    assert bundle.name == "jinsong"
    assert degraded is False


def test_patch_bbox_wins_over_lite():
    """patch bbox 与六环同时覆盖：优先全量 patch。"""
    bundle, degraded = _pick(116.458, 39.884)  # 劲松内
    assert bundle.name == "jinsong"
    assert degraded is False


def test_ring6_interior_uses_lite():
    """六环内、patch 外：命中挂载的 lite（无 TileManager 的兼容回退）。"""
    bundle, degraded = _pick(*RUIYUEFU)
    assert bundle.name == "beijing6r"
    assert degraded is False


def test_lite_bbox_outside_ring6_falls_back_to_patch():
    """lite 外接矩形内但 ring6 多边形外（西北山地）：不命中，降级最近 patch。"""
    bundle, degraded = _pick(*NW_MOUNTAIN)
    assert bundle.lite is False
    assert degraded is True


def test_far_outside_falls_back_to_nearest_patch():
    """六环外远点（如上海）：降级最近全量 patch，lite 不参与兜底。"""
    bundle, degraded = _pick(121.47, 31.23)
    assert bundle.lite is False
    assert degraded is True
