"""BaiduClient 离线单测：拼参（lat,lng / lng,lat 顺序）、切批、限流、429 重试。

全部用 httpx.MockTransport，不打真网。
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from app.baidu import (
    BaiduClient,
    BaiduError,
    DemoFallback,
    NotAvailableError,
    _TokenBucket,
)


async def _no_sleep(_seconds: float) -> None:
    return None


def _run(coro):
    return asyncio.run(coro)


def _client(handler, **kwargs) -> BaiduClient:
    kwargs.setdefault("qps", 1000.0)  # 单测不限流
    kwargs.setdefault("sleep", _no_sleep)
    return BaiduClient("test-ak", transport=httpx.MockTransport(handler), **kwargs)


def test_geocode_params():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.url.params))
        return httpx.Response(
            200, json={"status": 0, "result": {"location": {"lng": 116.46, "lat": 39.88}}}
        )

    result = _run(_use(_client(handler).geocode("劲松街道")))
    assert result == {"lng": 116.46, "lat": 39.88, "address": "劲松街道"}
    assert seen["address"] == "劲松街道"
    assert seen["output"] == "json"
    assert seen["ak"] == "test-ak"


def test_reverse_geocode_location_lat_lng():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.url.params))
        return httpx.Response(200, json={"status": 0, "result": {"formatted_address": "北京市朝阳区"}})

    result = _run(_use(_client(handler).reverse_geocode(116.46, 39.88)))
    assert result["address"] == "北京市朝阳区"
    # 写死：location=lat,lng（纬度在前）
    assert seen["location"] == "39.88,116.46"


def test_geoconv_coords_lng_lat():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.url.params))
        return httpx.Response(200, json={"status": 0, "result": [{"x": 1.0, "y": 2.0}]})

    out = _run(_use(_client(handler).geoconv([(116.11, 39.11), (116.22, 39.22)])))
    assert out == [(1.0, 2.0)]
    # 写死：geoconv 为 lng,lat（经度在前）
    assert seen["coords"] == "116.11,39.11;116.22,39.22"


def test_place_search_params_and_radius_cap():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.url.params))
        return httpx.Response(200, json={"status": 0, "results": [{"name": "便民菜场"}]})

    out = _run(_use(_client(handler).place_search("菜市场", (116.46, 39.88), 1500)))
    assert out == [{"name": "便民菜场"}]
    assert seen["location"] == "39.88,116.46"
    assert seen["radius"] == "1500"
    assert seen["scope"] == "2"  # scope=2 带回 detail_info.tag（public 剔写字楼/商场用）
    _run(_use(_client(handler).place_search("菜市场", (116.46, 39.88), 2700)))
    assert seen["radius"] == "2000"  # 百度上限 2km


def test_walking_matrix_lat_lng_pairs():
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(dict(request.url.params))
        return httpx.Response(200, json={"status": 0, "result": [{"distance": 1}]})

    origins = [(116.11, 39.11)]
    destinations = [(116.22, 39.22), (116.33, 39.33)]
    out = _run(_use(_client(handler).walking_matrix(origins, destinations)))
    assert out == [{"distance": 1}]
    # 写死：origins/destinations 均为 lat,lng 对
    assert requests[0]["origins"] == "39.11,116.11"
    assert requests[0]["destinations"] == "39.22,116.22|39.33,116.33"


def test_walking_matrix_batching_50_pairs():
    counts: list[tuple[int, int]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        counts.append(
            (len(params["origins"].split("|")), len(params["destinations"].split("|")))
        )
        return httpx.Response(200, json={"status": 0, "result": []})

    # 10 origins x 12 destinations：每批 ≤50 对 → 5+5+2 共 3 批
    origins = [(116.0 + i * 0.01, 39.0 + i * 0.01) for i in range(10)]
    destinations = [(117.0 + i * 0.01, 40.0 + i * 0.01) for i in range(12)]
    _run(_use(_client(handler).walking_matrix(origins, destinations)))
    assert counts == [(10, 5), (10, 5), (10, 2)]
    for o, d in counts:
        assert o * d <= 50

    # 1 origin x 120 destinations：50+50+20 共 3 批
    counts.clear()
    _run(_use(_client(handler).walking_matrix(origins[:1], destinations * 10)))
    assert counts == [(1, 50), (1, 50), (1, 20)]


def test_walking_route_directionlite_params():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.url.params))
        return httpx.Response(200, json={"status": 0, "result": {"routes": []}})

    _run(_use(_client(handler).walking_route((116.11, 39.11), (116.22, 39.22))))
    # 写死：directionlite origin/destination 为 lat,lng
    assert seen["origin"] == "39.11,116.11"
    assert seen["destination"] == "39.22,116.22"


def test_retry_on_429_then_success():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] <= 2:
            return httpx.Response(429, json={"status": 401, "message": "rate limited"})
        return httpx.Response(200, json={"status": 0, "result": {"formatted_address": "ok"}})

    result = _run(_use(_client(handler).reverse_geocode(116.46, 39.88)))
    assert result["address"] == "ok"
    assert calls["n"] == 3


def test_retry_on_429_exhausted():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(429)

    with pytest.raises(BaiduError):
        _run(_use(_client(handler).reverse_geocode(116.46, 39.88)))
    assert calls["n"] == 4  # 1 次原始 + ≤3 次重试


def test_retry_on_quota_302_then_success():
    """status=302（天配额超限）与 429 同族：退避重试，恢复后成功。

    实测百度配额管控有抖动，同一 AK 前一秒 302 后一秒成功。
    """
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] <= 2:
            return httpx.Response(200, json={"status": 302, "message": "天配额超限，限制访问"})
        return httpx.Response(200, json={"status": 0, "result": {"formatted_address": "ok"}})

    result = _run(_use(_client(handler).reverse_geocode(116.46, 39.88)))
    assert result["address"] == "ok"
    assert calls["n"] == 3


def test_retry_on_quota_302_exhausted():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"status": 302, "message": "天配额超限，限制访问"})

    with pytest.raises(BaiduError, match="302"):
        _run(_use(_client(handler).reverse_geocode(116.46, 39.88)))
    assert calls["n"] == 4  # 1 次原始 + ≤3 次重试


def test_retry_on_concurrency_401_then_success():
    """status=401（并发量超限）瞬态限流：退避重试，恢复后成功。"""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(200, json={"status": 401, "message": "当前并发量已经超过约定并发配额，限制访问"})
        return httpx.Response(200, json={"status": 0, "result": {"formatted_address": "ok"}})

    result = _run(_use(_client(handler).reverse_geocode(116.46, 39.88)))
    assert result["address"] == "ok"
    assert calls["n"] == 2


def test_baidu_error_status_raises():
    """非配额类业务错误（如参数非法）不重试，立即抛出。"""

    calls = {"n": 0}

    def counting_handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"status": 2, "message": "请求参数非法"})

    with pytest.raises(BaiduError, match="status=2"):
        _run(_use(_client(counting_handler).reverse_geocode(116.46, 39.88)))
    assert calls["n"] == 1


def test_disabled_client_raises():
    client = BaiduClient(None)
    assert client.enabled is False
    with pytest.raises(DemoFallback):
        _run(_use(client.geocode("劲松")))
    # DemoFallback 是 BaiduError 子类，编排层统一捕获
    with pytest.raises(BaiduError):
        _run(_use(client.reverse_geocode(116.46, 39.88)))


def test_official_isochrone_not_available():
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("官方等时圈不应发起 HTTP 请求")

    client = _client(handler)
    with pytest.raises(NotAvailableError, match="等时圈"):
        _run(_use(client.official_isochrone((116.46, 39.88), 15)))


def test_result_cache_memory():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"status": 0, "result": {"formatted_address": "ok"}})

    client = _client(handler)

    async def go():
        try:
            await client.reverse_geocode(116.46, 39.88)
            await client.reverse_geocode(116.46, 39.88)
            assert calls["n"] == 1, "第二次同参请求应命中内存缓存"
            # 不同参数不命中
            await client.reverse_geocode(116.47, 39.88)
            assert calls["n"] == 2
        finally:
            await client.aclose()

    _run(go())


def test_result_cache_disk_shared_across_clients(tmp_path):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"status": 0, "result": {"formatted_address": "disk"}})

    c1 = _client(handler, cache_dir=tmp_path)
    assert _run(_use(c1.reverse_geocode(116.46, 39.88)))["address"] == "disk"
    assert calls["n"] == 1

    # 新实例（内存缓存为空）共享同一 cache_dir → 磁盘命中，不再发请求
    c2 = _client(handler, cache_dir=tmp_path)
    assert _run(_use(c2.reverse_geocode(116.46, 39.88)))["address"] == "disk"
    assert calls["n"] == 1


def test_token_bucket_reserve():
    now = [0.0]
    bucket = _TokenBucket(2.0, clock=lambda: now[0])
    assert bucket.reserve() == 0.0  # 第 1 个令牌
    assert bucket.reserve() == 0.0  # 第 2 个令牌
    wait = bucket.reserve()  # 桶空，需等 1/2 QPS = 0.5s
    assert 0.4 < wait <= 0.5
    now[0] += wait
    assert bucket.reserve() == 0.0  # 补充后可立即取


async def _use(coro):
    """await coro 并关闭其所属 client（从 coroutine 的 self 取回）。"""
    self = coro.cr_frame.f_locals.get("self")
    try:
        return await coro
    finally:
        if isinstance(self, BaiduClient):
            await self.aclose()
