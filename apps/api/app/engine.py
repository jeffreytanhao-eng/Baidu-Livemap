"""引擎上下文：启动预热把各 region 底图裁片 → 可进入性分类 → 步行图载入内存。

请求路径只做「吸附 → 截止 Dijkstra → 缓冲成斑」，绝不重新解析 GeoJSON。
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

from geo import (
    CATEGORY_ENTERABLE,
    ClassifiedBuilding,
    MapFabricService,
    WalkGraph,
    build_graph,
    classify_buildings,
    haversine_m,
    inject_baidu_polyline,
)
from geo.coords import LocalProjection
from geo.ring6 import in_ring6
from geo.tiles import TILE_PAD_M, tile_indices, tile_name
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from .baidu import BaiduClient
from .cache import DiskCache
from .config import Settings
from .demo_data import JINSONG_DEMO_ROUTES, REGION_CENTERS

logger = logging.getLogger("livemap.engine")

KNOWN_REGIONS: tuple[str, ...] = ("jinsong", "zhongguancun", "nanyuan")
# 六环全域由全量网格片（TileManager 懒加载，b6r_* 目录）覆盖，
# 不再使用单一 lite 大 region；LITE_REGIONS 仅为兼容保留（当前为空）
LITE_REGIONS: frozenset[str] = frozenset()


@dataclass
class RegionBundle:
    """单个 region 的预热产物（几何均为以 region 中心为原点的本地米坐标）。"""

    name: str
    center_wgs: tuple[float, float]
    graph: WalkGraph
    walls: list[BaseGeometry]
    water: list[BaseGeometry]
    water_labels: list[tuple[BaseGeometry, str]]
    barrier_labels: list[tuple[BaseGeometry, str]]
    classified: list[ClassifiedBuilding]
    enterable: list[BaseGeometry]
    # 图版本号：注入百度对照边会增长图节点，node_costs 缓存键须含版本号，
    # 否则旧 costs（短数组）配新图（长边表）会在 _reachable_edges 越界。
    # v2：高速/快速路 barrier 不再阻隔步行图（交叉口立交可穿越）——
    # 旧 costs 基于割裂图，必须整体失效重算
    graph_version: int = 2
    # lite 精简档（beijing6r）：无 buildings 层——walls/classified/enterable 为空，
    # 障碍并集只含水域；checkup 侧跳过建筑相关语义并标记 meta.fabricLite
    lite: bool = False
    # 启动期预融合的障碍物并集（walls+water）：fast/full 请求路径直接 difference，
    # 避免每次请求对数千墙厚区重新 unary_union（真实 OSM 建筑下是主要耗时）
    obstacles: BaseGeometry | None = None
    # green 面（公园/绿地等，本地米坐标）：POI 面感知「边缘到达」用
    green: list[BaseGeometry] = field(default_factory=list)


@dataclass
class EngineContext:
    settings: Settings
    service: MapFabricService
    regions: dict[str, RegionBundle]
    baidu: BaiduClient
    disk: DiskCache
    # 内存缓存：节点级 costs 与 full 结果，key=(region, node_id, minutes, graph_version[, blindFilter])
    node_costs: dict[tuple, list[float]] = field(default_factory=dict)
    node_results: dict[tuple, dict] = field(default_factory=dict)
    # 已注入的百度对照折线：key=region|route|geohash5 -> 注入边数（幂等，防重复注入）
    baidu_injected: dict[str, int] = field(default_factory=dict)
    # 六环全量网格片懒加载（b6r_* 目录缺失时为 None，如测试/精简部署）
    tiles: TileManager | None = None


def _region_center(service: MapFabricService, name: str) -> tuple[float, float, float, float]:
    loaded = service._regions[name]  # 仓库内自用，读取 bbox
    return loaded.bbox_wgs84


def _build_bundle(
    service: MapFabricService,
    name: str,
    *,
    lite: bool = False,
    extra_radius_m: float = 0.0,
) -> RegionBundle:
    """装载 region 目录并构建完整 bundle（load → clip → 分类 → 建图 → 障碍并集）。

    build_engine 启动预热与 TileManager 懒加载共用。extra_radius_m 用于
    网格片：片内容覆盖 bbox 外扩 TILE_PAD_M 的邻域，裁切半径须含 pad。
    阻塞型（秒级），TileManager 侧放线程池调用。
    """
    t0 = time.perf_counter()
    service.load_region(name)
    x0, y0, x1, y1 = _region_center(service, name)
    center = ((x0 + x1) / 2.0, (y0 + y1) / 2.0)
    radius = haversine_m(center[0], center[1], x0, y0) + 300.0 + extra_radius_m
    # 按调用方指定的 region 名直裁：网格片 bbox 因水系长线可能异常宽，
    # clip() 按中心点猜 region 会命中别的片（通州点裁到西城路网的事故）
    layers = service.clip_region(name, center, radius)
    # lite 档无 buildings 层，跳过建筑分类（walls/enterable 自然为空）
    classified = [] if lite else classify_buildings(layers.buildings)
    graph = build_graph(layers.roads, classified, layers.water, layers.barriers, center, radius)
    walls = [b.wall for b in classified if b.wall is not None and not b.wall.is_empty]
    water = [g for g, _ in layers.water if not g.is_empty]
    water_labels = [
        (g, str(props.get("name") or "水域")) for g, props in layers.water if not g.is_empty
    ]
    barrier_labels = [
        (g, str(props.get("name") or "快速路")) for g, props in layers.barriers if not g.is_empty
    ]
    green = [g for g, _ in layers.green if not g.is_empty]
    enterable = [
        b.geometry for b in classified if b.category == CATEGORY_ENTERABLE and not b.geometry.is_empty
    ]
    obstacles = unary_union([g for g in (*walls, *water) if not g.is_empty]) if (walls or water) else None
    bundle = RegionBundle(
        name=name,
        center_wgs=center,
        graph=graph,
        walls=walls,
        water=water,
        water_labels=water_labels,
        barrier_labels=barrier_labels,
        classified=classified,
        enterable=enterable,
        lite=lite,
        obstacles=obstacles,
        green=green,
    )
    logger.info(
        "region %s built: nodes=%d edges=%d in %.0fms",
        name,
        graph.node_count,
        graph.edge_count,
        (time.perf_counter() - t0) * 1000,
    )
    return bundle


class TileManager:
    """六环全量网格片懒加载：LRU 常驻 + 同片并发防重。

    片目录 ``<fabric_root>/b6r_rXXcYY/`` 由 scripts/tile_fabric.py 生成，
    内容覆盖片 bbox 外扩 TILE_PAD_M 的邻域（跨片复制、不裁几何），保证
    片内任意点位的等时圈不因片边界截断。ensure() 命中 LRU 直接返回；
    未命中在片级锁内构建（load+clip+建图约 1.5~5s，放线程池避免阻塞
    事件循环）；超出 max_tiles 淘汰最久未用片，并从 service 卸载底图
    释放内存。
    """

    def __init__(self, service: MapFabricService, root: Path, *, max_tiles: int = 8) -> None:
        self.service = service
        self.root = root
        self.max_tiles = max_tiles
        self._bundles: OrderedDict[str, RegionBundle] = OrderedDict()
        self._locks: dict[str, asyncio.Lock] = {}

    def has_tile(self, lng: float, lat: float) -> bool:
        row, col = tile_indices(lng, lat)
        return (self.root / tile_name(row, col)).is_dir()

    async def ensure(self, lng: float, lat: float) -> RegionBundle | None:
        row, col = tile_indices(lng, lat)
        name = tile_name(row, col)
        bundle = self._bundles.get(name)
        if bundle is not None:
            self._bundles.move_to_end(name)
            return bundle
        if not (self.root / name).is_dir():
            return None
        lock = self._locks.setdefault(name, asyncio.Lock())
        async with lock:
            bundle = self._bundles.get(name)
            if bundle is None:
                bundle = await asyncio.to_thread(
                    _build_bundle, self.service, name, extra_radius_m=TILE_PAD_M
                )
                self._bundles[name] = bundle
                self._evict()
        return bundle

    def _evict(self) -> None:
        while len(self._bundles) > self.max_tiles:
            old, _ = self._bundles.popitem(last=False)
            self.service._regions.pop(old, None)
            logger.info("tile evicted: %s (loaded=%d)", old, len(self._bundles))


def build_engine(settings: Settings) -> EngineContext:
    service = MapFabricService(settings.fabric_root)
    regions: dict[str, RegionBundle] = {}
    for name in KNOWN_REGIONS:
        region_dir = settings.fabric_root / name
        if not region_dir.is_dir():
            logger.warning("fabric region missing, skipped: %s", region_dir)
            continue
        regions[name] = _build_bundle(service, name, lite=name in LITE_REGIONS)

    ctx = EngineContext(
        settings=settings,
        service=service,
        regions=regions,
        baidu=BaiduClient(settings.baidu_ak, cache_dir=settings.cache_dir / "baidu"),
        disk=DiskCache(settings.cache_dir, settings.cache_ttl_s),
        tiles=TileManager(service, settings.fabric_root),
    )

    # demo/无 AK：启动即注入劲松演示对照折线（真实 DirectionLite 预生成，见 demo_data），
    # 保证图状态从 t0 起确定——若留到首次请求才注入，首算用干净图、后续请求全用
    # 增广图（注入边权重 0.9 更优先，等时圈可扩大约 1/3），同一中心的结果会随
    # 请求顺序漂移（compare_report 多 case 串跑数字不一致的根因）。
    # key 用 region|name（折线固定、与请求中心无关），请求侧幂等跳过不重复注入。
    if (settings.demo_mode or not settings.baidu_ak) and "jinsong" in regions:
        bundle = regions["jinsong"]
        # 折线坐标原点 = REGION_CENTERS["jinsong"]（见 demo_data 注释），
        # 与 checkup._baidu_compare_routes 的换算保持同一投影原点
        proj = LocalProjection(*REGION_CENTERS["jinsong"])
        total = 0
        for name, coords_xy in JINSONG_DEMO_ROUTES:
            key = f"jinsong|{name}"
            injected = inject_baidu_polyline(
                bundle.graph, [proj.to_lnglat(x, y) for x, y in coords_xy]
            )
            ctx.baidu_injected[key] = injected
            total += injected
        if total:
            bundle.graph_version += 1
            logger.info("demo routes pre-injected: edges=%d graph_version=%d", total, bundle.graph_version)

    return ctx


async def pick_region(ctx: EngineContext, lng: float, lat: float) -> tuple[RegionBundle, bool]:
    """选 region：六环全量网格片优先（ring6 多边形判定 + TileManager 懒加载，
    未加载时现场构建约 2~5s）→ 全量精修 patch（bbox 命中，覆盖无切片部署）
    → 挂载 lite region（测试兼容回退）→ 都不覆盖时取最近的全量 patch 并标记
    degraded。

    网格片必须优先于 patch bbox：遗留样例 patch 的 bbox 远大于其样例路网
    实际覆盖（如 jinsong bbox 约 7×5.5km，路网仅样例核心区），bbox 命中
    会把覆盖缺口内的点位交给稀疏图，造成 POI 大面积吸附失败（全部按直线
    估算标「圈外」、最近步行「暂无」、误判缺口）。

    demo 部署（DEMO_MODE / 无 AK）例外：demo 对照折线注入与劲松样例语义
    绑定 jinsong patch，保持原「patch bbox 优先」次序。
    网格片不参与 degraded 兜底：六环外点位吸附到片图最近节点没有意义，
    交给最近 patch（snapshot/演示语义一致）或走廊近似处理。
    """
    demo_mode = bool(getattr(ctx, "settings", None) and ctx.settings.demo_mode) or bool(
        getattr(ctx, "baidu", None) and not ctx.baidu.enabled
    )
    if not demo_mode and in_ring6(lng, lat):
        tiles = getattr(ctx, "tiles", None)
        if tiles is not None:
            bundle = await tiles.ensure(lng, lat)
            if bundle is not None:
                return bundle, False
    for name, bundle in ctx.regions.items():
        if bundle.lite:
            continue
        x0, y0, x1, y1 = _region_center(ctx.service, name)
        if x0 <= lng <= x1 and y0 <= lat <= y1:
            return bundle, False
    if in_ring6(lng, lat):
        # 兼容回退：无 TileManager 但 ctx.regions 显式挂载了 lite region（测试）
        for bundle in ctx.regions.values():
            if bundle.lite and in_ring6(lng, lat):
                return bundle, False
    if not ctx.regions:
        raise RuntimeError("no map fabric regions loaded")
    full = [b for b in ctx.regions.values() if not b.lite]
    bundle = min(
        full or list(ctx.regions.values()),
        key=lambda b: haversine_m(lng, lat, *b.center_wgs),
    )
    return bundle, True
