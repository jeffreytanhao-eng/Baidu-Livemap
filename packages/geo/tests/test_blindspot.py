"""服务盲区单测：题面设施组判缺（名称收窄）、等时圈域、4-连通聚合 + 走廊标记。"""

from __future__ import annotations

from geo.blindspot import (
    BLINDSPOT_RADIUS_M,
    BlindspotPoi,
    blind_group,
    blindspots,
)
from geo.coords import LocalProjection

CENTER = (116.4578, 39.8846)
_PROJ = LocalProjection(*CENTER)


def _offset(x_m: float, y_m: float) -> tuple[float, float]:
    return _PROJ.to_lnglat(x_m, y_m)


def _strip_pois() -> list[BlindspotPoi]:
    """构造恰好 y=0 一排格缺小学（东西向带状走廊）。

    小学密布在 y=±1001 两条线上：y=0 行每格距最近小学 >1000m（缺），
    其余行距最近小学 <=901m（不缺）。菜市场/药店在中心，直线 1km 全覆盖。
    """
    pois = [
        BlindspotPoi("convenience", *_offset(0.0, 0.0), name="物美超市"),
        BlindspotPoi("health", *_offset(0.0, 0.0), name="康民大药房"),
    ]
    for s in range(-1500, 1501, 50):
        pois.append(
            BlindspotPoi("education", *_offset(float(s), 1001.0), name="试验小学")
        )
        pois.append(
            BlindspotPoi("education", *_offset(float(s), -1001.0), name="试验小学")
        )
    return pois


def _missing_set(result: dict) -> set[str]:
    return {c for f in result["features"] for c in f["properties"]["missing"]}


def test_radius_fixed() -> None:
    """判缺半径写死 1km，不随 X 缩放。"""
    assert BLINDSPOT_RADIUS_M == 1000.0


def test_group_matching() -> None:
    """题面组归类：名称收窄，便利店/养老院/中学/幼儿园不构成覆盖。"""
    assert blind_group("convenience", "XX菜市场") == "market"
    assert blind_group("mall", "物美超市") == "market"
    assert blind_group("health", "康民大药房") == "pharmacy"
    assert blind_group("health", "市人民医院") == "pharmacy"
    assert blind_group("health", "社区卫生服务中心") == "pharmacy"
    assert blind_group("education", "劲松第一小学") == "school"
    # 非题面设施：不算任何组
    assert blind_group("convenience", "全家便利店") is None
    assert blind_group("health", "夕阳红养老院") is None
    assert blind_group("education", "劲松中学") is None
    assert blind_group("education", "实验幼儿园") is None
    assert blind_group("education", None) is None


def test_narrow_pools_do_not_cover() -> None:
    """宽口径的「兜底设施」不再覆盖：有便利店但无超市 → 便利缺。"""
    base = [
        BlindspotPoi("health", *_offset(0.0, 0.0), name="康民大药房"),
        BlindspotPoi("education", *_offset(0.0, 0.0), name="试验小学"),
    ]
    r = blindspots(
        CENTER,
        [*base, BlindspotPoi("convenience", *_offset(0.0, 0.0), name="全家便利店")],
    )
    assert _missing_set(r) == {"convenience"}

    # mall 类超市可补位 market 组
    r2 = blindspots(
        CENTER,
        [*base, BlindspotPoi("mall", *_offset(0.0, 0.0), name="永辉超市")],
    )
    assert r2["features"] == []

    # health 只有养老院、education 只有中学 → 两组均缺
    r3 = blindspots(
        CENTER,
        [
            BlindspotPoi("convenience", *_offset(0.0, 0.0), name="物美超市"),
            BlindspotPoi("health", *_offset(0.0, 0.0), name="夕阳红养老院"),
            BlindspotPoi("education", *_offset(0.0, 0.0), name="劲松中学"),
        ],
    )
    assert _missing_set(r3) == {"health", "education"}

    # 无名称 POI 不参与判缺
    r4 = blindspots(CENTER, [BlindspotPoi("convenience", *_offset(0.0, 0.0))])
    assert "convenience" in _missing_set(r4)


def test_strip_corridor() -> None:
    pois = _strip_pois()
    result = blindspots(CENTER, pois)
    assert len(result["features"]) == 1, "一排缺格应 4-连通聚成 1 个区域"
    props = result["features"][0]["properties"]
    assert props["missing"] == ["education"]
    assert props["cells"] == 21, "恰好 y=0 一排 21 格"
    assert props["corridor"] is True, "长宽比 >3 的带状区域应标 corridor"


def test_no_blindspot_when_covered() -> None:
    pois = [
        BlindspotPoi("convenience", *_offset(0.0, 0.0), name="物美超市"),
        BlindspotPoi("health", *_offset(0.0, 0.0), name="康民大药房"),
        BlindspotPoi("education", *_offset(0.0, 0.0), name="试验小学"),
    ]
    result = blindspots(CENTER, pois)
    assert result["features"] == []


def test_categories_subset_filter() -> None:
    """单类/复合筛选：只算所选类的 missing；空子集不算任何盲区。"""
    # 三类全缺的布局（1km 内无题面设施）
    pois = [
        BlindspotPoi("convenience", *_offset(5000.0, 5000.0), name="远处超市")
    ]
    full = blindspots(CENTER, pois)
    assert _missing_set(full) == {"convenience", "health", "education"}

    only_convenience = blindspots(CENTER, pois, categories=("convenience",))
    assert only_convenience["features"], "单类筛选仍应产出盲区面"
    for feat in only_convenience["features"]:
        assert feat["properties"]["missing"] == ["convenience"]

    subset = blindspots(CENTER, pois, categories=("convenience", "health"))
    for feat in subset["features"]:
        assert set(feat["properties"]["missing"]) <= {"convenience", "health"}

    empty = blindspots(CENTER, pois, categories=())
    assert empty["features"] == [], "空子集不算任何盲区"


def test_domain_restricts_grid() -> None:
    """判定域为等时圈：域内格心逐格判缺，域外格不产出。"""
    # 全缺布局：唯一 POI 在远处，域内外都缺
    pois = [BlindspotPoi("convenience", *_offset(5000.0, 5000.0), name="远处超市")]
    # 等时圈域：本地米空间 x∈[250,750], y∈[-50,50] 的矩形（WGS84 GeoJSON）
    ring = [
        _offset(250.0, -50.0),
        _offset(750.0, -50.0),
        _offset(750.0, 50.0),
        _offset(250.0, 50.0),
        _offset(250.0, -50.0),
    ]
    domain = {"type": "Polygon", "coordinates": [ring]}
    result = blindspots(CENTER, pois, domain=domain)
    # 全缺布局：三类各自聚合成 1 个面，每面恰含域内 5 格
    assert len(result["features"]) == 3
    assert {f["properties"]["missing"][0] for f in result["features"]} == {
        "convenience",
        "health",
        "education",
    }
    for feat in result["features"]:
        assert feat["properties"]["cells"] == 5, "格心 (300..700, 0) 共 5 格落在域内"

    # 无域退化为中心 1km 圆：同一布局缺格远多于域内 5 格
    full = blindspots(CENTER, pois)
    assert sum(f["properties"]["cells"] for f in full["features"]) > 5
