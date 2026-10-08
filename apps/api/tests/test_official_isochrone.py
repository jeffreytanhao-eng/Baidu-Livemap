"""includeOfficialIsochrone=true 的编排分支：无权限接口的明确降级诊断。

官方等时圈接口需单独商务授权，``BaiduClient.official_isochrone`` 在无权限时
抛 ``NotAvailableError``；编排层捕获后必须在 diagnosis 里写明「以自建引擎为准」，
且整体请求不受影响。客户端级抛错已有 test_baidu_client.py 覆盖，这里补编排级。
"""

from __future__ import annotations


def test_official_isochrone_unavailable_falls_back_with_note(
    client, ctx, monkeypatch, sample_center_bd
):
    # 模拟已配置 AK：official_isochrone 无网络开销地抛 NotAvailableError；
    # POI 检索会因 DemoFallback 自动回退确定性合成，全程离线。
    monkeypatch.setattr(ctx.baidu, "enabled", True)
    ctx.node_results.clear()
    resp = client.post(
        "/api/v1/checkup",
        json={
            "center": {"lng": sample_center_bd[0], "lat": sample_center_bd[1], "coordType": "bd09ll"},
            "minutes": 16,
            "engine": "map-fabric",
            "includeOfficialIsochrone": True,
            "includeBlindWalk": True,
            "phase": "full",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["meta"]["demoMode"] is False
    assert any("官方等时圈" in line and "自建引擎" in line for line in body["diagnosis"])
