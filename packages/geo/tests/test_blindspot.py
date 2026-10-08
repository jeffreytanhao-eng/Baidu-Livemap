"""1km 三类盲区单测：网格写死不随 X，增强开关才跟 X 走，4-连通聚合 + 走廊标记。"""

from __future__ import annotations

from geo.blindspot import BLINDSPOT_RADIUS_M, BlindspotPoi, blindspots
from geo.coords import LocalProjection

CENTER = (116.4578, 39.8846)
_PROJ = LocalProjection(*CENTER)


def _offset(x_m: float, y_m: float) -> tuple[float, float]:
    return _PROJ.to_lnglat(x_m, y_m)


def _strip_pois(convenience_walk: float | None = None) -> list[BlindspotPoi]:
    """构造恰好 y=0 一排格缺教育（东西向带状走廊）。

    教育设施密布在 y=±1001 两条线上：y=0 行每格距最近教育 >1000m（缺），
    其余行距最近教育 <=901m（不缺）。便利/医疗在中心，直线 1km 全覆盖。
    """
    pois = [
        BlindspotPoi("convenience", *_offset(0.0, 0.0), walk_minutes=convenience_walk),
        BlindspotPoi("health", *_offset(0.0, 0.0)),
    ]
    for s in range(-1500, 1501, 50):
        pois.append(BlindspotPoi("education", *_offset(float(s), 1001.0)))
        pois.append(BlindspotPoi("education", *_offset(float(s), -1001.0)))
    return pois


def test_blindspot_grid_fixed_not_scaled_by_minutes() -> None:
    """minutes=15 与 30 网格范围相同（不随 X），非增强模式结果完全一致。"""
    pois = _strip_pois()
    r15 = blindspots(CENTER, pois, minutes=15)
    r30 = blindspots(CENTER, pois, minutes=30)
    assert r15 == r30
    assert BLINDSPOT_RADIUS_M == 1000.0  # 写死 1km


def test_strip_corridor() -> None:
    pois = _strip_pois()
    result = blindspots(CENTER, pois, minutes=15)
    assert len(result["features"]) == 1, "一排缺格应 4-连通聚成 1 个区域"
    props = result["features"][0]["properties"]
    assert props["missing"] == ["education"]
    assert props["cells"] == 21, "恰好 y=0 一排 21 格"
    assert props["corridor"] is True, "长宽比 >3 的带状区域应标 corridor"


def test_enhanced_switch_follows_minutes() -> None:
    """增强开关：步行 >X 不可达才跟 X 走。"""
    pois = _strip_pois(convenience_walk=25.0)  # 便利设施步行 25 分钟
    base15 = blindspots(CENTER, pois, minutes=15, enhanced=False)
    base30 = blindspots(CENTER, pois, minutes=30, enhanced=False)
    enh15 = blindspots(CENTER, pois, minutes=15, enhanced=True)
    enh30 = blindspots(CENTER, pois, minutes=30, enhanced=True)

    assert base15 == base30, "非增强模式不随 X 变"
    assert enh15 != enh30, "增强模式下 X 改变才影响不可达判定"
    # 增强 + 15 分钟：便利 25>15 全区缺便利
    enh15_missing = set()
    for feat in enh15["features"]:
        enh15_missing.update(feat["properties"]["missing"])
    assert "convenience" in enh15_missing
    # 增强 + 30 分钟：便利 25<=30 可达，只剩教育走廊
    enh30_missing = set()
    for feat in enh30["features"]:
        enh30_missing.update(feat["properties"]["missing"])
    assert "convenience" not in enh30_missing
    assert enh30 == base30, "增强 30 分钟时便利可达，结果应退回直线口径"


def test_no_blindspot_when_covered() -> None:
    pois = [
        BlindspotPoi("convenience", *_offset(0.0, 0.0)),
        BlindspotPoi("health", *_offset(0.0, 0.0)),
        BlindspotPoi("education", *_offset(0.0, 0.0)),
    ]
    result = blindspots(CENTER, pois, minutes=15)
    assert result["features"] == []


def test_categories_subset_filter() -> None:
    """单类/复合筛选：只算所选类的 missing。"""
    # 三类全缺的布局（1km 内无任何 POI）
    pois = [BlindspotPoi("convenience", *_offset(5000.0, 5000.0))]
    full = blindspots(CENTER, pois, minutes=15)
    full_missing = set()
    for feat in full["features"]:
        full_missing.update(feat["properties"]["missing"])
    assert full_missing == {"convenience", "health", "education"}

    only_convenience = blindspots(CENTER, pois, minutes=15, categories=("convenience",))
    assert only_convenience["features"], "单类筛选仍应产出灰块"
    for feat in only_convenience["features"]:
        assert feat["properties"]["missing"] == ["convenience"]

    subset = blindspots(CENTER, pois, minutes=15, categories=("convenience", "health"))
    for feat in subset["features"]:
        assert set(feat["properties"]["missing"]) <= {"convenience", "health"}

    empty = blindspots(CENTER, pois, minutes=15, categories=())
    assert empty["features"] == [], "空子集不算任何盲区"
