"""测试夹具：全离线，无 AK（demo 路径），CACHE_DIR 指向临时目录。"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

API_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_DIR))

# 置空而非 pop：load_dotenv(override=False) 不会覆盖已存在的键，
# pop 会让仓库根 .env 的真实 AK 泄入测试进程
os.environ["BAIDU_SERVER_AK"] = ""
os.environ["DEMO_MODE"] = "false"
os.environ["CACHE_DIR"] = tempfile.mkdtemp(prefix="livemap-cache-test-")

import pytest
from app.config import load_settings
from app.demo_data import SAMPLES
from app.main import create_app
from fastapi.testclient import TestClient
from geo import wgs84_to_bd09ll

# 前端默认 pin（BD09LL）
DEFAULT_CENTER_BD = (116.4616, 39.8845)

# 劲松样例中心（BD09LL），与 SAMPLES 注册表一致
_bd = wgs84_to_bd09ll(*SAMPLES[0].center_wgs)
SAMPLE_CENTER_BD = (_bd[0], _bd[1])


def _post_checkup(
    client: TestClient,
    center: tuple[float, float],
    minutes: float | str,
    *,
    phase: str = "full",
    engine: str = "map-fabric",
    coord_type: str = "bd09ll",
    **extra: object,
) -> object:
    body: dict = {
        "center": {"lng": center[0], "lat": center[1], "coordType": coord_type},
        "minutes": minutes,
        "engine": engine,
        "includeOfficialIsochrone": False,
        "includeBlindWalk": True,
        "phase": phase,
    }
    body.update(extra)
    return client.post("/api/v1/checkup", json=body)


# 注意：本目录与 packages/geo/tests 各有 conftest.py，模块名 `conftest` 会冲突
# （pytest 9 默认 importlib 模式下后者覆盖前者），测试文件不得
# `from conftest import ...`，统一改用下列 fixture 注入。


@pytest.fixture(scope="session")
def post_checkup():
    """POST /api/v1/checkup 辅助函数（签名见 _post_checkup）。"""
    return _post_checkup


@pytest.fixture(scope="session")
def sample_center_bd() -> tuple[float, float]:
    return SAMPLE_CENTER_BD


@pytest.fixture(scope="session")
def default_center_bd() -> tuple[float, float]:
    return DEFAULT_CENTER_BD


@pytest.fixture(scope="session")
def settings():
    return load_settings()


@pytest.fixture(scope="session")
def client(settings):
    app = create_app(settings)
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session")
def ctx(client):
    return client.app.state.ctx
