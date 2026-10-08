"""Fix：百度 place_search 失败（如 AK 天配额超限 302）降级合成 POI 时，
结果不得写入结果缓存（内存 + 磁盘）——否则配额恢复后同中心同档位请求
会命中被污染的合成数据，直到 24h TTL 过期。"""

from __future__ import annotations

from app.baidu import BaiduError
from app.cache import disk_cache_key


class _QuotaDeadBaidu:
    """AK 天配额超限：所有接口一律抛 302 BaiduError。"""

    enabled = True
    api_calls = 0

    async def place_search(self, query, center, radius):
        raise BaiduError("baidu api status=302 天配额超限，限制访问")

    async def walking_matrix(self, origins, dests):
        raise BaiduError("baidu api status=302 天配额超限，限制访问")

    async def walking_route(self, origin, dest):
        raise BaiduError("baidu api status=302 天配额超限，限制访问")


def test_quota_dead_pois_not_cached(client, monkeypatch, sample_center_bd):
    """配额超限：响应正常返回（合成降级）但缓存不落盘，次日配额恢复即真实召回。"""
    ctx = client.app.state.ctx
    # session 级 ctx 可能被前序测试写入过同中心同档位缓存：先清干净，
    # 确保本次请求真正走到 place_search 降级路径，而不是命中已有缓存。
    ctx.node_results.clear()
    for stale in ctx.disk.root.glob("*_15_cat12_*"):
        stale.unlink(missing_ok=True)
    monkeypatch.setattr(ctx, "baidu", _QuotaDeadBaidu())
    body = {
        "center": {"lng": sample_center_bd[0], "lat": sample_center_bd[1], "coordType": "bd09ll"},
        "minutes": "15",
        "engine": "map-fabric",
        "includeOfficialIsochrone": False,
        "includeBlindWalk": True,
        "phase": "full",
    }
    r = client.post("/api/v1/checkup", json=body)
    assert r.status_code == 200
    body_json = r.json()
    # 非区域降级：POI 检索降级不影响 degraded 字段，但结果不可信
    assert body_json["meta"]["degraded"] is False
    # 缓存键与正常请求完全同键——必须无缓存条目
    center_wgs = __import__("geo").bd09ll_to_wgs84(*sample_center_bd)
    key = disk_cache_key(center_wgs[0], center_wgs[1], 15, "cat12|map-fabric")
    assert ctx.disk.get(key) is None
    assert not list(ctx.disk.root.glob("*_15_cat12_*"))
