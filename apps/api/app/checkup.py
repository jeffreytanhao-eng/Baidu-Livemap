"""X 分钟生活圈体检编排。

- 主圈绝不调用百度 RouteMatrix / 百度等时圈；POI 步行分钟直接查同一次
  cutoff_dijkstra 的 costs 数组；
- phase=fast 只跑 fast_mask（目标 <300ms）；phase=full 一次 Dijkstra 出全部层；
- 内存节点缓存 key=(region, nodeId, minutes) 存 full 结果；整响应磁盘缓存
  key=中心坐标(1e-5 度)+minutes+engine（TTL 24h）；
- DEMO_MODE 或无 AK：优先回 data/samples 快照，否则合成 POI + 内存图现场算，
  一律标 meta.demoMode=true；
- 输入统一转 WGS84 计算，输出一律 BD09LL（与百度底图套合）。
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
import math
import time
from typing import Any

from geo import (
    BLINDSPOT_RADIUS_M,
    DEFAULT_CATEGORIES,
    GRID_CELL_M,
    bd09ll_to_wgs84,
    blindspots,
    build_isochrone,
    circle_radius_m,
    cutoff_dijkstra,
    fast_mask,
    geohash5,
    haversine_m,
    inject_baidu_polyline,
    search_radius,
    sector_gap_report,
    wgs84_to_bd09ll,
)
from geo.coords import LocalProjection
from shapely.geometry import GeometryCollection, LineString, Point, mapping
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform as shp_transform
from shapely.ops import unary_union

from .baidu import BaiduError, NotAvailableError
from .cache import disk_cache_key
from .coverage import (
    ANCHOR3,
    BAIDU_QUERIES,
    EDU_QUERIES,
    TRANSPORT_QUERIES,
    attach_walk_minutes,
    calibrate_walk_minutes,
    clean_pois,
    compute_coverage,
    select_within_pois,
)
from .demo_data import (
    JINSONG_DEMO_ROUTES,
    REGION_CENTERS,
    SNAPSHOT_MAX_DIST_M,
    nearest_sample,
    snapshot_path,
)
from .engine import EngineContext, RegionBundle, pick_region
from .geojson import feature, geometry_wgs84_to_bd09ll
from .llm import diagnose as llm_diagnose
from .poi_synth import synth_pois
from .whitelist import in_whitelist

logger = logging.getLogger("livemap.checkup")

_LAYER_BASE = (5, 10, 15, 20)

# geo.sector 的角度定义：自 +x 轴（东）逆时针
_DIR_ANGLE = {
    "E": 0.0,
    "NE": 45.0,
    "N": 90.0,
    "NW": 135.0,
    "W": 180.0,
    "SW": 225.0,
    "S": 270.0,
    "SE": 315.0,
}

_REGION_DENSITY = {"jinsong": 1.0, "zhongguancun": 1.4, "nanyuan": 0.65}

# 结果缓存 schema 版本：分类体系等响应结构变化时 +1，旧缓存自然失配
CACHE_SCHEMA = "cat13"  # 13：类别更名（医疗养老/商超购物）+ health 扩词表（旧缓存名/词表失效）

# 演示快照精确档位之外的分钟，统一回退到 15 分钟快照
_SNAPSHOT_FALLBACK_MINUTES = 15

# 盲区网格总格数（与 geo.blindspot 的写死口径一致：1000m 半径 / 100m 格）
_BLIND_TOTAL_CELLS = sum(
    1
    for ix in range(-int(BLINDSPOT_RADIUS_M / GRID_CELL_M), int(BLINDSPOT_RADIUS_M / GRID_CELL_M) + 1)
    for iy in range(-int(BLINDSPOT_RADIUS_M / GRID_CELL_M), int(BLINDSPOT_RADIUS_M / GRID_CELL_M) + 1)
    if math.hypot(ix * GRID_CELL_M, iy * GRID_CELL_M) <= BLINDSPOT_RADIUS_M
)


def layer_minutes(minutes: int) -> list[int]:
    """时间分层：15→[5,10,15]；20→[5,10,15,20]；5→[3,5]；10→[5,10]；
    其它 2-4 层且必含 X。"""
    if minutes <= 5:
        return [3, 5]
    return [m for m in _LAYER_BASE if m < minutes][-3:] + [minutes]


def _nearest_node(graph, lng: float, lat: float) -> tuple[int, float]:
    """snap 失败兜底：全表扫描最近图节点，返回 (node_id, 距离 m)。"""
    x, y = graph.proj.to_xy(lng, lat)
    best = 0
    best_d2 = math.inf
    for nid, (nx, ny) in enumerate(graph.nodes):
        d2 = (nx - x) ** 2 + (ny - y) ** 2
        if d2 < best_d2:
            best_d2 = d2
            best = nid
    return best, math.sqrt(best_d2)


def _sample_points(geom: BaseGeometry, step_m: float = 100.0) -> list[tuple[float, float]]:
    """沿线/环按里程采样点（阻隔物方向判定用，避免长边只有端点被漏判）。"""
    lines: list[BaseGeometry] = []
    if geom.geom_type == "LineString":
        lines = [geom]
    elif geom.geom_type == "Polygon":
        lines = [geom.exterior]
    elif geom.geom_type in ("MultiLineString", "MultiPolygon", "GeometryCollection"):
        for part in geom.geoms:
            if part.geom_type == "Polygon":
                lines.append(part.exterior)
            elif part.geom_type == "LineString":
                lines.append(part)
    pts: list[tuple[float, float]] = []
    for line in lines:
        n = max(1, int(line.length / step_m))
        for k in range(n + 1):
            pt = line.interpolate(min(line.length, k * step_m))
            pts.append((pt.x, pt.y))
    return pts


def _barrier_labels_by_direction(
    bundle: RegionBundle, center_xy: tuple[float, float], radius_m: float
) -> dict[str, list[str]]:
    """把水域/快速路名称挂到 8 方位（只统计直线圆范围内的部分）。"""
    cx, cy = center_xy
    out: dict[str, list[str]] = {}

    def add(geom: BaseGeometry, label: str) -> None:
        for px, py in _sample_points(geom):
            dx, dy = px - cx, py - cy
            if math.hypot(dx, dy) > radius_m:
                continue
            ang = math.degrees(math.atan2(dy, dx)) % 360.0
            direction = min(
                _DIR_ANGLE,
                key=lambda d: abs(((ang - _DIR_ANGLE[d] + 180.0) % 360.0) - 180.0),
            )
            labels = out.setdefault(direction, [])
            if label not in labels:
                labels.append(label)

    for geom, name in bundle.water_labels:
        add(geom, name)
    for geom, name in bundle.barrier_labels:
        add(geom, name)
    return out


def _build_meta(
    *,
    minutes: int,
    engine: str,
    demo_mode: bool,
    degraded: bool,
    whitelisted: bool,
    center_wgs: tuple[float, float],
    elapsed_ms: int,
    isochrone_area: float,
    circle_area: float,
    snap_note: str | None,
    fabric_lite: bool = False,
) -> dict:
    bd = wgs84_to_bd09ll(*center_wgs)
    meta = {
        "minutes": minutes,
        "engine": engine,
        "demoMode": demo_mode,
        "degraded": degraded,
        "fabricLite": fabric_lite,
        "cityWhitelist": whitelisted,
        "methodOnly": not whitelisted,
        "center": {"lng": bd[0], "lat": bd[1]},
        "elapsedMs": elapsed_ms,
        "isochroneArea": isochrone_area,
        "circleArea": circle_area,
        "areaRatio": min(1.0, isochrone_area / circle_area) if circle_area > 0 else 0.0,
    }
    if snap_note:
        meta["snapNote"] = snap_note
    return meta


def _read_snapshot(path) -> dict | None:
    try:
        with path.open(encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError) as exc:
        logger.warning("snapshot read failed: %s", exc)
        return None


def _load_snapshot(ctx: EngineContext, center_wgs: tuple[float, float], minutes: int) -> dict | None:
    """最近样例的分钟精确快照；非精确档回退到 15 分钟快照并改写 meta/diagnosis。"""
    sample, dist = nearest_sample(*center_wgs)
    if dist > SNAPSHOT_MAX_DIST_M:
        return None
    exact = snapshot_path(ctx.settings.samples_dir, sample.region, minutes)
    if exact.exists():
        return _read_snapshot(exact)
    fallback = snapshot_path(ctx.settings.samples_dir, sample.region, _SNAPSHOT_FALLBACK_MINUTES)
    if not fallback.exists():
        return None
    payload = _read_snapshot(fallback)
    if payload is None:
        return None
    # 其他分钟：用 15 分钟快照改 meta.minutes，并在 diagnosis 注明
    meta = {**payload["meta"], "minutes": minutes}
    note = f"演示数据按 {_SNAPSHOT_FALLBACK_MINUTES} 分钟生活圈计算，仅供参考。"
    diagnosis = [note, *[d for d in payload.get("diagnosis", []) if d != note]][:6]
    return {**payload, "meta": meta, "diagnosis": diagnosis}


async def _fetch_pois(
    ctx: EngineContext,
    bundle: RegionBundle,
    center_wgs: tuple[float, float],
    minutes: int,
    demo_mode: bool,
) -> tuple[list[dict[str, Any]], bool]:
    """有 AK 走 placeSearch（radius=max(1500, 90*X)），否则确定性合成。

    返回 ``(pois, poi_ok)``：``poi_ok=False`` 表示百度检索失败降级为合成
    （如 AK 天配额超限）——结果不可信，调用方不得写入结果缓存，否则
    配额恢复后同中心同档位会命中被污染的合成数据。
    """
    radius = float(search_radius(minutes))
    poi_ok = True
    if ctx.baidu.enabled and not demo_mode:
        try:
            # place_search 的 location 按 bd09ll 语义（Place API coord_type 默认 3），
            # 必须先转 BD09，否则检索圆心整体偏移约 550m（WGS84/BD09 差）。
            center_bd = wgs84_to_bd09ll(*center_wgs)
            # 5 类常规词 + education/transport 双检索（学校/幼儿园、地铁站/公交站，标 eduKind/transportKind）
            jobs: list[tuple[str, str | None, str]] = [
                (cat, None, query) for cat, query in BAIDU_QUERIES.items()
            ] + [("education", kind, query) for kind, query in EDU_QUERIES.items()] + [
                ("transport", kind, query) for kind, query in TRANSPORT_QUERIES.items()
            ]
            gathered = await asyncio.gather(
                *(
                    ctx.baidu.place_search(query, center_bd, radius)
                    for _, _, query in jobs
                )
            )
        except BaiduError as exc:
            logger.warning("place_search failed, fallback to synthetic pois: %s", exc)
            poi_ok = False
        else:
            raw: list[dict[str, Any]] = []
            for (cat, kind, _), results in zip(jobs, gathered, strict=True):
                for item in results:
                    loc = item.get("location") or {}
                    lng, lat = loc.get("lng"), loc.get("lat")
                    if lng is None or lat is None:
                        continue
                    w_lng, w_lat = bd09ll_to_wgs84(float(lng), float(lat))
                    uid = str(item.get("uid") or f"bd-{cat}-{len(raw)}")
                    poi: dict[str, Any] = {
                        "id": uid,
                        "uid": uid,
                        "name": str(item.get("name") or ""),
                        "category": cat,
                        "lng": w_lng,
                        "lat": w_lat,
                    }
                    # 百度标准类别（scope=2 detail_info.tag，如「房地产;写字楼」），
                    # public 清洗用它剔除伪装成「XX广场」的写字楼/商场
                    baidu_tag = str((item.get("detail_info") or {}).get("tag") or "")
                    if baidu_tag:
                        poi["baiduTag"] = baidu_tag
                    if kind:
                        if cat == "education":
                            poi["eduKind"] = kind
                        else:
                            poi["transportKind"] = kind
                    raw.append(poi)
            return clean_pois(raw), True
    seed = f"{geohash5(*center_wgs)}|{bundle.name}"
    density = _REGION_DENSITY.get(bundle.name, 1.0)
    pois = synth_pois(
        center_wgs,
        seed=seed,
        graph=bundle.graph,
        enterable=bundle.enterable,
        density=density,
        radius_m=radius,
        # 合成 POI 只落在等时圈内（圈内选取口径下圈外召回无意义）
        walk_budget_s=minutes * 60.0,
    )
    return clean_pois(pois), poi_ok


async def _measure_walk_minutes(
    ctx: EngineContext,
    center_wgs: tuple[float, float],
    poi_views: list[dict[str, Any]],
    per_category: int = 20,
) -> dict[str, float]:
    """RouteMatrix 实测步行分钟：每类取直线最近的 N 个 POI（对齐规格「每类最多 20 个算路」）。

    真实模式下底图（预裁切/合成）与真实 POI 坐标错位，图上吸附不可靠；
    用百度实测耗时做覆盖判定（同时体现 API 深度）。N=20 时 ≤160 dest，
    自动切 4 批。返回 poi id -> 步行分钟；失败抛 BaiduError 由调用方降级。
    """
    by_cat: dict[str, list[dict[str, Any]]] = {}
    for p in poi_views:
        by_cat.setdefault(str(p["category"]), []).append(p)
    picked: list[dict[str, Any]] = []
    for items in by_cat.values():
        items.sort(key=lambda p: haversine_m(p["_wgs"][0], p["_wgs"][1], *center_wgs))
        picked.extend(items[:per_category])
    if not picked:
        return {}
    origin_bd = wgs84_to_bd09ll(*center_wgs)
    dests_bd = [(float(p["lng"]), float(p["lat"])) for p in picked]  # view 内已是 BD09LL
    rows = await ctx.baidu.walking_matrix([origin_bd], dests_bd)
    out: dict[str, float] = {}
    for poi, row in zip(picked, rows, strict=False):
        duration = (row or {}).get("duration") or {}
        value = duration.get("value")
        if isinstance(value, (int, float)) and value >= 0:
            out[str(poi["id"])] = float(value) / 60.0
    return out


def _blind_summary(blind_fc: dict) -> dict:
    missing_cells = {cat: 0 for cat in ANCHOR3}
    for feat in blind_fc.get("features", []):
        props = feat.get("properties") or {}
        for cat in props.get("missing") or []:
            if cat in missing_cells:
                missing_cells[cat] += int(props.get("cells") or 0)
    total = max(1, _BLIND_TOTAL_CELLS)
    return {
        "conveniencePct": missing_cells["convenience"] / total,
        "healthPct": missing_cells["health"] / total,
        "educationPct": missing_cells["education"] / total,
    }


# 百度对照折线注入成功时固定追加的诊断句
BAIDU_COMPARE_NOTE = "与百度步行路线大体重合；本圈沿采样路网等时计算，边界更保守。"


def _parse_directionlite_path(data: dict) -> list[tuple[float, float]]:
    """解析 DirectionLite 返回的步行折线（steps[].path = 'lng,lat;lng,lat'）。"""
    routes = (data.get("result") or {}).get("routes") or []
    if not routes:
        return []
    pts: list[tuple[float, float]] = []
    for step in routes[0].get("steps") or []:
        for pair in str(step.get("path") or "").split(";"):
            if not pair:
                continue
            try:
                lng_s, lat_s = pair.split(",")
                pt = (float(lng_s), float(lat_s))
            except ValueError:
                continue
            if not pts or pts[-1] != pt:
                pts.append(pt)
    return pts


# 走廊近似等时圈参数（degraded + 百度可用时）
_CORRIDOR_BUFFER_M = 50.0  # 路线走廊半宽
_CORRIDOR_HUB_M = 100.0  # 中心缓冲半径
_CORRIDOR_MIN_DIRECTIONS = 3  # 可用方向少于该数不成圈


def _geom_local_to_wgs(geom: BaseGeometry, proj: LocalProjection) -> dict:
    """本地米坐标几何 → WGS84 GeoJSON（编排层输出一律走 GeoJSON dict）。"""
    return mapping(shp_transform(lambda x, y, z=None: proj.to_lnglat(x, y), geom))


async def _corridor_iso(
    ctx: EngineContext, center_wgs: tuple[float, float], minutes: int
) -> dict[str, Any] | None:
    """区域外（degraded）且百度可用：8 方向步行路线走廊近似等时斑。

    预置路网裁片只覆盖演示区域，其他点位用最近区域底图近似会得到完全
    错位的等时圈（吸附到几公里外的节点）。改为以请求中心为原点向 8 方向
    各取一条 DirectionLite 步行路线（终点=该方向直线圆边界），沿折线按
    80 m/min 累计距离、在各层 cutoff 截断，缓冲 50m 取并集——走廊天然
    沿真实步行路网、绕开水域/快速路，语义与约束等时圈一致。
    返回 {"geoms": {minutes: 本地米坐标几何}, "proj": proj}；可用方向
    不足或主层缺失时返回 None（调用方回退最近区域近似）。
    """
    center_bd = wgs84_to_bd09ll(*center_wgs)
    mins = layer_minutes(minutes)
    max_r = circle_radius_m(max(mins))
    lat_rad = math.radians(center_wgs[1])
    dests = [
        wgs84_to_bd09ll(
            center_wgs[0] + max_r * math.cos(math.radians(_DIR_ANGLE[d])) / (111320.0 * math.cos(lat_rad)),
            center_wgs[1] + max_r * math.sin(math.radians(_DIR_ANGLE[d])) / 111320.0,
        )
        for d in _DIR_ANGLE
    ]
    results = await asyncio.gather(
        *(ctx.baidu.walking_route(center_bd, dest) for dest in dests),
        return_exceptions=True,
    )
    proj = LocalProjection(*center_wgs)
    lines: list[list[tuple[float, float]]] = []
    for res in results:
        if isinstance(res, BaseException):
            continue
        pts_bd = _parse_directionlite_path(res)
        if len(pts_bd) < 2:
            continue
        pts_wgs = [bd09ll_to_wgs84(lng, lat) for lng, lat in pts_bd]
        lines.append([proj.to_xy(lng, lat) for lng, lat in pts_wgs])
    if len(lines) < _CORRIDOR_MIN_DIRECTIONS:
        return None
    geoms: dict[int, BaseGeometry] = {}
    for m in mins:
        cutoff = circle_radius_m(m)
        parts: list[LineString] = []
        for pts in lines:
            seg = [pts[0]]
            acc = 0.0
            for prev, cur in itertools.pairwise(pts):
                step = math.dist(prev, cur)
                if acc + step <= cutoff:
                    seg.append(cur)
                    acc += step
                    continue
                ratio = (cutoff - acc) / step if step > 0 else 0.0
                seg.append(
                    (prev[0] + (cur[0] - prev[0]) * ratio, prev[1] + (cur[1] - prev[1]) * ratio)
                )
                break
            if len(seg) >= 2:
                parts.append(LineString(seg))
        if not parts:
            continue
        geoms[m] = unary_union(
            [ln.buffer(_CORRIDOR_BUFFER_M) for ln in parts]
            + [Point(0.0, 0.0).buffer(_CORRIDOR_HUB_M)]
        )
    if minutes not in geoms:
        return None
    return {"geoms": geoms, "proj": proj}


async def _baidu_compare_routes(
    ctx: EngineContext,
    bundle: RegionBundle,
    center_wgs: tuple[float, float],
    poi_views: list[dict[str, Any]],
    demo_mode: bool,
) -> list[dict]:
    """第三拍：百度步行对照折线（主圈 costs 不变、不重算；任何失败静默）。

    有 AK 且非 demo：中心→最近 1-2 个重点 POI 调 DirectionLite 取真实折线；
    demo/无 AK：劲松用 DirectionLite 预生成的真实折线（见 demo_data）演示注入，
    该批折线已在引擎预热时注入（engine.build_engine），此处幂等回填 baiduRoutes。
    折线拆成 source=baidu 高权边注入图（幂等），输出 BD09LL 供前端叠看。
    """
    named_paths: list[tuple[str, list[tuple[float, float]]]] = []  # (name, wgs84 path)
    if ctx.baidu.enabled and not demo_mode:
        center_bd = wgs84_to_bd09ll(*center_wgs)
        targets = [
            p
            for p in poi_views
            if p.get("category") in ANCHOR3 and p.get("_raw") is not None
        ]
        targets.sort(key=lambda p: float(p["_raw"]))
        for poi in targets[:2]:
            try:
                dest_bd = wgs84_to_bd09ll(poi["_wgs"][0], poi["_wgs"][1])
                data = await ctx.baidu.walking_route(center_bd, dest_bd)
            except BaiduError as exc:
                logger.info("walking_route failed, skipped: %s", exc)
                continue
            path_bd = _parse_directionlite_path(data)
            if len(path_bd) >= 2:
                named_paths.append(
                    (
                        f"百度步行→{poi['name']}",
                        [bd09ll_to_wgs84(lng, lat) for lng, lat in path_bd],
                    )
                )
    elif bundle.name == "jinsong":
        proj = LocalProjection(*REGION_CENTERS["jinsong"])
        for name, coords_xy in JINSONG_DEMO_ROUTES:
            named_paths.append((name, [proj.to_lnglat(x, y) for x, y in coords_xy]))

    out: list[dict] = []
    # demo 固定折线与请求中心无关（启动预热已注入同批折线，见 engine.build_engine），
    # key 不含 geohash5，保证与预热 key 一致、幂等跳过；有 AK 的按中心取目标，保留 geohash5。
    demo_fixed = not (ctx.baidu.enabled and not demo_mode)
    for name, path_wgs in named_paths:
        if demo_fixed:
            key = f"{bundle.name}|{name}"
        else:
            key = f"{bundle.name}|{name}|{geohash5(*center_wgs)}"
        injected = ctx.baidu_injected.get(key)
        if injected is None:
            injected = inject_baidu_polyline(bundle.graph, path_wgs)
            ctx.baidu_injected[key] = injected
            if injected > 0:
                # 图已增长：旧 node_costs 一律作废（缓存键含版本号）
                bundle.graph_version += 1
        out.append(
            {
                "name": name,
                "path": [
                    [round(lng, 6), round(lat, 6)]
                    for lng, lat in (wgs84_to_bd09ll(*pt) for pt in path_wgs)
                ],
                "injectedEdges": injected,
            }
        )
    return out


def _diagnosis(
    *,
    coverage: dict,
    minutes: int,
    isochrone_area: float,
    circle_area: float,
    sector_report,
    enclaves_count: int,
    blind_summary: dict,
    degraded: bool,
    method_only: bool,
) -> list[str]:
    """模板化白话诊断 3-6 条；扇区句以 'sector:XX ' 前缀开头供前端互跳。"""
    out: list[str] = []
    by_id = {c["id"]: c for c in coverage["categories"]}
    for cat in ANCHOR3:
        c = by_id[cat]
        if c["nearestMinutes"] is None:
            out.append(f"{c['name']}缺口：检索半径内没有找到{c['name']}设施。")
        elif not c["passed"]:
            out.append(
                f"最近的{c['name']}步行约 {c['nearestMinutes']:.0f} 分钟，"
                f"超过 {minutes} 分钟目标。"
            )
    ratio = isochrone_area / circle_area if circle_area > 0 else 0.0
    out.append(
        f"{minutes} 分钟约束等时圈面积约 {isochrone_area / 1e6:.2f} km²，"
        f"为直线对照圆的 {ratio:.0%}。"
    )
    if sector_report.diagnosis:
        out.append(f"sector:{sector_report.worst_direction} {sector_report.diagnosis}")
    if enclaves_count:
        out.append(f"等时圈边缘有 {enclaves_count} 块飞地，需经桥梁或天桥绕行到达。")
    out.append(
        "1km 网格盲区占比："
        f"便利生活 {blind_summary['conveniencePct']:.0%}、"
        f"医疗养老 {blind_summary['healthPct']:.0%}、"
        f"学习教育 {blind_summary['educationPct']:.0%}。"
    )
    if degraded:
        out.append("当前位置没有预置底图，已借用最近区域底图近似计算，与百度步行结果可能有偏差。")
    if method_only:
        out.append("该城市未做设施词表校准，结果仅用于方法验证。")
    while len(out) < 3:
        out.append("该区域数据有限，建议结合实地走访确认。")
    return out[:6]


def _serve_cached(payload: dict, elapsed_ms: int) -> dict:
    """缓存/快照命中：只刷新 elapsedMs，不深拷贝其余字段（不会被修改）。"""
    return {**payload, "meta": {**payload["meta"], "elapsedMs": elapsed_ms}}


def _apply_blind_filter_to_snapshot(
    ctx: EngineContext,
    bundle: RegionBundle,
    center_wgs: tuple[float, float],
    minutes: int,
    snapshot: dict,
    blind_filter: list[str],
) -> dict:
    """demo 快照路径的盲区筛选：只重算盲区层与汇总，其余字段沿用快照。

    快照 pois 是 BD09LL 展示坐标且无 ``_raw``，这里转回 WGS84 后吸附查
    （缓存的）同一次 cutoff_dijkstra costs，保证口径与现场计算一致。
    """
    graph = bundle.graph
    node = graph.snap(*center_wgs)
    if node is None:
        node, _ = _nearest_node(graph, *center_wgs)
    node_key = (bundle.name, node, minutes, bundle.graph_version)
    costs = ctx.node_costs.get(node_key)
    if costs is None:
        costs = cutoff_dijkstra(graph, node, minutes * 60.0)
        ctx.node_costs[node_key] = costs
    blind_input = []
    for p in snapshot.get("pois", []):
        w_lng, w_lat = bd09ll_to_wgs84(p["lng"], p["lat"])
        nid = graph.snap(w_lng, w_lat)
        raw = (
            costs[nid] / 60.0
            if nid is not None and not math.isinf(costs[nid])
            else math.inf
        )
        blind_input.append(
            {
                "category": p["category"],
                "lng": w_lng,
                "lat": w_lat,
                "walk_minutes": raw,
            }
        )
    blind_fc = blindspots(
        center_wgs,
        blind_input,
        minutes=minutes,
        enhanced=True,
        categories=tuple(blind_filter),
    )
    blind_fc = {
        "type": "FeatureCollection",
        "features": [
            {**f, "geometry": geometry_wgs84_to_bd09ll(f["geometry"])}
            for f in blind_fc.get("features", [])
        ],
    }
    return {
        **snapshot,
        "blindSpots": blind_fc,
        "blindSummary": _blind_summary(blind_fc),
    }


async def run_checkup(
    ctx: EngineContext,
    *,
    center_wgs: tuple[float, float],
    minutes: int,
    engine: str,
    include_blind_walk: bool,
    phase: str,
    include_official: bool = False,
    allow_snapshot: bool = True,
    blind_filter: list[str] | None = None,
) -> dict:
    t0 = time.perf_counter()
    api_calls_before = ctx.baidu.api_calls

    bundle, degraded = await pick_region(ctx, *center_wgs)
    graph = bundle.graph
    whitelisted = in_whitelist(*center_wgs)
    demo_mode = ctx.settings.demo_mode or not ctx.baidu.enabled or engine == "demo"

    # 演示快照优先（demoMode / 无 AK）
    if demo_mode and allow_snapshot:
        snapshot = _load_snapshot(ctx, center_wgs, minutes)
        if snapshot is not None:
            # 盲区筛选口径与默认复合不一致时，只重算盲区层（快照其余字段不变）
            if (
                phase == "full"
                and blind_filter is not None
                and tuple(sorted(blind_filter)) != tuple(sorted(DEFAULT_CATEGORIES))
            ):
                snapshot = _apply_blind_filter_to_snapshot(
                    ctx, bundle, center_wgs, minutes, snapshot, blind_filter
                )
            elapsed = round((time.perf_counter() - t0) * 1000)
            if phase == "fast":
                return {
                    "meta": _serve_cached(snapshot, elapsed)["meta"],
                    "fastLayer": snapshot["fastLayer"],
                }
            logger.info(
                "checkup minutes=%d api_calls=%d matrix_routes=0 elapsed_ms=%d degraded=%s (snapshot)",
                minutes,
                ctx.baidu.api_calls - api_calls_before,
                elapsed,
                degraded,
            )
            return _serve_cached(snapshot, elapsed)

    # 吸附：>30m 无节点时用最近节点兜底并注明
    node = graph.snap(*center_wgs)
    snap_note: str | None = None
    if node is None:
        node, dist = _nearest_node(graph, *center_wgs)
        snap_note = f"中心不在可走路上，已吸附到最近可走点（约 {dist:.0f} m）"
    if degraded:
        note = f"当前位置无预置底图，已使用最近的「{bundle.name}」区域底图近似"
        snap_note = f"{snap_note}；{note}" if snap_note else note
    if bundle.lite:
        note = "当前为六环内精简底图（lite）：等时圈沿真实路网计算，暂无建筑进入性数据。"
        snap_note = f"{snap_note}；{note}" if snap_note else note

    node_key = (bundle.name, node, minutes, bundle.graph_version)
    # 盲区筛选参与结果缓存键（costs 与筛选无关，仍按 node_key 缓存）；
    # None（默认复合）/ []（不算盲区）/ 子集 三种口径键必须互不相同
    if blind_filter is None:
        blind_key = ""
    elif not blind_filter:
        blind_key = "_empty_"
    else:
        blind_key = ",".join(sorted(blind_filter))
    # 结果键必须含请求中心（1e-5 度，与磁盘缓存同粒度）：POI 种子、meta.center、
    # cityWhitelist 等都是中心的函数，且 degraded 兜底会把相距千里的中心吸附到
    # 同一节点——只按 (region,node,minutes) 缓存会跨中心串结果。
    results_key = (*node_key, round(center_wgs[0] * 1e5), round(center_wgs[1] * 1e5), blind_key)
    cache_engine = f"{CACHE_SCHEMA}|{engine}|bf={blind_key}" if blind_key else f"{CACHE_SCHEMA}|{engine}"
    circle_area = math.pi * circle_radius_m(minutes) ** 2

    if phase == "full":
        hit = ctx.node_results.get(results_key)
        if hit is None:
            hit = ctx.disk.get(disk_cache_key(*center_wgs, minutes, cache_engine))
            if hit is not None:
                ctx.node_results[results_key] = hit
        if hit is not None:
            elapsed = round((time.perf_counter() - t0) * 1000)
            logger.info(
                "checkup minutes=%d api_calls=%d matrix_routes=0 elapsed_ms=%d degraded=%s (cache)",
                minutes,
                ctx.baidu.api_calls - api_calls_before,
                elapsed,
                degraded,
            )
            return _serve_cached(hit, elapsed)

    # 节点级 costs 缓存：一次 Dijkstra 出全部层
    costs = ctx.node_costs.get(node_key)
    if costs is None:
        costs = cutoff_dijkstra(graph, node, minutes * 60.0)
        ctx.node_costs[node_key] = costs

    # 障碍物用启动期预融合并集（单个几何），避免每次请求重算 unary_union
    obstacle_list = [bundle.obstacles] if bundle.obstacles is not None else []
    fast_res = fast_mask(graph, costs, minutes, obstacle_list, [])
    fast_layer = feature(
        geometry_wgs84_to_bd09ll(fast_res.polygon),
        {"minutes": minutes, "areaM2": fast_res.area_m2, "kind": "fast"},
    )

    if phase == "fast":
        elapsed = round((time.perf_counter() - t0) * 1000)
        meta = _build_meta(
            minutes=minutes,
            engine=engine,
            demo_mode=demo_mode,
            degraded=degraded,
            whitelisted=whitelisted,
            center_wgs=center_wgs,
            elapsed_ms=elapsed,
            isochrone_area=fast_res.area_m2,
            circle_area=circle_area,
            snap_note=snap_note,
            fabric_lite=bundle.lite,
        )
        logger.info(
            "checkup minutes=%d api_calls=%d matrix_routes=0 elapsed_ms=%d degraded=%s (fast)",
            minutes,
            ctx.baidu.api_calls - api_calls_before,
            elapsed,
            degraded,
        )
        return {"meta": meta, "fastLayer": fast_layer}

    # ---- phase=full ----
    # 区域外（degraded）且百度可用：8 方向步行路线走廊近似等时圈，
    # 替代「吸附到几公里外节点」的最近区域底图近似（完全错位不可用）。
    corridor = None
    if degraded and ctx.baidu.enabled and not demo_mode:
        try:
            corridor = await _corridor_iso(ctx, center_wgs, minutes)
        except BaiduError as exc:
            logger.warning(
                "corridor isochrone failed, fallback to nearest-region graph: %s", exc
            )

    iso_results = {
        m: build_isochrone(graph, costs, m, obstacle_list, [])
        for m in layer_minutes(minutes)
    }
    main_iso = iso_results[minutes]
    main_area = corridor["geoms"][minutes].area if corridor else main_iso.area_m2
    if corridor is not None:
        corridor_proj: LocalProjection = corridor["proj"]
        fast_layer = feature(
            geometry_wgs84_to_bd09ll(
                _geom_local_to_wgs(corridor["geoms"][minutes], corridor_proj)
            ),
            {"minutes": minutes, "areaM2": main_area, "kind": "fast"},
        )
        layers_out = [
            {
                "minutes": m,
                "polygon": feature(
                    geometry_wgs84_to_bd09ll(_geom_local_to_wgs(g, corridor_proj)),
                    {"minutes": m, "areaM2": g.area},
                ),
                "areaM2": g.area,
            }
            for m, g in sorted(corridor["geoms"].items())
        ]
        enclaves_out = []
        start_xy = (0.0, 0.0)
        barrier_labels: dict[str, list[str]] = {}
        iso_geom = corridor["geoms"][minutes]
        corridor_note = (
            "该位置超出预置路网覆盖（六环内 lite + 三个精修区域），"
            "等时圈由 8 方向百度步行路线走廊近似。"
        )
        snap_note = f"{snap_note}；{corridor_note}" if snap_note else corridor_note
    else:
        layers_out = [
            {
                "minutes": m,
                "polygon": feature(
                    geometry_wgs84_to_bd09ll(res.polygon),
                    {"minutes": m, "areaM2": res.area_m2},
                ),
                "areaM2": res.area_m2,
            }
            for m, res in sorted(iso_results.items())
        ]
        enclaves_out = [
            feature(geometry_wgs84_to_bd09ll(g), {"minutes": minutes}) for g in main_iso.enclaves
        ]
        start_xy = graph.nodes[node]
        barrier_labels = _barrier_labels_by_direction(bundle, start_xy, circle_radius_m(minutes))
        iso_geom = main_iso.geometry if main_iso.geometry is not None else GeometryCollection()
    sector_report = sector_gap_report(iso_geom, start_xy, minutes, barrier_labels)
    sector_gaps = []
    for gap in sector_report.gaps:
        item = {
            "direction": gap.direction,
            "isochroneArea": gap.isochroneArea,
            "circleArea": gap.circleArea,
            "gapRatio": gap.gapRatio,
        }
        labels = barrier_labels.get(gap.direction)
        if labels:
            item["barrierNote"] = "+".join(labels)
        sector_gaps.append(item)

    # POI + 覆盖打分（分母=请求 minutes）
    pois, poi_ok = await _fetch_pois(ctx, bundle, center_wgs, minutes, demo_mode)
    poi_views = attach_walk_minutes(graph, costs, pois, center_wgs, minutes, bundle.green)
    # 真实模式：底图与真实 POI 坐标错位时图上吸附不可靠，用 RouteMatrix 实测校准
    if ctx.baidu.enabled and not demo_mode:
        try:
            measured = await _measure_walk_minutes(ctx, center_wgs, poi_views)
        except BaiduError as exc:
            logger.warning("walking_matrix calibrate failed, keep graph times: %s", exc)
        else:
            if measured:
                poi_views = calibrate_walk_minutes(poi_views, measured, minutes)
    # 盲区输入：全量视图（圈外/不可达 = inf，本就不构成圈内覆盖），圈内选取之前取
    blind_input = [
        {
            "category": p["category"],
            "lng": p["_wgs"][0],
            "lat": p["_wgs"][1],
            "walk_minutes": p["_raw"] if p["_raw"] is not None else math.inf,
        }
        for p in poi_views
    ]
    # 圈内选取：所有类别只保留圈内（达标明细与悬浮窗同口径）；education 三段
    # （幼儿园→小学→中学）、transport 同名站台归并、每类截 20 并打 _withinTotal
    poi_views = select_within_pois(poi_views, minutes)
    coverage, _nearest = compute_coverage(poi_views, minutes, main_area, circle_area)

    # 1km 盲区（写死网格，enhanced 才跟 X 走）
    # blindFilter：只算所选类的 missing（None=复合全类；[]=不算盲区）
    blind_categories = (
        tuple(blind_filter) if blind_filter is not None else DEFAULT_CATEGORIES
    )
    blind_fc = blindspots(
        center_wgs,
        blind_input,
        minutes=minutes,
        enhanced=include_blind_walk,
        categories=blind_categories,
    )
    blind_fc = {
        "type": "FeatureCollection",
        "features": [
            {**f, "geometry": geometry_wgs84_to_bd09ll(f["geometry"])}
            for f in blind_fc.get("features", [])
        ],
    }
    blind_summary = _blind_summary(blind_fc)

    diagnosis = _diagnosis(
        coverage=coverage,
        minutes=minutes,
        isochrone_area=main_area,
        circle_area=circle_area,
        sector_report=sector_report,
        enclaves_count=len(enclaves_out),
        blind_summary=blind_summary,
        degraded=degraded,
        method_only=not whitelisted,
    )
    if corridor is not None:
        diagnosis = [
            *diagnosis,
            "等时圈为百度步行路线走廊近似（8 方向实测），边界沿真实可步行道路。",
        ]

    # 可选 LLM 诊断：未配置/失败返回 None，保持模板结果
    llm_lines = await llm_diagnose(
        {
            "minutes": minutes,
            "coverage": coverage["categories"],
            "score": coverage["score"],
            "areaRatio": main_area / circle_area if circle_area > 0 else 0.0,
            "worstSector": sector_report.worst_direction,
            "sectorDiagnosis": sector_report.diagnosis,
            "blindSummary": blind_summary,
        }
    )
    if llm_lines:
        diagnosis = llm_lines

    # 官方等时圈（需单独授权）：无权限时记一条诊断，以自建引擎为准
    if include_official and ctx.baidu.enabled and not demo_mode:
        try:
            await ctx.baidu.official_isochrone(center_wgs, minutes)
        except NotAvailableError as exc:
            diagnosis = [*diagnosis[:5], str(exc)]

    # 第三拍：百度步行对照折线注入（等时圈算完后；costs 不变、主圈不重算）
    baidu_routes = await _baidu_compare_routes(
        ctx, bundle, center_wgs, poi_views, demo_mode
    )
    if baidu_routes and BAIDU_COMPARE_NOTE not in diagnosis:
        diagnosis = [*diagnosis[:5], BAIDU_COMPARE_NOTE]

    elapsed = round((time.perf_counter() - t0) * 1000)
    meta = _build_meta(
        minutes=minutes,
        engine=engine,
        demo_mode=demo_mode,
        degraded=degraded,
        whitelisted=whitelisted,
        center_wgs=center_wgs,
        elapsed_ms=elapsed,
        isochrone_area=main_area,
        circle_area=circle_area,
        snap_note=snap_note,
        fabric_lite=bundle.lite,
    )
    payload = {
        "meta": meta,
        "fastLayer": fast_layer,
        "layers": layers_out,
        "enclaves": enclaves_out,
        "pois": [
            {k: v for k, v in p.items() if not k.startswith("_")} for p in poi_views
        ],
        "coverage": coverage,
        "sectorGaps": sector_gaps,
        "blindSpots": blind_fc,
        "blindSummary": blind_summary,
        "diagnosis": diagnosis,
        "straightCircle": {"radiusM": circle_radius_m(minutes)},
        "baiduRoutes": baidu_routes,
    }

    if poi_ok:
        ctx.node_results[results_key] = payload
        ctx.disk.set(disk_cache_key(*center_wgs, minutes, cache_engine), payload)
    logger.info(
        "checkup minutes=%d api_calls=%d matrix_routes=0 elapsed_ms=%d degraded=%s",
        minutes,
        ctx.baidu.api_calls - api_calls_before,
        elapsed,
        degraded,
    )
    return payload
