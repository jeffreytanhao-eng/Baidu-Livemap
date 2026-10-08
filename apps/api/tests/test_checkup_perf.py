"""HTTP 层性能冒烟：demo 模式非精确档分钟回退快照，响应仍是毫秒级。

现场计算路径的性能与正确性见 test_engine_live.py（allow_snapshot=False）。
"""

from __future__ import annotations

import time


def test_fast_under_300ms(client, post_checkup, sample_center_bd):
    t0 = time.perf_counter()
    r = post_checkup(client, sample_center_bd, 13, phase="fast")
    wall_ms = (time.perf_counter() - t0) * 1000
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"meta", "fastLayer"}
    elapsed = body["meta"]["elapsedMs"]
    print(f"\n[perf] phase=fast elapsedMs={elapsed} wall={wall_ms:.0f}ms")
    assert elapsed < 300
    assert wall_ms < 1000  # 含 TestClient HTTP 开销的兜底


def test_full_under_3s(client, post_checkup, sample_center_bd):
    t0 = time.perf_counter()
    r = post_checkup(client, sample_center_bd, 13, phase="full")
    wall_ms = (time.perf_counter() - t0) * 1000
    assert r.status_code == 200
    elapsed = r.json()["meta"]["elapsedMs"]
    print(f"\n[perf] phase=full elapsedMs={elapsed} wall={wall_ms:.0f}ms (目标 <1500ms)")
    assert elapsed < 3000
