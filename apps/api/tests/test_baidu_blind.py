"""Fix 1/Fix 3：full 响应 baiduRoutes（demo 也有）+ 保守诊断句 + blindFilter 筛选。"""

from __future__ import annotations

from geo import wgs84_to_bd09ll
from geo.coords import LocalProjection

JINSONG_CENTER = (116.4578, 39.8846)


def _jinsong_live_center_bd() -> tuple[float, float]:
    """劲松区域内、距所有样例中心 >150m 的点（走现场计算而非快照）。

    本地坐标 (1200, 1000)：距劲松样例中心 (-800,-800) 约 2691m。
    """
    proj = LocalProjection(*JINSONG_CENTER)
    lng, lat = proj.to_lnglat(1200.0, 1000.0)
    return wgs84_to_bd09ll(lng, lat)


def _post(client, center_bd, minutes=15, **extra):
    body = {
        "center": {"lng": center_bd[0], "lat": center_bd[1], "coordType": "bd09ll"},
        "minutes": minutes,
        "engine": "demo",
        "includeOfficialIsochrone": False,
        "includeBlindWalk": True,
        "phase": "full",
    }
    body.update(extra)
    return client.post("/api/v1/checkup", json=body)


def test_full_response_has_baidu_routes_demo(client):
    """demo 模式现场计算：响应含 baiduRoutes（真实预生成折线注入）且诊断含保守说明。"""
    r = _post(client, _jinsong_live_center_bd())
    assert r.status_code == 200
    body = r.json()
    assert body["meta"]["demoMode"] is True

    routes = body["baiduRoutes"]
    assert len(routes) == 2, "demo 劲松应注入 2 条对照折线"
    names = {rt["name"] for rt in routes}
    assert "劲松中心→劲松地铁站" in names
    assert "劲松中心→首都图书馆" in names
    for rt in routes:
        assert rt["injectedEdges"] >= 1
        assert len(rt["path"]) >= 2
        for lng, lat in rt["path"]:
            assert 116.3 < lng < 116.6 and 39.8 < lat < 40.0  # BD09LL 劲松范围

    assert any("更保守" in d and "采样路网" in d for d in body["diagnosis"]), (
        "诊断应含「沿采样路网等时计算，边界更保守」说明"
    )


def test_snapshot_response_has_baidu_routes(client, post_checkup, sample_center_bd):
    """demo 快照路径：预建快照同样带 baiduRoutes 与保守诊断句。"""
    r = post_checkup(client, sample_center_bd, 15)
    assert r.status_code == 200
    body = r.json()
    assert body["meta"]["demoMode"] is True
    assert len(body["baiduRoutes"]) == 2
    assert all(rt["injectedEdges"] >= 1 for rt in body["baiduRoutes"])
    assert any("更保守" in d for d in body["diagnosis"])


def test_blind_filter_convenience_only(client):
    """blindFilter=['convenience']：missing 只含 convenience，其余类占比为 0。"""
    r = _post(client, _jinsong_live_center_bd(), blindFilter=["convenience"])
    assert r.status_code == 200
    body = r.json()
    for feat in body["blindSpots"]["features"]:
        assert set(feat["properties"]["missing"]) <= {"convenience"}
    summary = body["blindSummary"]
    assert summary["healthPct"] == 0.0
    assert summary["educationPct"] == 0.0
    assert summary["conveniencePct"] > 0.0


def test_blind_filter_empty_means_no_blindspots(client):
    r = _post(client, _jinsong_live_center_bd(), blindFilter=[])
    assert r.status_code == 200
    body = r.json()
    assert body["blindSpots"]["features"] == []
    assert body["blindSummary"] == {
        "conveniencePct": 0.0,
        "healthPct": 0.0,
        "educationPct": 0.0,
    }


def test_blind_filter_invalid_400(client):
    r = _post(client, _jinsong_live_center_bd(), blindFilter=["hospital"])
    assert r.status_code == 400


def test_blind_filter_on_snapshot_path(client, post_checkup, sample_center_bd):
    """demo 快照路径同样响应 blindFilter：只重算盲区层，其余字段沿用快照。"""
    r = post_checkup(client, sample_center_bd, 15, blindFilter=["convenience"])
    assert r.status_code == 200
    body = r.json()
    assert body["meta"]["demoMode"] is True
    for feat in body["blindSpots"]["features"]:
        assert set(feat["properties"]["missing"]) <= {"convenience"}
    summary = body["blindSummary"]
    assert summary["healthPct"] == 0.0
    assert summary["educationPct"] == 0.0
    assert summary["conveniencePct"] > 0.0
    # 快照其余字段（如综合分）不受筛选影响
    assert body["coverage"]["score"] > 0

    # 显式全选三类 = 默认复合口径：不重算，与无筛选结果一致
    r_all = post_checkup(
        client,
        sample_center_bd,
        15,
        blindFilter=["convenience", "health", "education"],
    )
    assert r_all.status_code == 200
    assert r_all.json()["blindSummary"] == post_checkup(
        client, sample_center_bd, 15
    ).json()["blindSummary"]
