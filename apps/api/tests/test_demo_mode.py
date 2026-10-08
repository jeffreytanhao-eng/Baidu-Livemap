"""DEMO_MODE / 无 AK：快照优先；非精确档回退 15 分钟快照；无快照现场算仍标 demo。"""

from __future__ import annotations


def test_snapshot_served_in_demo_mode(client, post_checkup, sample_center_bd):
    # 劲松 15：无 AK 走 demo 路径，命中 data/samples/jinsong_15.json 快照
    r = post_checkup(client, sample_center_bd, 15)
    assert r.status_code == 200
    meta = r.json()["meta"]
    assert meta["demoMode"] is True
    assert [layer["minutes"] for layer in r.json()["layers"]] == [5, 10, 15]

    # fast phase 同样回快照子集
    r = post_checkup(client, sample_center_bd, 15, phase="fast")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"meta", "fastLayer"}
    assert body["meta"]["demoMode"] is True


def test_non_exact_minutes_fallback_to_15_snapshot(client, post_checkup, sample_center_bd):
    # 劲松 14：无精确快照 → 回退 jinsong_15 快照，改 meta.minutes 并注明
    r = post_checkup(client, sample_center_bd, 14)
    assert r.status_code == 200
    body = r.json()
    assert body["meta"]["demoMode"] is True
    assert body["meta"]["minutes"] == 14
    assert any("15 分钟" in d and "演示数据" in d for d in body["diagnosis"])

    # fast phase 同样回退
    r = post_checkup(client, sample_center_bd, 14, phase="fast")
    assert r.status_code == 200
    assert set(r.json()) == {"meta", "fastLayer"}
    assert r.json()["meta"]["minutes"] == 14


def test_live_compute_without_snapshot_still_demo(client, post_checkup):
    # 远离任何样例（上海）：无快照可回，现场用内存图算，仍标 demoMode
    r = post_checkup(client, (121.47, 31.23), 14, coord_type="wgs84")
    assert r.status_code == 200
    body = r.json()
    assert body["meta"]["demoMode"] is True
    assert body["meta"]["degraded"] is True  # 借用最近 region 底图
    assert 3 <= len(body["diagnosis"]) <= 6


def test_demo_mode_env_forces_snapshot_even_with_ak(client, ctx, post_checkup, sample_center_bd):
    # DEMO_MODE=true 时即便配置了 AK 也优先快照
    old_ak, old_enabled, old_demo = ctx.baidu.ak, ctx.baidu.enabled, ctx.settings.demo_mode
    try:
        ctx.baidu.ak = "fake-ak"
        ctx.baidu.enabled = True
        ctx.settings.demo_mode = True
        r = post_checkup(client, sample_center_bd, 15)
        assert r.status_code == 200
        assert r.json()["meta"]["demoMode"] is True
        assert ctx.baidu.api_calls == 0, "demo 快照路径不应产生百度调用"
    finally:
        ctx.baidu.ak, ctx.baidu.enabled, ctx.settings.demo_mode = old_ak, old_enabled, old_demo


def test_engine_demo_flag(client, post_checkup, default_center_bd):
    r = post_checkup(client, default_center_bd, 16, engine="demo")
    assert r.status_code == 200
    assert r.json()["meta"]["demoMode"] is True
