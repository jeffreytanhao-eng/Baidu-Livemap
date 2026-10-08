"""POI 覆盖打分：纯函数，分母随入参 minutes，禁止写死任何档位分钟数。

七类体系权重（W_* 常量）：
- 0.40 七类达标比例（娱乐休闲/大型商超天然稀疏，各≈5.7% 即「小权重」）；
- 0.20 锚点三类（便利生活/医疗保障/学习教育）达标比例；
- 0.15 锚点三类平均最近步行分钟线性衰减；
- 0.15 交通出行专项（地铁一票制 1.0 / 仅公交达标 0.6 / 均不达标 0）；
- 0.10 等时圈面积比。
"""

from __future__ import annotations

import math

W_COVERAGE = 0.40
W_ANCHOR = 0.20
W_NEAREST = 0.15
W_TRANSPORT = 0.15
W_AREA = 0.10

# 交通出行「公交兜底」的折算系数（地铁一票制记满分）
TRANSPORT_BUS_FACTOR = 0.6


def search_radius(minutes: int) -> int:
    """POI 检索半径（米）。

    召回半径随档位线性增长（135 = 90 m/min 步行速度 × 1.5 放大系数），
    最大档触达百度 place 检索半径上限 2000：等时圈由 OSM 路网画出，
    直线可达 ~1.3km，而百度 POI 标签常落在设施内部（如青甸湖公园标签距
    中心 1.8km），1500 召回半径会把圈内可达的真实设施漏掉。
    """
    return max(1500, min(2000, 135 * minutes))


def passed(nearest_walk_min: float | None, minutes: int) -> bool:
    """某类 POI 是否达标：最近步行分钟 <= 当前 X。"""
    if nearest_walk_min is None or math.isinf(nearest_walk_min):
        return False
    return nearest_walk_min <= minutes


def _mean_nearest(nearest_minutes: list[float | None], fallback: float) -> float:
    values = [fallback if (v is None or math.isinf(v)) else v for v in nearest_minutes]
    if not values:
        return fallback
    return sum(values) / len(values)


def coverage_score(
    categories: dict[str, float | None],
    anchor_nearest_minutes: list[float | None],
    isochrone_area: float,
    circle_area: float,
    minutes: int,
    *,
    transport_component: float = 0.0,
) -> float:
    """综合分。

    - ``categories``：7 类 POI -> 最近步行分钟（None 表示缺失）；
    - ``anchor_nearest_minutes``：锚点三类（便利生活/医疗保障/学习教育）最近步行分钟；
    - ``transport_component``：交通出行专项分量（1.0 地铁达标 / 0.6 仅公交达标 / 0 缺口）；
    - 分母一律使用入参 ``minutes``。
    """
    if minutes <= 0:
        raise ValueError("minutes must be positive")

    total = len(categories)
    coverage = (
        sum(1 for v in categories.values() if passed(v, minutes)) / total if total else 0.0
    )
    anchor_total = len(anchor_nearest_minutes)
    anchor = (
        sum(1 for v in anchor_nearest_minutes if passed(v, minutes)) / anchor_total
        if anchor_total
        else 0.0
    )
    mean_nearest = _mean_nearest(anchor_nearest_minutes, fallback=float(minutes))
    proximity = 1.0 - min(1.0, mean_nearest / minutes)
    area_ratio = 0.0 if circle_area <= 0 else min(1.0, isochrone_area / circle_area)
    transport = min(1.0, max(0.0, transport_component))

    return (
        W_COVERAGE * coverage
        + W_ANCHOR * anchor
        + W_NEAREST * proximity
        + W_TRANSPORT * transport
        + W_AREA * area_ratio
    )
