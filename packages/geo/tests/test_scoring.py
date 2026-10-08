"""打分层单测：分母随 X，禁止写死 15。"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from geo.scoring import coverage_score, passed, search_radius

CATEGORIES = {
    "convenience": 8.0,
    "leisure": 12.0,
    "mall": 18.0,
    "education": 6.0,
    "health": None,
    "public": 9.0,
    "transport": 14.0,
}
ANCHOR3 = [8.0, 6.0, None]  # convenience/health/education
ISO_AREA = 1_000_000.0
CIRCLE_AREA = 3_000_000.0


def test_search_radius() -> None:
    assert search_radius(5) == 1500
    assert search_radius(10) == 1500
    # 15 分钟档起取百度 place 检索半径上限（客户端本就 min(radius, 2000) 截断）
    assert search_radius(15) == 2000
    assert search_radius(20) == 2000
    assert search_radius(30) == 2000


def test_passed() -> None:
    assert passed(8.0, 10)
    assert not passed(12.0, 10)
    assert passed(12.0, 20)
    assert not passed(None, 20)
    assert not passed(float("inf"), 20)


def test_same_pois_different_minutes() -> None:
    """同一组 POI 耗时，minutes=10 与 minutes=20 的达标结果与分数必须不同。"""
    passed_10 = {k: passed(v, 10) for k, v in CATEGORIES.items()}
    passed_20 = {k: passed(v, 20) for k, v in CATEGORIES.items()}
    assert passed_10 != passed_20

    score_10 = coverage_score(CATEGORIES, ANCHOR3, ISO_AREA, CIRCLE_AREA, 10)
    score_20 = coverage_score(CATEGORIES, ANCHOR3, ISO_AREA, CIRCLE_AREA, 20)
    assert score_10 != score_20
    assert 0.0 <= score_10 <= 1.0
    assert 0.0 <= score_20 <= 1.0


def test_score_weights_and_bounds() -> None:
    # 全部达标且最近分钟为 0 + 面积比 1 + 地铁达标 -> 满分
    full = {k: 0.0 for k in CATEGORIES}
    assert (
        coverage_score(
            full, [0.0, 0.0, 0.0], CIRCLE_AREA, CIRCLE_AREA, 15, transport_component=1.0
        )
        == pytest.approx(1.0)
    )
    # 全缺 -> 只剩面积项（transport_component=0）
    none_cats = {k: None for k in CATEGORIES}
    s = coverage_score(none_cats, [None, None, None], 0.0, CIRCLE_AREA, 15)
    assert 0.0 <= s < 0.15


def test_transport_component() -> None:
    """交通专项分量：地铁 1.0 / 公交 0.6 / 缺 0，其余条件不变时分数单调。"""
    cats = {k: 0.0 for k in CATEGORIES}
    anchor = [0.0, 0.0, 0.0]
    metro = coverage_score(cats, anchor, ISO_AREA, CIRCLE_AREA, 15, transport_component=1.0)
    bus = coverage_score(cats, anchor, ISO_AREA, CIRCLE_AREA, 15, transport_component=0.6)
    none = coverage_score(cats, anchor, ISO_AREA, CIRCLE_AREA, 15, transport_component=0.0)
    assert metro > bus > none
    assert abs(metro - bus - (1.0 - 0.6) * 0.15) < 1e-9


def test_no_hardcoded_15_in_scoring() -> None:
    """grep 级别确认 scoring.py 无写死的 15。"""
    src = Path(__file__).resolve().parents[1] / "geo" / "scoring.py"
    text = src.read_text(encoding="utf-8")
    assert not re.search(r"(?<![\d.])15(?![\d.])", text), "scoring.py 出现写死的 15"
