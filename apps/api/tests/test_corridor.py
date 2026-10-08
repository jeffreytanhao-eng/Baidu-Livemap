"""Fix：六环外（degraded）点位用 8 方向百度步行路线走廊近似等时圈。

预置路网覆盖 = 三个全量 patch + beijing6r lite（六环内，无 buildings）。
六环内点位（如瑞悦府）现由 lite 层给出真实路网等时圈；六环外点位此前
会吸附到几公里外的最近区域节点，等时圈完全错位。现改为以请求中心为
原点的 8 方向 DirectionLite 路线走廊近似（真实路网、不能穿河穿快速路）。
"""

from __future__ import annotations

import pytest
from geo import haversine_m, wgs84_to_bd09ll

# 顺义城区：六环外（ring6 多边形外）、距三个 patch 均 >10km，必 degraded
SHUNYI_WGS = (116.6543, 40.1322)
SHUNYI_BD = wgs84_to_bd09ll(*SHUNYI_WGS)


class _FakeCorridorBaidu:
    """8 方向返回直线步行折线的假百度客户端（enabled=True）。"""

    enabled = True
    api_calls = 0

    async def place_search(self, query, center, radius):
        return []

    async def walking_matrix(self, origins, dests):
        return []

    async def walking_route(self, origin, dest):
        # BaiduClient 实际约定：origin/dest 为 (lng, lat) BD09 元组
        # （客户端内部拼 lat,lng 字符串）；返回直线折线，步长约 100m
        o_lng, o_lat = origin[0], origin[1]
        d_lng, d_lat = dest[0], dest[1]
        total = haversine_m(o_lng, o_lat, d_lng, d_lat)
        n = max(2, int(total / 100.0))
        pts = []
        for k in range(n + 1):
            t = k / n
            pts.append((o_lng + (d_lng - o_lng) * t, o_lat + (d_lat - o_lat) * t))
        path = ";".join(f"{lng:.6f},{lat:.6f}" for lng, lat in pts)
        return {"status": 0, "result": {"routes": [{"steps": [{"path": path}]}]}}


@pytest.fixture()
def corridor_client(client, monkeypatch):
    """demo 引擎（无 AK）+ 假百度客户端：强制走 degraded 走廊路径。"""
    ctx = client.app.state.ctx
    monkeypatch.setattr(ctx, "baidu", _FakeCorridorBaidu())
    return client


def _post(client, minutes=15, **extra):
    body = {
        "center": {"lng": SHUNYI_BD[0], "lat": SHUNYI_BD[1], "coordType": "bd09ll"},
        "minutes": minutes,
        "engine": "map-fabric",
        "includeOfficialIsochrone": False,
        "includeBlindWalk": True,
        "phase": "full",
    }
    body.update(extra)
    return client.post("/api/v1/checkup", json=body)


def test_degraded_center_uses_corridor_layers(corridor_client):
    """六环外点位：等时圈以请求中心为原点（走廊近似），不再错位到最近区域。"""
    r = _post(corridor_client)
    assert r.status_code == 200
    body = r.json()
    assert body["meta"]["degraded"] is True

    layers = body["layers"]
    assert [l["minutes"] for l in layers] == [5, 10, 15]
    areas = [l["areaM2"] for l in layers]
    assert all(a > 0 for a in areas)
    assert areas == sorted(areas), "层面积应随 minutes 递增"

    # 主层几何应以请求中心为圆心：顶点距中心不超过直线圆半径 + 缓冲余量
    main = next(l for l in layers if l["minutes"] == 15)
    flat: list[tuple[float, float]] = []

    def _walk(c):
        if isinstance(c[0], (int, float)):
            flat.append((c[0], c[1]))
        else:
            for sub in c:
                _walk(sub)

    _walk(main["polygon"]["geometry"]["coordinates"])
    from geo import bd09ll_to_wgs84

    max_dist = max(
        haversine_m(SHUNYI_WGS[0], SHUNYI_WGS[1], *bd09ll_to_wgs84(lng, lat))
        for lng, lat in flat
    )
    assert max_dist <= 1200.0 + 200.0, f"等时圈顶点距中心过远：{max_dist:.0f}m"

    assert body["enclaves"] == []
    assert any("走廊近似" in d for d in body["diagnosis"])
    assert len(body["sectorGaps"]) == 8
    # snap_note 说明走廊近似
    assert "走廊" in (body["meta"].get("snapNote") or "")


def test_corridor_fallback_when_routes_fail(client, monkeypatch):
    """路线全部失败：回退最近区域近似，不 500。"""
    ctx = client.app.state.ctx

    class _Dead:
        enabled = True
        api_calls = 0

        async def place_search(self, *a, **k):
            return []

        async def walking_matrix(self, *a, **k):
            return []

        async def walking_route(self, *a, **k):
            raise RuntimeError("network down")

    monkeypatch.setattr(ctx, "baidu", _Dead())
    r = _post(client, 20)  # 20 分钟档：避开上一用例 15 分钟的磁盘缓存
    assert r.status_code == 200
    body = r.json()
    assert body["meta"]["degraded"] is True
    assert body["layers"], "应回退到最近区域底图近似"
    assert not any("走廊近似" in d for d in body["diagnosis"])
