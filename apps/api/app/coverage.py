"""POI 覆盖服务：7 类词表、清洗、步行耗时来源、达标判定与综合打分。

- 关键词表（百度 place 检索，``$`` 为百度「或」语法）与展示名按契约写死；
  ``transport`` 特殊：地铁站/公交站分两次检索（POI 带 ``transportKind``）；
  ``education`` 同为双检索（POI 带 ``eduKind``）：圈内选取按
  幼儿园→小学→中学 三段配额（小学/中学按名称关键词识别）；
- 清洗：uid 去重；无 uid 按「名称 + 30m」去重；不截断，去重后的全量召回数打
  内部键 ``_catTotal``（随 ``_`` 前缀剥离不外发），覆盖结果以 ``total`` 透出；
- 圈内选取（:func:`select_within_pois`，步行分钟定稿后）：所有类别只保留
  圈内（达标明细与悬浮窗同口径），按距离截到 cap 20，截断前**圈内**数打
  ``_withinTotal``，以 ``withinCount`` 透出（超 20 前端显示 20+）；
  education 段内按距离、段间幼儿园→小学→中学（圈外不再冲抵）；transport
  同名站台按规范化基础站名归并（多出口/上下行只计最近一处）；health
  同院区科室按「主机构名 + 相距≤200m」归并（多科室只计最近一处）；
  ``public`` 额外做两级处理：剔除政府机关、社区政务设施（社区服务中心(站)/
  党群服务(中心|站)/街道活动中心）与商业广场/写字楼「XX广场」/商业健身
  （名称规则 + 百度 scope=2 类别 tag 黑名单：房地产/购物/公司企业等一级类），
  公园/绿地/广场类名称优先于图书馆/文化中心等保留（超 cap 时核心公共空间
  不被挤掉）；
  ``transport`` 地铁站优先保留（超 cap 时不被公交站挤掉）；
- 步行耗时：优先图引擎（POI 吸附节点查同一次 cutoff_dijkstra 的 costs），
  吸不上时用「直线距离 / 70m/min」估算并标 ``estimated=true``；
  主圈绝不打 RouteMatrix；第三拍若拿到 walking_matrix 结果，
  用 :func:`calibrate_walk_minutes` 校准（由编排层异步控制）；
- 达标：``nearestWalkMin <= minutes``；``convenience`` 额外要求 count>=3；
  ``transport`` 一票制：地铁站最近<=X 达标，否则公交站最近<=X 达标（标注公交）；
- 综合分调 geo.coverage_score，分母=请求 minutes，禁止写死 15。
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

from shapely import STRtree
from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry

from geo import coverage_score, haversine_m, passed, wgs84_to_bd09ll
from geo.graph import WalkGraph

from .db import normalize_name

CATEGORY_CONVENIENCE = "convenience"
CATEGORY_HEALTH = "health"
CATEGORY_EDUCATION = "education"

CATEGORIES: tuple[str, ...] = (
    "convenience",
    "leisure",
    "mall",
    "education",
    "health",
    "public",
    "transport",
)

# 展示名（契约写死）
CATEGORY_NAMES: dict[str, str] = {
    "convenience": "便利生活",
    "leisure": "娱乐休闲",
    "mall": "商超购物",
    "education": "学习教育",
    "health": "医疗养老",
    "public": "公共空间",
    "transport": "交通出行",
}

# 百度 place 检索关键词（契约写死，$ = 或）。
# 「景区/绿地」等裸词实测返回旅行社/墓/清真寺等噪声：绿地用「公共绿地」限定；
# 「广场」配合 _PUBLIC_EXCLUDE_RE 的商业广场剔除规则使用；
# 公共空间按返回名称做核心度分级（见 _PUBLIC_CORE_RE）。
BAIDU_QUERIES: dict[str, str] = {
    "convenience": "便利店$菜市场$农贸市场$理发店$快餐$蛋糕房$洗衣店",
    "leisure": "电影院$剧院$体育馆$游泳馆$展览馆$健身$洗浴$按摩$温泉$度假村",
    "mall": "购物中心$商场$超市$奥特莱斯$家居",
    "health": "医院$社区卫生服务中心$诊所$药店$养老$敬老院$老年公寓",
    "public": "公园$图书馆$公共绿地$广场",
}

# education 双检索：小学/中学/大学（学校优先保留）与幼儿园分体配额——
# 合并单查询时百度按距离排序，数量占优的幼儿园会挤掉 20 条配额里的中/小学
EDU_QUERIES: dict[str, str] = {
    "school": "小学$中学$大学",
    "kindergarten": "幼儿园",
}

# transport 双检索：地铁站（一票制）/ 公交站（兜底，达标注记「公交」）
TRANSPORT_QUERIES: dict[str, str] = {
    "metro": "地铁站",
    "bus": "公交站",
}

# 锚点三类：综合分中的「基础民生」口径（原硬 3 语义延续）
ANCHOR3: tuple[str, str, str] = (CATEGORY_CONVENIENCE, CATEGORY_HEALTH, CATEGORY_EDUCATION)

CAP_PER_CATEGORY = 20

# 公共空间「核心度」：可进入的公园/绿地/景区与公共广场类名称优先保留（超
# cap 时排在图书馆、文化中心之前）；「已关闭」仍排尾。
_PUBLIC_CORE_RE = re.compile(r"公园|花园|绿地|景区|湿地|滨河|河滨|健身园|广场")
# 非公共空间：政府机关及社区政务设施（办事处/管委会/社区服务中心(站)/党群
# 服务(中心|站)/街道活动中心）、商业广场（购物/百货/商城等冠名）与「XX广场」
# 命名的写字楼分栋（办公楼/配楼/分座，如「东方广场-东办公楼E3座」「中粮广场-A座」）、
# 商业卖场与停车场（「广场」检索词实测召回眼镜城/古玩城/停车场等噪声）与商业
# 健身场所（「健身」名称但不含公共性质的「健身园」；以及俱乐部/私教/搏击/瑜伽）
# 一律不计入——商业健身归属娱乐休闲（leisure）检索词
_PUBLIC_EXCLUDE_RE = re.compile(
    r"办事处|管委会|政务|行政审批"
    r"|社区服务(中心|站)|党群服务(中心|站)|街道活动中心"
    r"|(购物|百货|商城|SOHO|写字楼|贸易|国际)广场"
    r"|经贸|办公楼|写字楼|配楼|附楼|裙楼"
    r"|[A-Z]\d{0,2}座|[东南西北中]座"
    r"|购物中心|超市|百货|商场"
    r"|停车场|眼镜城|古玩|旧货|批发市场|建材城"
    r"|健身(?!园)|俱乐部|私教|搏击|瑜伽"
)
# 百度标准类别黑名单（place scope=2 ``detail_info.tag``，一级类;二级类）：
# 一级类为商业/私有属性的一律剔除——「XX广场」命名的写字楼与商业综合体
# （房地产;写字楼/商业综合体，如「东方广场」「中粮广场」「哈德门广场」）、
# 购物中心（购物;购物中心）、公司企业、金融、停车场（交通设施;停车场）等。
# tag 缺失（scope 降级/合成 POI）时仅靠上面的名称规则，不影响既有行为。
_PUBLIC_EXCLUDE_TAG_RE = re.compile(
    r"^(房地产|购物|公司企业|金融|交通设施|汽车服务|美食|餐饮|酒店|生活服务)"
)

# transport 噪声剔除（检索词「地铁站/公交站」实测召回）：
# - 地铁出入口（「北工大西门地铁站-A西北口」「潘家园地铁站-C1东南口」，以及
#   无字母编号的「九龙山地铁站-东北口」等变体）：出入口与车站本体是同一
#   设施，逐口计数会虚增数量——车站通名「地铁站」后带任意后缀的一律剔除
#   （本体名以「地铁站」结尾、不带后缀，不受影响）；
# - 「冠名地铁站」的关联设施（如家酒店(地铁站店)、停车场-出入口）：
#   不是出行设施。
# 保留车站本体名（「劲松」「北工大西门」「平乐园」等）。
_TRANSPORT_EXCLUDE_RE = re.compile(
    r"出入口|出站口|停车场"
    r"|[A-Z]\d{0,2}[东南西北]口"  # A西北口 / C1东南口 / B东北口 / D2西南口
    r"|地铁站."  # 通名后带后缀 → 出入口/关联设施（含「地铁站-东北口」「地铁站店」）
    r"|酒店|饭店|宾馆"
)

# 直线估算步速（米/分钟），与 geo 引擎 SPEED_WALK_M_MIN 同口径（百度实测 ≈70）
ESTIMATE_SPEED_M_MIN = 70.0

# 边缘到达接驳半径（米）：面状设施（公园等）的 POI 点位常在设施内部，
# 普通 30m 吸附够不到路网。在此半径内取「节点耗时 + 末段直线接驳」的
# 最小值，近似「到达设施边缘即算到达」——与等时圈触及设施外围的视觉
# 语义对齐。250m 覆盖青甸湖类案例（POI 距最近路网 ≈160m）。
EDGE_ACCESS_RADIUS_M = 250.0

# 面感知边缘到达（green 层：公园/绿地等）：POI 标签落在面内或贴边
# ≤AREA_ATTACH_RADIUS_M 时，以「到达面边界」为到达标准——等时圈触及
# 设施外围即计入，不要求走到设施内部的标签点（青甸湖类大面积公园）。
AREA_ATTACH_RADIUS_M = 60.0
# 面模式下候选接驳节点：面边界 AREA_LEG_RADIUS_M 邻域内的路网点
AREA_LEG_RADIUS_M = 250.0
# 相邻 green 面间小缝隙的合并容差（面外扩缓冲桥接，步行误差约 13 秒）
GAP_MERGE_M = 15.0


def clean_pois(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """uid 去重；无 uid 名称+30m 去重；**不截断**。

    截断移到步行分钟定稿后的 :func:`select_within_pois`（cap 只作用圈内，
    圈外设施不再挤占配额）；本函数只做噪声剔除与去重，去重后的全量召回
    数打内部键 ``_catTotal``（快照等无打标路径回退为 count）。
    """
    by_cat: dict[str, list[dict[str, Any]]] = {}
    for poi in raw:
        by_cat.setdefault(str(poi.get("category") or ""), []).append(poi)

    out: list[dict[str, Any]] = []
    for cat in CATEGORIES:
        items = by_cat.get(cat, [])
        if cat == "public":
            items = [
                p
                for p in items
                if not _PUBLIC_EXCLUDE_RE.search(str(p.get("name") or ""))
                and not _PUBLIC_EXCLUDE_TAG_RE.search(str(p.get("baiduTag") or ""))
            ]
        elif cat == "transport":
            items = [p for p in items if not _TRANSPORT_EXCLUDE_RE.search(str(p.get("name") or ""))]
        kept: list[dict[str, Any]] = []
        seen_uid: set[str] = set()
        for poi in items:
            uid = poi.get("uid")
            if uid:
                if uid in seen_uid:
                    continue
                seen_uid.add(str(uid))
            else:
                dup = any(
                    p["name"] == poi["name"]
                    and haversine_m(p["lng"], p["lat"], poi["lng"], poi["lat"]) < 30.0
                    for p in kept
                )
                if dup:
                    continue
            kept.append(poi)
        # 去重后全量保留（不截断）；真实召回数打内部键，序列化时随 _ 前缀剥离
        out.extend({**p, "_catTotal": len(kept)} for p in kept)
    return out


def category_passed(cat: str, nearest_min: float | None, count: int, minutes: int) -> bool:
    """分类达标判定：convenience 要求圈内数量>=3 且最近<=X；其余一律最近<=X。

    ``count`` 为**圈内**设施数（compute_coverage 传 withinCount，与「数量」列
    同口径）；``transport`` 的一票制（地铁优先/公交兜底）由
    :func:`compute_coverage` 特判，不走本函数的「最近<=X」口径。
    """
    if cat == "convenience":
        return count >= 3 and passed(nearest_min, minutes)
    return passed(nearest_min, minutes)


def _green_area_for(
    graph: WalkGraph, poi: dict[str, Any], green: list[BaseGeometry], green_tree: STRtree
) -> BaseGeometry | None:
    """POI 落在/贴边（≤AREA_ATTACH_RADIUS_M）的 green 面外扩 GAP_MERGE_M 后的几何。

    多面命中取面积最大者（嵌套标签场景，大面才是设施本体）；外扩缓冲
    顺带桥接相邻面间的小缝隙（如被小路割开的同一公园多块图斑）。
    """
    x, y = graph.proj.to_xy(poi["lng"], poi["lat"])
    probe = Point(x, y).buffer(AREA_ATTACH_RADIUS_M)
    best_idx = -1
    best_area = -1.0
    for i in green_tree.query(probe, predicate="intersects"):
        g = green[int(i)]
        if g.area > best_area:
            best_idx, best_area = int(i), g.area
    if best_idx < 0:
        return None
    return green[best_idx].buffer(GAP_MERGE_M)


def attach_walk_minutes(
    graph: WalkGraph,
    costs: list[float],
    pois: list[dict[str, Any]],
    center_wgs: tuple[float, float],
    minutes: int,
    green: list[BaseGeometry] | tuple[BaseGeometry, ...] = (),
) -> list[dict[str, Any]]:
    """POI 步行分钟：以**边缘到达**为标准。

    吸附半径内（:data:`EDGE_ACCESS_RADIUS_M`）取「节点 Dijkstra 耗时 +
    末段直线接驳」的最小值——面状设施（公园等）的 POI 点位在设施内部时，
    到达设施边缘即计入，与等时圈触及设施外围的视觉语义对齐。半径内无可
    达节点退化为直线估算（标 ``estimated=true``）。末段接驳超过普通吸附
    距离（30m，即靠接驳才够到路网）标 ``edgeArrival=true``：百度点到点
    实测走到的是设施内部点位、口径偏大，校准阶段据此跳过。

    ``green`` 非空时做**面感知**：POI 标签落在公园/绿地等面内（或贴边）时，
    另按「面边界 250m 邻域内节点耗时 + 节点到面的接驳」计一遍——大面积
    公园的标签点距边界远超 250m（如青甸湖），标签口径会漏算，面口径以
    等时圈触及面外围为到达。两口径取更小者；面口径命中时必标
    ``edgeArrival=true``（时间到边即止，同样跳过百度校准）。返回元素带
    内部键 ``_raw``（图上分钟，不可达为 None）与 ``_wgs``，序列化前剔除。
    """
    green_list = [g for g in green if not g.is_empty]
    green_tree = STRtree(green_list) if green_list else None
    out: list[dict[str, Any]] = []
    for poi in pois:
        raw: float | None = None
        last_leg_m = 0.0
        for nid, dist_m in graph.nodes_within(poi["lng"], poi["lat"], EDGE_ACCESS_RADIUS_M):
            cost = costs[nid]
            if math.isinf(cost):
                continue
            total = cost / 60.0 + dist_m / ESTIMATE_SPEED_M_MIN
            if raw is None or total < raw:
                raw = total
                last_leg_m = dist_m
        area_mode = False
        if green_tree is not None:
            area = _green_area_for(graph, poi, green_list, green_tree)
            if area is not None:
                for nid, dist_m in graph.nodes_near_geom(area, AREA_LEG_RADIUS_M):
                    cost = costs[nid]
                    if math.isinf(cost):
                        continue
                    total = cost / 60.0 + dist_m / ESTIMATE_SPEED_M_MIN
                    if raw is None or total < raw:
                        raw = total
                        last_leg_m = dist_m
                        area_mode = True
        view: dict[str, Any] = {
            "id": str(poi["id"]),
            "name": str(poi["name"]),
            "category": str(poi["category"]),
        }
        if poi.get("transportKind"):
            view["transportKind"] = str(poi["transportKind"])
        if poi.get("eduKind"):
            view["eduKind"] = str(poi["eduKind"])
        if raw is None:
            view["walkMinutes"] = round(
                haversine_m(poi["lng"], poi["lat"], *center_wgs) / ESTIMATE_SPEED_M_MIN, 1
            )
            view["estimated"] = True
        else:
            view["walkMinutes"] = round(raw, 1)
            if area_mode or last_leg_m > 30.0:
                view["edgeArrival"] = True
        view["within"] = raw is not None and raw <= minutes
        bd = wgs84_to_bd09ll(poi["lng"], poi["lat"])
        view["lng"], view["lat"] = bd[0], bd[1]
        view["_raw"] = raw
        view["_wgs"] = (poi["lng"], poi["lat"])
        if poi.get("_catTotal") is not None:
            view["_catTotal"] = poi["_catTotal"]
        out.append(view)
    return out


def calibrate_walk_minutes(
    poi_views: list[dict[str, Any]],
    matrix_minutes: dict[str, float],
    minutes: int,
) -> list[dict[str, Any]]:
    """第三拍校准：用 RouteMatrix 实测步行分钟替换估算/图上值。

    ``matrix_minutes``：poi id -> 步行分钟。未命中条目保持原值；
    ``edgeArrival``（靠末段接驳才够到路网的设施内部点位，如公园）跳过——
    百度实测走到的是设施内部点位，与边缘到达口径冲突，实测天然偏大。
    """
    out: list[dict[str, Any]] = []
    for view in poi_views:
        measured = matrix_minutes.get(str(view["id"]))
        if measured is None or view.get("edgeArrival"):
            out.append(view)
            continue
        updated = dict(view)
        updated["walkMinutes"] = round(float(measured), 1)
        updated["within"] = float(measured) <= minutes
        updated.pop("estimated", None)
        updated["_raw"] = float(measured)
        out.append(updated)
    return out


# ---- 圈内选取（步行分钟定稿后，attach + calibrate 之后调用） ----

# 站名通名尾缀：「九龙山地铁站」「珠江帝景(公交站)」→ 基础站名「九龙山」「珠江帝景」
_TRANSPORT_NAME_SUFFIX_RE = re.compile(r"[(（]?(?:地铁站|公交站)[)（]?$")


def _transport_base_name(name: str) -> str:
    """规范化基础站名：剥离「地铁站/公交站」通名尾缀，用于同名站台归并。"""
    return _TRANSPORT_NAME_SUFFIX_RE.sub("", str(name or "").strip())


# health 同院区科室尾缀：「孙河社区卫生服务中心-驾驶人体检室」「XX医院-发热门诊」
# → 主机构名「孙河社区卫生服务中心」「XX医院」（半角连字符；全角/长破折号兼容）
_HEALTH_BRANCH_RE = re.compile(r"[-－—–].*$")
# 同主机构且相距不超过该值视为同一体检/门诊分支（不同院区通常相距数百米以上）
HEALTH_MERGE_RADIUS_M = 200.0


def _health_base_name(name: str) -> str:
    """规范化主机构名：剥离「-科室/分支」后缀，用于同院区多科室归并。"""
    return _HEALTH_BRANCH_RE.sub("", str(name or "").strip()).strip()


def _edu_stage(p: dict[str, Any]) -> int:
    """education 三段序：幼儿园=0、小学=1、中学（含大学/其余学校）=2。

    eduKind 只有 school/kindergarten 两值，小学/中学按名称关键词识别。
    """
    if str(p.get("eduKind") or "") == "kindergarten":
        return 0
    if "小学" in str(p.get("name") or ""):
        return 1
    return 2


def _within_sort_key(p: dict[str, Any]) -> tuple[float, bool]:
    """圈内排序基础键：步行分钟升序，「已关闭」排尾（圈内的已关闭项最后丢）。"""
    return (float(p["_raw"]), "已关闭" in str(p.get("name") or ""))


def select_within_pois(
    poi_views: list[dict[str, Any]],
    minutes: int,
    cap: int = CAP_PER_CATEGORY,
) -> list[dict[str, Any]]:
    """圈内选取：所有类别只保留圈内（达标明细与悬浮窗同口径），按距离截到 cap。

    在 :func:`attach_walk_minutes` + :func:`calibrate_walk_minutes` 之后、
    :func:`compute_coverage` 之前调用；圈外设施不再进入响应。截断前**圈内**
    数打 ``_withinTotal``，由 compute_coverage 以 ``withinCount`` 透出
    （超 cap 前端显示 20+），截断后剩余条目按步行距离升序。

    - ``education`` 三段选取：段间幼儿园→小学→中学、段内按步行距离，
      圈外学校不冲抵配额（用户口径：先圈内幼儿园，再圈内小学，后圈内中学）；
    - ``transport`` 同名站台归并：规范化基础站名 + transportKind 相同视为
      同一站（多出口/上下行只计最近一处，如「珠江帝景」双向站台），归并后
      地铁站优先保留（超 cap 不被公交站挤掉）；
    - ``health`` 同院区科室归并：剥「-科室」后缀的主机构名相同且相距
      ≤200m 视为同一设施（多科室/体检点只计最近一处，如孙河社区卫生服务
      中心的驾驶人体检室/狂犬疫苗/发热门诊），不同院区相距远不误伤；
    - ``public`` 公园/绿地/广场核心类优先保留（超 cap 不被图书馆挤掉）。
    """
    out: list[dict[str, Any]] = []
    for cat in CATEGORIES:
        items = [p for p in poi_views if p["category"] == cat and p.get("within")]
        if cat == "transport":
            # 同名站台归并：items 先按距离升序，同基础站名+同 kind 只留最近一处
            merged: list[dict[str, Any]] = []
            seen: set[tuple[str, str]] = set()
            for p in sorted(items, key=_within_sort_key):
                base = _transport_base_name(str(p.get("name") or ""))
                key = (base, str(p.get("transportKind") or "bus"))
                if key in seen:
                    continue
                seen.add(key)
                merged.append(p)
            items = merged
        elif cat == "health":
            # 同院区科室归并：主机构名相同且与任一已保留点相距 ≤200m → 只留最近
            merged = []
            anchors: list[tuple[str, float, float]] = []
            for p in sorted(items, key=_within_sort_key):
                base = _health_base_name(str(p.get("name") or ""))
                lng, lat = p["_wgs"]
                if any(
                    b == base and haversine_m(x, y, lng, lat) <= HEALTH_MERGE_RADIUS_M
                    for b, x, y in anchors
                ):
                    continue
                anchors.append((base, lng, lat))
                merged.append(p)
            items = merged
        within_total = len(items)
        if cat == "education":
            items.sort(
                key=lambda p: (_edu_stage(p), "已关闭" in str(p.get("name") or ""), float(p["_raw"]))
            )
        elif cat == "transport":
            items.sort(key=lambda p: (p.get("transportKind") != "metro", *_within_sort_key(p)))
        elif cat == "public":
            items.sort(
                key=lambda p: (
                    _PUBLIC_CORE_RE.search(str(p.get("name") or "")) is None,
                    *_within_sort_key(p),
                )
            )
        else:
            items.sort(key=_within_sort_key)
        out.extend({**p, "_withinTotal": within_total} for p in items[:cap])
    return out


# ---- 重点设施打标（重点学校/三甲医院/地铁站） ----
# data/hot_facilities.json 由 scripts/import_hot_facilities.py 维护：
# [{"type": "key_middle_school"|"key_primary"|"sanjia_hospital", "name": ..., "aliases": [...]}]
HOT_TYPE_TO_TAG: dict[str, str] = {
    "key_middle_school": "key_school",
    "key_primary": "key_school",
    "sanjia_hospital": "sanjia",
}
# 打标适用的 POI 类别：education 匹配学校、health 匹配三甲、transport 仅地铁站
HOT_TAG_CATEGORIES: dict[str, tuple[str, ...]] = {
    "key_school": ("education",),
    "sanjia": ("health",),
    "metro": ("transport",),
}
_HOT_INDEX_CACHE: dict[str, list[str]] | None | bool = False  # False=未加载，None=名单缺失


def load_hot_index(path: str | Path) -> dict[str, list[str]] | None:
    """加载重点设施名单 → {tag: [规范化名称]}；文件缺失返回 None（不打标）。"""
    p = Path(path)
    if not p.is_file():
        return None
    entries = json.loads(p.read_text(encoding="utf-8"))
    index: dict[str, list[str]] = {}
    for e in entries:
        tag = HOT_TYPE_TO_TAG.get(str(e.get("type") or ""))
        if tag is None:
            continue
        names = [str(e.get("name") or ""), *(str(a) for a in (e.get("aliases") or []))]
        for n in names:
            norm = normalize_name(n)
            if len(norm) >= 3 and norm not in index.setdefault(tag, []):
                index[tag].append(norm)
    return index


def get_hot_index(settings_path: str | Path | None = None) -> dict[str, list[str]] | None:
    global _HOT_INDEX_CACHE
    if _HOT_INDEX_CACHE is False:
        p = settings_path or Path(__file__).resolve().parents[3] / "data" / "hot_facilities.json"
        try:
            _HOT_INDEX_CACHE = load_hot_index(p)
        except (OSError, ValueError):
            logger.warning("hot facilities list unreadable: %s", p)
            _HOT_INDEX_CACHE = None
    return _HOT_INDEX_CACHE  # type: ignore[return-value]


def set_hot_index_for_tests(index: dict[str, list[str]] | None) -> None:
    global _HOT_INDEX_CACHE
    _HOT_INDEX_CACHE = index


def apply_hot_tags(poi_views: list[dict[str, Any]]) -> None:
    """对 POI 视图就地打 tags：重点学校（education）/三甲（health）/地铁站（transport）。

    名称匹配：规范化后互相包含（短名 >=3 字，如「五中」in「北京市第五中学」）。
    """
    index = get_hot_index()
    if not index:
        return
    for view in poi_views:
        cat = str(view.get("category") or "")
        if cat == "transport":
            if str(view.get("transportKind") or "") == "metro":
                view.setdefault("tags", []).append("metro")
            continue
        norm = normalize_name(str(view.get("name") or ""))
        if not norm:
            continue
        tags: list[str] = []
        for tag, names in index.items():
            if cat not in HOT_TAG_CATEGORIES.get(tag, ()):
                continue
            for hn in names:
                if (hn in norm or norm in hn):
                    tags.append(tag)
                    break
        if tags:
            existing = view.setdefault("tags", [])
            for tg in tags:
                if tg not in existing:
                    existing.append(tg)


def _transport_nearest(poi_views: list[dict[str, Any]]) -> dict[str, float | None]:
    """transport 的地铁/公交最近步行分钟（图上口径 _raw）。"""
    out: dict[str, float | None] = {"metro": None, "bus": None}
    for p in poi_views:
        if p["category"] != "transport" or p["_raw"] is None:
            continue
        kind = str(p.get("transportKind") or "bus")
        cur = out.get(kind)
        if cur is None or p["_raw"] < cur:
            out[kind] = p["_raw"]
    return out


def compute_coverage(
    poi_views: list[dict[str, Any]],
    minutes: int,
    isochrone_area: float,
    circle_area: float,
) -> tuple[dict, dict[str, float | None]]:
    """7 类覆盖 + 锚点 3 达标数 + 综合分（0-100，分母=请求 minutes）。

    宜在 :func:`select_within_pois` 之后调用：poi_views 只剩圈内条目时，
    ``nearestMinutes`` 语义为「圈内最近」（圈内无设施 → None → 不达标）。
    transport 特判：nearestMinutes 取「地铁优先，无地铁用公交」的决定性最近值，
    类目额外输出 ``metroNearestMinutes``/``busNearestMinutes``/``transportMode``。
    每类额外输出 ``withinCount``（截断前圈内数，取 ``_withinTotal``，达标判定
    与「数量」列同口径；快照等无打标路径回退为按 ``within`` 计数）
    与 ``hot``（圈内重点设施：key_school/sanjia/metro 名称清单，按步行分钟
    升序去重，cap 5，空类剔除）。
    """
    apply_hot_tags(poi_views)

    nearest: dict[str, float | None] = {}
    for cat in CATEGORIES:
        raws = [p["_raw"] for p in poi_views if p["category"] == cat and p["_raw"] is not None]
        nearest[cat] = min(raws) if raws else None

    t = _transport_nearest(poi_views)
    metro_n, bus_n = t["metro"], t["bus"]
    metro_pass = passed(metro_n, minutes)
    bus_pass = passed(bus_n, minutes)
    transport_mode = "metro" if metro_pass else ("bus" if bus_pass else None)
    transport_nearest = metro_n if metro_n is not None else bus_n
    nearest["transport"] = transport_nearest

    cats_out = []
    hot_tag_names = {"key_school": "keySchool", "sanjia": "sanjia", "metro": "metro"}
    for cat in CATEGORIES:
        n = nearest[cat]
        count = sum(1 for p in poi_views if p["category"] == cat)
        # 圈内设施数：select_within_pois 在截断前打的 _withinTotal（圈内真实数，
        # 可能 >cap）；快照等无打标路径回退为按 within 计数
        within_total = next(
            (
                int(p["_withinTotal"])
                for p in poi_views
                if p["category"] == cat and p.get("_withinTotal") is not None
            ),
            None,
        )
        within_count = (
            within_total
            if within_total is not None
            else sum(1 for p in poi_views if p["category"] == cat and p.get("within"))
        )
        # 截断前真实召回数（clean_pois 打标；快照等无打标路径回退为 count）
        total = next(
            (
                int(p["_catTotal"])
                for p in poi_views
                if p["category"] == cat and p.get("_catTotal") is not None
            ),
            count,
        )
        # 圈内重点设施聚合：按步行分钟升序，名称去重，cap 5
        hot: dict[str, list[str]] = {}
        for tag, out_key in hot_tag_names.items():
            names: list[str] = []
            cand = [
                p
                for p in poi_views
                if p["category"] == cat and p.get("within") and tag in (p.get("tags") or [])
            ]
            for p in sorted(cand, key=lambda q: (q["_raw"] is None, q["_raw"] if q["_raw"] is not None else 0)):
                name = str(p["name"])
                if name not in names:
                    names.append(name)
                if len(names) >= 5:
                    break
            if names:
                hot[out_key] = names
        entry: dict[str, Any] = {
            "id": cat,
            "name": CATEGORY_NAMES[cat],
            "count": count,
            "withinCount": within_count,
            "total": total,
            "nearestMinutes": round(n, 1) if n is not None else None,
            "passed": category_passed(cat, n, within_count, minutes),
        }
        if hot:
            entry["hot"] = hot
        if cat == "transport":
            entry["passed"] = metro_pass or bus_pass
            entry["metroNearestMinutes"] = round(metro_n, 1) if metro_n is not None else None
            entry["busNearestMinutes"] = round(bus_n, 1) if bus_n is not None else None
            entry["transportMode"] = transport_mode
        cats_out.append(entry)

    anchor_nearest = [nearest[c] for c in ANCHOR3]
    transport_component = 1.0 if metro_pass else (0.6 if bus_pass else 0.0)
    score = coverage_score(
        nearest, anchor_nearest, isochrone_area, circle_area, minutes,
        transport_component=transport_component,
    )
    anchor3_passed = sum(1 for v in anchor_nearest if passed(v, minutes))
    return (
        {"score": round(score * 100.0, 1), "categories": cats_out, "anchor3Passed": anchor3_passed},
        nearest,
    )
