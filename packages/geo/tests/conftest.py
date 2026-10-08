"""pytest 夹具：一条河 + 一座桥 + 河边住宅(blocked) + 河边商场(enterable)。

布局（本地米坐标，y 北）：
- 河：x∈[-600, 600]，y∈[-30, 30]（东西向，长到不可能在可达范围内绕行）；
- 桥：x=0 的南北路，桥段 y∈[-35, 35]（bridge=yes）；
- 北岸路 y=100、南岸路 y=-100、南岸第二条南北路 x=-120；
- 住宅(blocked)：北岸 (40,40)-(100,90)；
- 商场(enterable)：南岸 (-150,-95)-(-10,-45)「水岸商场」。
"""

from __future__ import annotations

import os
import tempfile

# 环境隔离：防止仓库根 .env 的真实 AK 经 load_dotenv 泄入测试
# （load_dotenv override=False 不覆盖已存在的键）
os.environ["BAIDU_SERVER_AK"] = ""
os.environ.setdefault("DEMO_MODE", "false")
os.environ.setdefault("CACHE_DIR", tempfile.mkdtemp(prefix="livemap-cache-geo-test-"))

import pytest
from geo.coords import LocalProjection
from geo.enterability import classify_buildings
from geo.graph import WalkGraph, build_graph
from shapely.geometry import LineString, box

CENTER = (116.4578, 39.8846)

RIVER = box(-600.0, -30.0, 600.0, 30.0)
RESIDENTIAL = box(40.0, 40.0, 100.0, 90.0)
MALL = box(-150.0, -95.0, -10.0, -45.0)

ROADS: list[tuple[LineString, dict]] = [
    (LineString([(0.0, 35.0), (0.0, 300.0)]), {"highway": "residential"}),
    (LineString([(0.0, -35.0), (0.0, 35.0)]), {"highway": "residential", "bridge": "yes"}),
    (LineString([(0.0, -300.0), (0.0, -35.0)]), {"highway": "residential"}),
    (LineString([(-200.0, 100.0), (200.0, 100.0)]), {"highway": "residential"}),
    (LineString([(-200.0, -100.0), (200.0, -100.0)]), {"highway": "residential"}),
    (LineString([(-120.0, -35.0), (-120.0, -300.0)]), {"highway": "residential"}),
]

BUILDING_FEATURES: list[tuple[object, dict]] = [
    (RESIDENTIAL, {"building": "apartments", "name": "河畔家园"}),
    (MALL, {"building": "retail", "shop": "mall", "name": "水岸商场"}),
]

WATER: list[tuple[object, dict]] = [(RIVER, {"natural": "water", "name": "通惠河"})]

# 注意：本目录与 apps/api/tests 各有 conftest.py，模块名 `conftest` 会冲突
# （pytest 9 默认 importlib 模式），测试文件不得 `from conftest import ...`，
# 统一改用 fixture 注入共享几何常量。


@pytest.fixture(scope="session")
def river_poly():
    return RIVER


@pytest.fixture(scope="session")
def residential_poly():
    return RESIDENTIAL


@pytest.fixture(scope="session")
def mall_poly():
    return MALL


@pytest.fixture(scope="session")
def river_proj() -> LocalProjection:
    return LocalProjection(*CENTER)


@pytest.fixture(scope="session")
def river_graph() -> WalkGraph:
    classified = classify_buildings(BUILDING_FEATURES)  # type: ignore[arg-type]
    return build_graph(ROADS, classified, WATER, [], CENTER, 1500.0)  # type: ignore[arg-type]


@pytest.fixture(scope="session")
def river_walls() -> list[object]:
    classified = classify_buildings(BUILDING_FEATURES)  # type: ignore[arg-type]
    return [b.wall for b in classified if b.wall is not None]


@pytest.fixture(scope="session")
def river_water() -> list[object]:
    return [RIVER]
