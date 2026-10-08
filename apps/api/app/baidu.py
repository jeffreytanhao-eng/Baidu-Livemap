"""百度服务端 API 客户端（AK 仅服务端持有）。

- 令牌桶限流 2 QPS；超时 8s；429 指数退避重试 ≤3 次；
- 参数顺序严格按百度各接口文档写死：
  - geocoding / reverse_geocoding：``location=lat,lng``；
  - directionlite / routematrix：``origin``/``destination``/``origins``/``destinations``
    均为 ``lat,lng`` 对；
  - geoconv v1：``coords=lng,lat;lng,lat``（经度在前）；
- 无 AK 时 ``enabled=False``，所有方法抛 ``DemoFallback``，编排层捕获后走快照；
- 结果缓存：内存 dict + 磁盘 JSON（``cache_dir``，TTL 24h），key=方法+参数 hash。

测试通过注入 ``transport``（httpx.MockTransport）与 ``sleep``/时钟离线断言。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger("livemap.baidu")

GEOCODE_URL = "https://api.map.baidu.com/geocoding/v3/"
REVERSE_GEOCODE_URL = "https://api.map.baidu.com/reverse_geocoding/v3/"
GEOCONV_URL = "https://api.map.baidu.com/geoconv/v1/"
PLACE_SEARCH_URL = "https://api.map.baidu.com/place/v2/search"
ROUTE_MATRIX_WALKING_URL = "https://api.map.baidu.com/routematrix/v2/walking"
DIRECTION_LITE_WALKING_URL = "https://api.map.baidu.com/directionlite/v1/walking"

# routematrix 单请求 origin×destination 上限（步行）
MATRIX_MAX_PAIRS = 50
MATRIX_MAX_ORIGINS = 10


class BaiduError(RuntimeError):
    """百度 API 调用失败（网络/限流/业务状态码）。"""


class DemoFallback(BaiduError):
    """未配置 AK：编排层捕获后走演示快照路径。"""


class NotAvailableError(BaiduError):
    """接口不存在或当前 AK 无权限（如官方等时圈）。"""


class _RateLimited(BaiduError):
    """HTTP 429 或业务 status=302/401（天配额/并发量超限），触发指数退避。

    实测百度配额管控存在抖动：同一 AK 前一秒 302、后一秒成功；
    401 并发超限更是瞬态（并发窗口释放即恢复），故与 429 同族处理——
    退避重试而非立即失败。
    """


class _TokenBucket:
    """令牌桶（2 QPS）。``reserve`` 返回需要等待的秒数，便于离线测试。"""

    def __init__(self, qps: float, clock: Callable[[], float] = time.monotonic) -> None:
        self.qps = float(qps)
        self.capacity = max(1.0, float(qps))
        self.tokens = self.capacity
        self.updated = clock()
        self._clock = clock
        self._lock = asyncio.Lock()

    def reserve(self) -> float:
        now = self._clock()
        self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.qps)
        self.updated = now
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return 0.0
        wait = (1.0 - self.tokens) / self.qps
        self.tokens = 0.0  # 预支一个令牌，等待补齐
        return wait

    async def acquire(self, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> None:
        async with self._lock:
            wait = self.reserve()
            if wait > 0:
                await sleep(wait)


CACHE_TTL_S = 24 * 3600.0


class BaiduClient:
    def __init__(
        self,
        ak: str | None = None,
        *,
        qps: float = 2.0,
        timeout: float = 8.0,
        max_retries: int = 3,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
        cache_dir: Path | None = None,
        cache_ttl_s: float = CACHE_TTL_S,
    ) -> None:
        self.ak = (ak or "").strip() or None
        self.enabled = bool(self.ak)
        self.timeout = timeout
        self.max_retries = max_retries
        self.api_calls = 0
        self._bucket = _TokenBucket(qps, clock)
        self._sleep = sleep
        self._client = httpx.AsyncClient(timeout=timeout, transport=transport)
        self._mem_cache: dict[str, dict] = {}
        self._cache_dir = Path(cache_dir) if cache_dir else None
        self._cache_ttl_s = cache_ttl_s

    async def aclose(self) -> None:
        await self._client.aclose()

    def _require_enabled(self) -> None:
        if not self.enabled:
            raise DemoFallback("BAIDU_SERVER_AK 未配置，走演示快照路径")

    # ---- 缓存：内存 dict + 磁盘 JSON，key=方法+参数 hash，TTL 24h ----
    @staticmethod
    def _cache_key(method: str, params: dict[str, Any]) -> str:
        raw = json.dumps(params, sort_keys=True, ensure_ascii=False, default=str)
        return f"{method}:{hashlib.sha1(raw.encode('utf-8')).hexdigest()}"

    def _cache_path(self, key: str) -> Path:
        assert self._cache_dir is not None
        safe = key.replace(":", "_")
        return self._cache_dir / f"{safe}.json"

    def _cache_get(self, key: str) -> dict | None:
        if key in self._mem_cache:
            return self._mem_cache[key]
        if self._cache_dir is None:
            return None
        path = self._cache_path(key)
        try:
            if not path.exists() or time.time() - path.stat().st_mtime > self._cache_ttl_s:
                return None
            with path.open(encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as exc:
            logger.warning("baidu cache read failed: %s", exc)
            return None
        self._mem_cache[key] = data
        return data

    def _cache_set(self, key: str, data: dict) -> None:
        self._mem_cache[key] = data
        if self._cache_dir is None:
            return
        path = self._cache_path(key)
        try:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            with tmp.open("w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False)
            tmp.replace(path)
        except OSError as exc:
            logger.warning("baidu cache write failed: %s", exc)

    async def _get_json(self, url: str, params: dict[str, Any], *, _method: str = "GET") -> dict:
        """限流 + 429 指数退避（0.5/1/2s，≤3 次重试）+ 方法级结果缓存。"""
        self._require_enabled()
        full_params = {**params, "ak": self.ak}
        cache_key = self._cache_key(_method, {"url": url, "params": full_params})
        hit = self._cache_get(cache_key)
        if hit is not None:
            return hit
        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            if attempt > 0:
                await self._sleep(0.5 * (2 ** (attempt - 1)))
            await self._bucket.acquire(self._sleep)
            try:
                resp = await self._client.get(url, params=full_params)
                self.api_calls += 1
                if resp.status_code == 429:
                    raise _RateLimited("baidu api 429 (rate limited)")
                resp.raise_for_status()
                data = resp.json()
            except _RateLimited as exc:
                last_exc = exc
                continue
            except httpx.TransportError as exc:
                last_exc = BaiduError(f"baidu api transport error: {exc}")
                continue
            except httpx.HTTPStatusError as exc:
                raise BaiduError(f"baidu api http {exc.response.status_code}") from exc
            except ValueError as exc:
                raise BaiduError("baidu api returned invalid json") from exc
            status = data.get("status")
            if status not in (0, None):
                msg = data.get("message") or data.get("msg") or ""
                if status in (302, 401):  # 天配额超限/并发量超限：与 429 同族，退避重试（瞬态限流）
                    last_exc = BaiduError(f"baidu api status={status} {msg}".strip())
                    continue
                raise BaiduError(f"baidu api status={status} {msg}".strip())
            self._cache_set(cache_key, data)
            return data
        raise BaiduError(f"baidu api failed after {self.max_retries + 1} tries: {last_exc}")

    async def geocode(self, address: str, city: str | None = None) -> dict | None:
        """正向地理编码；返回 BD09LL ``{lng, lat, address}``。"""
        params: dict[str, Any] = {"address": address, "output": "json"}
        if city:
            params["city"] = city
        data = await self._get_json(GEOCODE_URL, params, _method="geocode")
        result = data.get("result") or {}
        loc = result.get("location") or {}
        if "lng" not in loc or "lat" not in loc:
            return None
        return {"lng": float(loc["lng"]), "lat": float(loc["lat"]), "address": address}

    async def reverse_geocode(self, lng: float, lat: float) -> dict:
        """逆地理编码；``location=lat,lng``（纬度在前，按百度文档写死）。"""
        params = {
            "location": f"{lat},{lng}",
            "output": "json",
            "coordtype": "bd09ll",
        }
        data = await self._get_json(REVERSE_GEOCODE_URL, params, _method="reverse_geocode")
        result = data.get("result") or {}
        return {"address": str(result.get("formatted_address") or "")}

    async def geoconv(
        self, coords: list[tuple[float, float]], from_type: int = 1, to_type: int = 5
    ) -> list[tuple[float, float]]:
        """坐标转换；geoconv v1 的 coords 为 ``lng,lat;lng,lat``（经度在前）。"""
        params = {
            "coords": ";".join(f"{lng},{lat}" for lng, lat in coords),
            "from": from_type,
            "to": to_type,
        }
        data = await self._get_json(GEOCONV_URL, params, _method="geoconv")
        out: list[tuple[float, float]] = []
        for item in data.get("result") or []:
            out.append((float(item["x"]), float(item["y"])))
        return out

    async def place_search(
        self, query: str, center: tuple[float, float], radius_m: float, page_size: int = 20
    ) -> list[dict]:
        """圆形区域 POI 检索；``location=lat,lng``，radius 单位米（≤2000）。

        center 约定为 **BD09LL**（Place API ``coord_type`` 默认 3=bd09ll），
        与 walking_route/walking_matrix 一致；调用方负责先把 WGS84 转BD09。
        返回的 POI 坐标同为 BD09LL。
        scope=2 带回 ``detail_info.tag``（百度标准类别，如「房地产;写字楼」
        「旅游景点;公园」），供 public 类剔除伪装成「XX广场」的写字楼/商场。
        """
        lng, lat = center
        params = {
            "query": query,
            "location": f"{lat},{lng}",
            "radius": min(int(radius_m), 2000),
            "output": "json",
            "page_size": page_size,
            "scope": 2,
        }
        data = await self._get_json(PLACE_SEARCH_URL, params, _method="place_search")
        return list(data.get("results") or [])

    async def walking_matrix(
        self,
        origins: list[tuple[float, float]],
        destinations: list[tuple[float, float]],
    ) -> list[dict]:
        """步行耗时矩阵（RouteMatrix v2）。

        每批 origin×destination ≤ 50 对，超出自动切批；坐标为 ``lat,lng`` 对。
        返回逐批 ``result`` 数组的拼接。
        """
        results: list[dict] = []
        for i in range(0, len(origins), MATRIX_MAX_ORIGINS):
            o_chunk = origins[i : i + MATRIX_MAX_ORIGINS]
            max_dest = max(1, MATRIX_MAX_PAIRS // max(1, len(o_chunk)))
            for j in range(0, len(destinations), max_dest):
                d_chunk = destinations[j : j + max_dest]
                params = {
                    "origins": "|".join(f"{lat},{lng}" for lng, lat in o_chunk),
                    "destinations": "|".join(f"{lat},{lng}" for lng, lat in d_chunk),
                }
                data = await self._get_json(
                    ROUTE_MATRIX_WALKING_URL, params, _method="walking_matrix"
                )
                results.extend(data.get("result") or [])
        return results

    async def walking_route(
        self, origin: tuple[float, float], destination: tuple[float, float]
    ) -> dict:
        """DirectionLite 步行路线；origin/destination 均为 ``lat,lng``。"""
        params = {
            "origin": f"{origin[1]},{origin[0]}",
            "destination": f"{destination[1]},{destination[0]}",
            "coord_type": "bd09ll",
        }
        return await self._get_json(DIRECTION_LITE_WALKING_URL, params, _method="walking_route")

    async def official_isochrone(
        self, center: tuple[float, float], minutes: int
    ) -> dict:
        """百度官方等时圈接口。

        该接口未对公众开放（需单独商务授权）；无权限/无此接口时抛
        ``NotAvailableError``，编排层捕获后仅以自建引擎结果为准。
        """
        self._require_enabled()
        raise NotAvailableError(
            "百度官方等时圈接口未对该 AK 开放（需单独授权），本次以自建引擎结果为准"
        )
