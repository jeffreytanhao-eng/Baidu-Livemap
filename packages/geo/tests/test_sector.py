"""8 方位扇区缺口单测。"""

from __future__ import annotations

import math

from geo.sector import (
    circle_radius_m,
    compute_sector_gaps,
    diagnose_worst_sector,
    sector_gap_report,
)
from shapely.geometry import Point, Polygon

MINUTES = 15


def _circle(r: float) -> Polygon:
    return Point(0.0, 0.0).buffer(r, quad_segs=256)


def test_full_circle_gaps_near_one() -> None:
    radius = circle_radius_m(MINUTES)
    assert radius == 80.0 * MINUTES
    geom = _circle(radius)
    gaps = compute_sector_gaps(geom, (0.0, 0.0), MINUTES)
    assert len(gaps) == 8
    for gap in gaps:
        assert gap.gapRatio > 0.98, f"{gap.direction} 正圆时 gapRatio 应≈1"
        assert gap.circleArea > 0


def test_cut_se_quadrant() -> None:
    """人为切掉东南 90°（270°..360°），SE 向 gapRatio 必须明显下降。"""
    radius = circle_radius_m(MINUTES)
    disk = _circle(radius)
    cutter = Polygon(
        [(0.0, 0.0), (radius * 2, 0.0), (radius * 2, -radius * 2), (0.0, -radius * 2)]
    )
    geom = disk.difference(cutter)
    gaps = {g.direction: g for g in compute_sector_gaps(geom, (0.0, 0.0), MINUTES)}
    assert gaps["N"].gapRatio > 0.98
    assert gaps["SE"].gapRatio < 0.1, "SE 被切掉后 gapRatio 应≈0"
    assert gaps["SE"].gapRatio < gaps["N"].gapRatio - 0.8


def test_diagnose_names_barrier() -> None:
    radius = circle_radius_m(MINUTES)
    disk = _circle(radius)
    cutter = Polygon(
        [(0.0, 0.0), (radius * 2, 0.0), (radius * 2, -radius * 2), (0.0, -radius * 2)]
    )
    geom = disk.difference(cutter)
    report = sector_gap_report(
        geom, (0.0, 0.0), MINUTES, barrier_labels={"SE": ["通惠河", "快速路"]}
    )
    assert report.worst_direction == "SE"
    assert "东南" in report.diagnosis
    assert "通惠河" in report.diagnosis and "快速路" in report.diagnosis
    assert "绕桥" in report.diagnosis  # 含水域 -> 需绕桥


def test_diagnose_balanced() -> None:
    gaps = compute_sector_gaps(_circle(circle_radius_m(MINUTES)), (0.0, 0.0), MINUTES)
    worst, sentence = diagnose_worst_sector(gaps, None)
    assert worst is not None
    assert "均衡" in sentence


def test_sector_areas_sum() -> None:
    radius = circle_radius_m(MINUTES)
    gaps = compute_sector_gaps(_circle(radius), (0.0, 0.0), MINUTES)
    total_circle = sum(g.circleArea for g in gaps)
    assert math.isclose(total_circle, math.pi * radius * radius, rel_tol=0.02)
