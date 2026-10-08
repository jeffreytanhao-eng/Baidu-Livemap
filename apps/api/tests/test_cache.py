"""缓存：同参数第二次走缓存；磁盘键含中心坐标 JSON 文件存在；磁盘缓存独立于内存。"""

from __future__ import annotations

import pytest
from app.checkup import CACHE_SCHEMA, disk_cache_key

# 远离任何样例快照的中心（上海，WGS84）：demo 模式无快照可回，强制现场计算
_FAR_CENTER_WGS = (121.47, 31.23)


def test_disk_cache_file_written(client, ctx, post_checkup):
    r = post_checkup(client, _FAR_CENTER_WGS, 12, coord_type="wgs84")
    assert r.status_code == 200
    engine = f"{CACHE_SCHEMA}|map-fabric"  # 与 checkup 内 cache_engine 口径一致
    key = disk_cache_key(*_FAR_CENTER_WGS, 12, engine)
    expected_center = f"{round(_FAR_CENTER_WGS[0] * 1e5):d}_{round(_FAR_CENTER_WGS[1] * 1e5):d}"
    assert expected_center in key
    # DiskCache._path 会把非法字符（如 |）清洗为 _，测试侧做同样变换
    safe = "".join(c if (c.isalnum() or c in "-_") else "_" for c in key)
    path = ctx.settings.cache_dir / f"{safe}.json"
    assert path.exists(), f"缓存文件应存在: {path}"

    # 中心相差约 300m 必须换键（曾用 geohash5 导致跨点误命中同一份结果）
    assert disk_cache_key(121.4735, 31.23, 12, engine) != key

    # 第二次：清空内存缓存，并把 Dijkstra 打桩为爆炸 → 仍 200 即证明走了磁盘缓存
    ctx.node_results.clear()
    ctx.node_costs.clear()

    def _boom(*args, **kwargs):  # pragma: no cover - 被调用即失败
        raise AssertionError("cutoff_dijkstra 不应被调用（应命中磁盘缓存）")

    import app.checkup as checkup_mod

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(checkup_mod, "cutoff_dijkstra", _boom)
        r2 = post_checkup(client, _FAR_CENTER_WGS, 12, coord_type="wgs84")
    assert r2.status_code == 200
    assert r2.json()["meta"]["minutes"] == 12


def test_second_request_hits_cache_faster(client, post_checkup):
    center = (114.51, 38.04)  # 石家庄：无快照，现场算
    r1 = post_checkup(client, center, 14, coord_type="wgs84")
    r2 = post_checkup(client, center, 14, coord_type="wgs84")
    assert r1.status_code == r2.status_code == 200
    e1 = r1.json()["meta"]["elapsedMs"]
    e2 = r2.json()["meta"]["elapsedMs"]
    assert e2 < max(e1, 50), f"第二次应命中缓存更快: first={e1}ms second={e2}ms"
