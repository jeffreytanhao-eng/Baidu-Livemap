"""FastAPI 入口：路由 + lifespan 预热。

启动时把 jinsong（以及存在的 zhongguancun/nanyuan）底图建成内存步行图；
请求期按 covers() 选 region，不覆盖时用最近 region 并标 degraded。
"""

from __future__ import annotations

import logging
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from geo import classify_building, wgs84_to_bd09ll
from geo.coords import LocalProjection
from geo.tiles import tile_indices, tile_name
from pydantic import BaseModel, Field
from shapely.geometry import mapping
from shapely.ops import transform as shp_transform

from . import community, db
from .baidu import BaiduError
from .checkup import run_checkup
from .config import Settings, load_settings
from .demo_data import REGION_CENTERS, SAMPLES, nearest_sample
from .engine import EngineContext, build_engine
from .geojson import to_wgs84
from .whitelist import is_overseas

logger = logging.getLogger("livemap.api")

# 让 livemap.* 业务日志（含每次 checkup 的 minutes/api_calls/elapsed/degraded）落到 stderr
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

APP_VERSION = "0.1.0"

OVERSEAS_DETAIL = "仅支持国内（经度 73–135、纬度 18–54）"


class CenterModel(BaseModel):
    lng: float
    lat: float
    coordType: str = "bd09ll"


class CheckupRequest(BaseModel):
    center: CenterModel
    minutes: int = Field(ge=5, le=30)
    engine: str = "map-fabric"
    includeOfficialIsochrone: bool = False
    includeBlindWalk: bool = True
    phase: Literal["fast", "full"] = "full"
    # 盲区单类/复合筛选：None=复合全类；子集只算所选类；非法值 400
    blindFilter: list[Literal["convenience", "health", "education"]] | None = None


class GeocodeRequest(BaseModel):
    query: str


class ReverseRequest(BaseModel):
    lng: float
    lat: float
    coordType: str = "bd09ll"


# 无 AK 时的内置地理编码映射（关键词 -> 展示地址 + WGS84 中心）
_BUILTIN_GEOCODE: dict[str, tuple[str, tuple[float, float]]] = {
    "劲松": ("劲松街道 · 北京", REGION_CENTERS["jinsong"]),
    "中关村": ("中关村 · 北京", REGION_CENTERS["zhongguancun"]),
    "南苑": ("南苑 · 北京", REGION_CENTERS["nanyuan"]),
}


def _ctx(request: Request) -> EngineContext:
    return request.app.state.ctx


def _parse_wgs(lng: float, lat: float, coord_type: str) -> tuple[float, float]:
    try:
        return to_wgs84(lng, lat, coord_type)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        ctx = build_engine(settings)
        app.state.ctx = ctx
        # SQLite 小区数据 + 本地图片目录（缺失时初始化空库，资讯框走「信息不足」）
        settings.uploads_dir.mkdir(parents=True, exist_ok=True)
        db.init_db(settings.db_path)
        app.state.db = db.connect(settings.db_path)
        yield
        await ctx.baidu.aclose()
        app.state.db.close()

    app = FastAPI(title="Livemap API", version=APP_VERSION, lifespan=lifespan)
    app.state.fabric_cache = {}
    # 小区照片等本地自托管图片（data/uploads/**）
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/static/uploads", StaticFiles(directory=settings.uploads_dir), name="uploads")
    app.add_middleware(
        CORSMiddleware,
        # 本地开发前端可能落在 3000-3100 任一端口（3000 常被占用）
        allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(RequestValidationError)
    async def validation_handler(_request: Request, exc: RequestValidationError):
        # 契约要求参数错误一律 400（FastAPI 默认 422）
        parts = []
        for err in exc.errors():
            loc = ".".join(str(x) for x in err.get("loc", []))
            msg = err.get("msg", "")
            if loc.endswith("minutes"):
                msg = "minutes 必须是 5–30 的整数"
            parts.append(f"{loc}: {msg}".strip(": "))
        return JSONResponse(status_code=400, content={"detail": "; ".join(parts)})

    @app.get("/health")
    def health(request: Request):
        ctx = _ctx(request)
        bundle = ctx.regions.get("jinsong") or next(iter(ctx.regions.values()), None)
        graph = (
            {"nodes": bundle.graph.node_count, "edges": bundle.graph.edge_count}
            if bundle is not None
            else {"nodes": 0, "edges": 0}
        )
        return {
            "status": "ok",
            "version": APP_VERSION,
            "akConfigured": ctx.baidu.enabled,
            "demoMode": settings.demo_mode or not ctx.baidu.enabled,
            "graph": graph,
        }

    @app.post("/api/v1/checkup")
    async def checkup(req: CheckupRequest, request: Request):
        ctx = _ctx(request)
        center_wgs = _parse_wgs(req.center.lng, req.center.lat, req.center.coordType)
        if is_overseas(*center_wgs):
            raise HTTPException(status_code=400, detail=OVERSEAS_DETAIL)
        # TODO: 本地包不覆盖时可接 Overpass 在线补全底图；离线环境不实现，
        # 目前由 run_checkup 内 pick_region 走「最近 region + degraded」降级。
        result = await run_checkup(
            ctx,
            center_wgs=center_wgs,
            minutes=req.minutes,
            engine=req.engine,
            include_blind_walk=req.includeBlindWalk,
            phase=req.phase,
            include_official=req.includeOfficialIsochrone,
            blind_filter=req.blindFilter,
        )
        # full 相位体检完成后回写得分：500m 内最近小区命中则更新 score/scored_at
        # （try/except 隔离，回写失败不影响体检响应）
        if req.phase == "full":
            try:
                score = (result.get("coverage") or {}).get("score")
                if score is not None:
                    hit = db.find_nearest(request.app.state.db, *center_wgs, max_m=500.0)
                    if hit is not None:
                        db.update_score(request.app.state.db, hit[0]["id"], score)
            except Exception:
                logger.warning("checkup 得分回写失败（不影响响应）", exc_info=True)
        return result

    @app.get("/api/v1/fabric")
    def fabric(
        request: Request,
        lng: float = Query(...),
        lat: float = Query(...),
        radiusM: float = Query(1500.0),
        coordType: str = Query("bd09ll"),
    ):
        ctx = _ctx(request)
        center = _parse_wgs(lng, lat, coordType)
        radius = min(max(radiusM, 100.0), 4000.0)
        # 内存缓存：同格同半径直接命中（底图启动后不变）。
        # key 用中心 4 位小数（≈11m）：geohash5 网格 4.9km，fabric 数据半径
        # 仅 2km，同格不同点会互相串缓存（瑞悦府拿到 1km 外泰禾的建筑，
        # 视口内「没有建筑」）
        cache_key = (round(center[0], 4), round(center[1], 4), round(radius))
        hit = app.state.fabric_cache.get(cache_key)
        if hit is not None:
            return hit
        # 选 region：网格片按片名直取（与 checkup 的 clip_region 同源）。
        # 相邻片的 padded bbox 相互重叠约 2×pad，clip() 按点猜会命中先加载
        # 的邻片——点落在其 pad 边缘带，buildings 等层裁出来近乎为空，
        # 前端整组替换后建筑图层「消失」（泰禾北京院子事故）。
        region_name: str | None = None
        if ctx.tiles is not None and ctx.tiles.has_tile(*center):
            region_name = tile_name(*tile_indices(*center))
        else:
            for name in ctx.regions:
                if ctx.regions[name].lite:
                    continue
                x0, y0, x1, y1 = ctx.service._regions[name].bbox_wgs84
                if x0 <= center[0] <= x1 and y0 <= center[1] <= y1:
                    region_name = name
                    break
        if region_name is not None:
            # 片可能已被 TileManager LRU 淘汰（max_tiles=8）或首次访问：
            # 就地重载。绕过 LRU 直挂 service，访问频率低、量级可控。
            if region_name not in ctx.service._regions:
                ctx.service.load_region(region_name)
            layers = ctx.service.clip_region(region_name, center, radius)
        else:
            layers = ctx.service.clip(center, radius)
        proj = LocalProjection(*center)

        def fc(items, extra=None):
            feats = []
            for geom, props in items:
                bd = shp_transform(lambda x, y: wgs84_to_bd09ll(*proj.to_lnglat(x, y)), geom)
                p = dict(props)
                if extra is not None:
                    p.update(extra(props))
                feats.append({"type": "Feature", "geometry": mapping(bd), "properties": p})
            return {"type": "FeatureCollection", "features": feats}

        payload = {
            "roads": fc(layers.roads),
            "buildings": fc(layers.buildings, lambda pr: {"category": classify_building(pr)}),
            "residential": fc(layers.residential),
            "water": fc(layers.water),
            "barriers": fc(layers.barriers),
        }
        app.state.fabric_cache[cache_key] = payload
        return payload

    @app.post("/api/v1/geocode")
    async def geocode(body: GeocodeRequest, request: Request):
        ctx = _ctx(request)
        query = body.query.strip()
        if not query:
            raise HTTPException(status_code=400, detail="query 不能为空")
        if ctx.baidu.enabled:
            try:
                hit = await ctx.baidu.geocode(query)
            except BaiduError as exc:
                raise HTTPException(status_code=502, detail=f"百度地理编码失败：{exc}") from exc
            if hit is None:
                raise HTTPException(status_code=404, detail="未找到该地址")
            return hit
        for keyword, (address, wgs) in _BUILTIN_GEOCODE.items():
            if keyword in query:
                bd = wgs84_to_bd09ll(*wgs)
                return {"lng": bd[0], "lat": bd[1], "address": address}
        raise HTTPException(status_code=404, detail="未找到该地址（离线演示支持：劲松 / 中关村 / 南苑）")

    @app.post("/api/v1/reverse")
    async def reverse(body: ReverseRequest, request: Request):
        ctx = _ctx(request)
        if ctx.baidu.enabled:
            try:
                return await ctx.baidu.reverse_geocode(body.lng, body.lat)
            except BaiduError as exc:
                raise HTTPException(status_code=502, detail=f"百度逆地理编码失败：{exc}") from exc
        center_wgs = _parse_wgs(body.lng, body.lat, body.coordType)
        sample, dist = nearest_sample(*center_wgs)
        if dist <= 2000.0:
            return {"address": f"{sample.name}附近"}
        return {"address": f"北京市（{body.lng:.4f}, {body.lat:.4f}）附近"}

    @app.get("/api/v1/community/locate")
    def community_locate(
        request: Request,
        lng: float = Query(...),
        lat: float = Query(...),
        coordType: str = Query("bd09ll"),
    ):
        """点击定位小区：点在面内识别 residential 面 → 名称/坐标匹配 SQLite 小区。

        三态：matchedBy=name（面名精确匹配）/ coord（500m 内最近兜底）/ none
        （未命中，前端不弹资讯框）。DB 为空时恒为 none（前端显示占位提示）。
        """
        ctx = _ctx(request)
        center_wgs = _parse_wgs(lng, lat, coordType)
        if is_overseas(*center_wgs):
            raise HTTPException(status_code=400, detail=OVERSEAS_DETAIL)
        conn: sqlite3.Connection = request.app.state.db
        return community.locate(ctx, conn, *center_wgs)

    @app.get("/api/v1/community/nearby")
    def community_nearby(
        request: Request,
        lng: float = Query(...),
        lat: float = Query(...),
        coordType: str = Query("bd09ll"),
        radiusM: float = Query(2000.0),
    ):
        """附近小区列表：radiusM 内按 distanceM 升序，坐标回传 BD09（与地图一致）。

        仅返回名称/距离/坐标，不含得分——得分在评估页体检完成后才计算入库。
        """
        center_wgs = _parse_wgs(lng, lat, coordType)
        if is_overseas(*center_wgs):
            raise HTTPException(status_code=400, detail=OVERSEAS_DETAIL)
        radius = min(max(radiusM, 100.0), 5000.0)
        conn: sqlite3.Connection = request.app.state.db
        out = []
        for c, dist in db.find_nearby(conn, center_wgs[0], center_wgs[1], radius):
            bd = wgs84_to_bd09ll(c["lng"], c["lat"])
            out.append(
                {
                    "id": c["id"],
                    "name": c["name"],
                    "lng": bd[0],
                    "lat": bd[1],
                    "distanceM": round(dist),
                }
            )
        return {"radiusM": radius, "communities": out}

    @app.get("/api/v1/samples")
    def list_samples():
        out = []
        for s in SAMPLES:
            bd = wgs84_to_bd09ll(*s.center_wgs)
            out.append(
                {
                    "id": s.id,
                    "name": s.name,
                    "center": {"lng": bd[0], "lat": bd[1]},
                    "minutes": s.minutes,
                }
            )
        return out

    return app


app = create_app()
