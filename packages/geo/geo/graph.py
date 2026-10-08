"""步行图构建（MapFabric -> WalkGraph）。

规则：
- 删与 blocked 墙厚区或水域相交的边（bridge=yes，或 tunnel=yes 的人行地道
  footway/path/steps，且不与建筑相交者除外）；
- barrier 线（motorway/无步道 trunk）为硬阻隔，边不得跨越
  （bridge=yes 天桥、tunnel=yes 人行地道除外）；
- enterable 建筑轮廓内生成简易廊道边（连接各入口/最近路网点，速度 1.1x 即权更低）；
- podium 建筑生成沿外墙内侧 8m 的廊道边；
- 边权 = 长度/速度：默认 70 m/min（对齐百度算路实测有效速度），台阶 44，
  podium 廊道 60，商场廊道 70x1.1；
- 度 2 收缩：无分叉连续路段并成一条边，形状点保留仅供绘制；
- 边带 source 字段：OSM 底图边为 "osm"，inject_baidu_polyline 注入的
  百度步行对照边为 "baidu"（权 = 长度/速度，与 OSM 边同速口径）。
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field

from shapely import STRtree
from shapely.geometry import LineString, Point
from shapely.geometry.base import BaseGeometry

from .coords import LocalProjection
from .enterability import (
    CATEGORY_ENTERABLE,
    CATEGORY_PODIUM,
    ClassifiedBuilding,
    podium_corridor_ring,
)

# 路面有效速度与百度算路实测对齐（中心→东方新天地 771m/11.0min、
# 中心→金宝汇 1815m/25.8min，均约 70m/min），保证等时圈与 POI 步行分钟同口径。
SPEED_WALK_M_MIN = 70.0
SPEED_STEPS_M_MIN = 44.0  # 台阶，保持原 50/80 = 0.625 的相对梯度
SPEED_CORRIDOR_M_MIN = 60.0  # podium 室内廊道（比路面慢）
SPEED_MALL_FACTOR = 1.1  # 商场内 1.1x 步行速度（权更低）

SNAP_GRID_M = 25.0
SNAP_MAX_DIST_M = 30.0
CORRIDOR_LINK_RADIUS_M = 20.0
PODIUM_LINK_RADIUS_M = 30.0
PODIUM_CORRIDOR_OFFSET_M = 8.0

KIND_ROAD = "road"
KIND_BRIDGE = "bridge"
KIND_TUNNEL = "tunnel"
KIND_CORRIDOR = "corridor"
KIND_PODIUM = "podium"
KIND_LINK = "link"

SOURCE_OSM = "osm"
SOURCE_BAIDU = "baidu"

_TRUTHY = {"yes", "true", "1", "viaduct", "bridge", True}
_TUNNEL_HIGHWAYS = {"footway", "path", "steps"}
_NODE_PRECISION = 3  # 节点合并精度：毫米


@dataclass(slots=True)
class Edge:
    u: int
    v: int
    weight_s: float
    length_m: float
    shape: tuple[tuple[float, float], ...]
    kind: str = KIND_ROAD
    source: str = SOURCE_OSM


@dataclass
class WalkGraph:
    """邻接表步行图（纯 Python dict + 数组，无 networkx 运行时依赖）。"""

    nodes: list[tuple[float, float]]
    edges: list[Edge]
    adj: list[list[tuple[int, int, float]]]  # node -> [(other, weight_s, edge_id)]
    proj: LocalProjection
    _grid: dict[tuple[int, int], list[int]] = field(default_factory=dict)
    # 懒建的节点点 STRtree（面感知接驳 nodes_near_geom 用，随图常驻）
    _node_tree: STRtree | None = field(default=None, repr=False, compare=False)

    @property
    def node_count(self) -> int:
        return len(self.nodes)

    @property
    def edge_count(self) -> int:
        return len(self.edges)

    def edge_linestring(self, edge_id: int) -> LineString:
        return LineString(self.edges[edge_id].shape)

    def snap(self, lng: float, lat: float) -> int | None:
        """25m 网格吸附 + 最近图节点；>30m 返回 None（中心不在可走路上）。"""
        x, y = self.proj.to_xy(lng, lat)
        qx = (math.floor(x / SNAP_GRID_M) + 0.5) * SNAP_GRID_M
        qy = (math.floor(y / SNAP_GRID_M) + 0.5) * SNAP_GRID_M
        c0x = math.floor((qx - SNAP_MAX_DIST_M) / SNAP_GRID_M)
        c1x = math.floor((qx + SNAP_MAX_DIST_M) / SNAP_GRID_M)
        c0y = math.floor((qy - SNAP_MAX_DIST_M) / SNAP_GRID_M)
        c1y = math.floor((qy + SNAP_MAX_DIST_M) / SNAP_GRID_M)
        best_id = -1
        best_d2 = SNAP_MAX_DIST_M * SNAP_MAX_DIST_M
        nodes = self.nodes
        for cx in range(c0x, c1x + 1):
            for cy in range(c0y, c1y + 1):
                for nid in self._grid.get((cx, cy), ()):
                    nx, ny = nodes[nid]
                    d2 = (nx - qx) ** 2 + (ny - qy) ** 2
                    if d2 <= best_d2:
                        best_d2 = d2
                        best_id = nid
        return best_id if best_id >= 0 else None

    def nodes_within(
        self, lng: float, lat: float, radius_m: float
    ) -> list[tuple[int, float]]:
        """半径内全部图节点，返回 ``[(nid, 距离米)]`` 按距离升序。

        供「边缘到达」口径使用：面状设施（公园等）的 POI 点位常在
        设施内部、普通 30m 吸附够不到路网，需在更大半径内取
        「节点耗时 + 末段直线接驳」的最小值近似到达设施边缘。
        """
        x, y = self.proj.to_xy(lng, lat)
        c0x = math.floor((x - radius_m) / SNAP_GRID_M)
        c1x = math.floor((x + radius_m) / SNAP_GRID_M)
        c0y = math.floor((y - radius_m) / SNAP_GRID_M)
        c1y = math.floor((y + radius_m) / SNAP_GRID_M)
        r2 = radius_m * radius_m
        nodes = self.nodes
        out: list[tuple[int, float]] = []
        for cx in range(c0x, c1x + 1):
            for cy in range(c0y, c1y + 1):
                for nid in self._grid.get((cx, cy), ()):
                    nx, ny = nodes[nid]
                    d2 = (nx - x) ** 2 + (ny - y) ** 2
                    if d2 <= r2:
                        out.append((nid, math.sqrt(d2)))
        out.sort(key=lambda t: t[1])
        return out

    def _node_points_tree(self) -> STRtree | None:
        if self._node_tree is None and self.nodes:
            self._node_tree = STRtree([Point(x, y) for x, y in self.nodes])
        return self._node_tree

    def nodes_near_geom(
        self, geom: BaseGeometry, radius_m: float
    ) -> list[tuple[int, float]]:
        """距给定几何 radius_m 内的图节点，返回 ``[(nid, 距离米)]`` 按距离升序。

        面感知「边缘到达」用：POI 标签落在公园/绿地等面状设施内部时，
        候选接驳节点取面 radius_m 邻域内的路网点，末段接驳按「节点到面」
        的距离计——节点已落在面内则距离为 0（触及面即到达设施）。
        """
        tree = self._node_points_tree()
        if tree is None:
            return []
        window = geom.buffer(radius_m)
        nodes = self.nodes
        out: list[tuple[int, float]] = []
        for i in tree.query(window, predicate="intersects"):
            nid = int(i)
            d = geom.distance(Point(*nodes[nid]))
            if d <= radius_m:
                out.append((nid, d))
        out.sort(key=lambda t: t[1])
        return out


def _speed_m_min(props: dict) -> float:
    if str(props.get("highway") or "") == "steps":
        return SPEED_STEPS_M_MIN
    return SPEED_WALK_M_MIN


def _is_bridge(props: dict) -> bool:
    return props.get("bridge") in _TRUTHY


def _is_tunnel(props: dict) -> bool:
    """人行地道例外：tunnel=yes 且为 footway/path/steps（与 bridge 同级，
    可穿水域、可穿 barrier；车行隧道不在步行图内，不适用）。"""
    if props.get("tunnel") not in _TRUTHY:
        return False
    return str(props.get("highway") or "") in _TUNNEL_HIGHWAYS


def _node_key(x: float, y: float) -> tuple[float, float]:
    return (round(x, _NODE_PRECISION), round(y, _NODE_PRECISION))


def _extract_lines(geom: BaseGeometry) -> list[LineString]:
    if geom.is_empty:
        return []
    if geom.geom_type == "LineString":
        return [geom]
    if geom.geom_type in ("MultiLineString", "GeometryCollection"):
        out: list[LineString] = []
        for part in geom.geoms:
            out.extend(_extract_lines(part))
        return out
    return []


def _insert_crossing_vertices(lines: list[LineString]) -> list[LineString]:
    """把线线交点（含 T 型节点）插入为顶点，保证交叉口处成图节点。"""
    tree = STRtree(lines)
    out: list[LineString] = []
    for i, line in enumerate(lines):
        hits = tree.query(line, predicate="intersects")
        cross_pts: list[Point] = []
        for j in hits:
            j = int(j)
            if j == i:
                continue
            inter = line.intersection(lines[j])
            if inter.geom_type == "Point":
                cross_pts.append(inter)
            elif inter.geom_type == "MultiPoint":
                cross_pts.extend(inter.geoms)
        if not cross_pts:
            out.append(line)
            continue
        # 事件合并：原顶点 + 交点，按里程排序后去重（毫米精度）
        events: list[tuple[float, tuple[float, float]]] = []
        acc = 0.0
        coords = list(line.coords)
        events.append((0.0, (coords[0][0], coords[0][1])))
        for k in range(1, len(coords)):
            acc += math.hypot(coords[k][0] - coords[k - 1][0], coords[k][1] - coords[k - 1][1])
            events.append((acc, (coords[k][0], coords[k][1])))
        for pt in cross_pts:
            d = line.project(pt)
            if 1e-6 < d < line.length - 1e-6:
                ip = line.interpolate(d)
                events.append((d, (ip.x, ip.y)))
        events.sort(key=lambda e: e[0])
        merged: list[tuple[float, float]] = []
        seen: set[tuple[float, float]] = set()
        for _d, (x, y) in events:
            key = _node_key(x, y)
            if key in seen:
                continue
            seen.add(key)
            merged.append((x, y))
        out.append(LineString(merged) if len(merged) >= 2 else line)
    return out


def _geom_intersects_any(tree: STRtree | None, geoms: list[BaseGeometry], g: BaseGeometry) -> bool:
    if tree is None or not geoms:
        return False
    for idx in tree.query(g, predicate="intersects"):
        if g.intersects(geoms[int(idx)]):
            return True
    return False


def _crosses_barrier(barrier_tree: STRtree | None, barriers: list[BaseGeometry], seg: LineString) -> bool:
    """边是否跨越阻隔线（端点触碰不算跨越；共线重叠算）。"""
    if barrier_tree is None or not barriers:
        return False
    for idx in barrier_tree.query(seg, predicate="intersects"):
        b = barriers[int(idx)]
        inter = seg.intersection(b)
        if inter.is_empty:
            continue
        if inter.geom_type in ("LineString", "MultiLineString"):
            return True
        if seg.crosses(b):
            return True
    return False


class _NodeRegistry:
    def __init__(self) -> None:
        self.key2id: dict[tuple[float, float], int] = {}
        self.coords: list[tuple[float, float]] = []

    def get(self, x: float, y: float) -> int:
        key = _node_key(x, y)
        nid = self.key2id.get(key)
        if nid is None:
            nid = len(self.coords)
            self.key2id[key] = nid
            self.coords.append((x, y))
        return nid


def _add_edge(
    edges: list[Edge],
    u: int,
    v: int,
    coords: list[tuple[float, float]],
    speed_m_min: float,
    kind: str,
) -> None:
    if u == v or len(coords) < 2:
        return
    length = 0.0
    for k in range(len(coords) - 1):
        length += math.hypot(coords[k + 1][0] - coords[k][0], coords[k + 1][1] - coords[k][1])
    if length <= 0.0:
        return
    weight_s = length / speed_m_min * 60.0
    edges.append(Edge(u=u, v=v, weight_s=weight_s, length_m=length, shape=tuple(coords), kind=kind))


def _nearest_on_outline(outline: LineString, pt: Point) -> Point:
    return outline.interpolate(outline.project(pt))


def _gen_enterable_corridors(
    poly: BaseGeometry,
    registry: _NodeRegistry,
    node_ids_near: list[int],
    edges: list[Edge],
    wall_tree: STRtree | None,
    walls: list[BaseGeometry],
    water_tree: STRtree | None,
    waters: list[BaseGeometry],
) -> None:
    """enterable 建筑：轮廓入口 + 内部廊道（速度 1.1x，权更低）。"""
    outline = LineString(poly.exterior.coords) if poly.geom_type == "Polygon" else None
    if outline is None:
        return
    entries: dict[tuple[float, float], int] = {}
    for nid in node_ids_near:
        nx, ny = registry.coords[nid]
        entry_pt = _nearest_on_outline(outline, Point(nx, ny))
        eid = registry.get(entry_pt.x, entry_pt.y)
        entries[_node_key(entry_pt.x, entry_pt.y)] = eid
        link = LineString([(nx, ny), (entry_pt.x, entry_pt.y)])
        if _geom_intersects_any(wall_tree, walls, link) or _geom_intersects_any(
            water_tree, waters, link
        ):
            continue
        _add_edge(edges, nid, eid, [(nx, ny), (entry_pt.x, entry_pt.y)], SPEED_WALK_M_MIN, KIND_LINK)

    entry_ids = list(entries.values())
    if len(entry_ids) < 2:
        return
    centroid = poly.centroid
    cx, cy = centroid.x, centroid.y
    cid = registry.get(cx, cy)
    speed = SPEED_WALK_M_MIN * SPEED_MALL_FACTOR
    for k in range(len(entry_ids)):
        a = entry_ids[k]
        b = entry_ids[(k + 1) % len(entry_ids)]
        ax, ay = registry.coords[a]
        bx, by = registry.coords[b]
        chord = LineString([(ax, ay), (bx, by)])
        if poly.covers(chord):
            _add_edge(edges, a, b, [(ax, ay), (bx, by)], speed, KIND_CORRIDOR)
        else:
            _add_edge(edges, a, cid, [(ax, ay), (cx, cy)], speed, KIND_CORRIDOR)
            _add_edge(edges, cid, b, [(cx, cy), (bx, by)], speed, KIND_CORRIDOR)


def _gen_podium_corridor(
    poly: BaseGeometry,
    registry: _NodeRegistry,
    node_ids_near: list[int],
    edges: list[Edge],
) -> None:
    """podium 建筑：沿外墙内侧 8m 的廊道环。"""
    ring_poly = podium_corridor_ring(poly)
    if ring_poly is None:
        return
    inner = poly.buffer(-PODIUM_CORRIDOR_OFFSET_M, quad_segs=8)
    if inner.is_empty:
        return
    if inner.geom_type == "MultiPolygon":
        inner = max(inner.geoms, key=lambda g: g.area)
    ring_coords = list(inner.exterior.coords)
    ring_ids = [registry.get(x, y) for x, y in ring_coords]
    for k in range(len(ring_ids) - 1):
        a, b = ring_ids[k], ring_ids[k + 1]
        _add_edge(
            edges,
            a,
            b,
            [registry.coords[a], registry.coords[b]],
            SPEED_CORRIDOR_M_MIN,
            KIND_PODIUM,
        )
    # 路网点接入廊道环（连到最近环顶点）
    for nid in node_ids_near:
        nx, ny = registry.coords[nid]
        best = -1
        best_d2 = PODIUM_LINK_RADIUS_M * PODIUM_LINK_RADIUS_M
        for rid in set(ring_ids):
            rx, ry = registry.coords[rid]
            d2 = (rx - nx) ** 2 + (ry - ny) ** 2
            if d2 <= best_d2:
                best_d2 = d2
                best = rid
        if best >= 0:
            bx, by = registry.coords[best]
            _add_edge(
                edges, nid, best, [(nx, ny), (bx, by)], SPEED_WALK_M_MIN, KIND_LINK
            )


def _contract(
    nodes: list[tuple[float, float]],
    edges: list[Edge],
) -> tuple[list[tuple[float, float]], list[Edge], list[list[tuple[int, int, float]]]]:
    """度 2 收缩：中间无分叉的连续同类路段并成一条边，形状点保留。"""
    deg = [0] * len(nodes)
    adj: list[list[tuple[int, int]]] = [[] for _ in nodes]
    for eid, e in enumerate(edges):
        adj[e.u].append((eid, e.v))
        adj[e.v].append((eid, e.u))
        deg[e.u] += 1
        deg[e.v] += 1

    visited = [False] * len(edges)
    new_edges: list[Edge] = []

    def walk(start: int, eid0: int, nxt0: int) -> None:
        total_w = 0.0
        total_len = 0.0
        shapes: list[tuple[float, float]] = []
        kind = edges[eid0].kind
        cur, eid, nxt = start, eid0, nxt0
        end = nxt0
        while True:
            visited[eid] = True
            e = edges[eid]
            total_w += e.weight_s
            total_len += e.length_m
            seg = list(e.shape) if e.u == cur else list(reversed(e.shape))
            if shapes:
                shapes.extend(seg[1:])
            else:
                shapes.extend(seg)
            end = nxt
            if deg[nxt] != 2:
                break
            eid2, nxt2 = None, None
            for cand_eid, cand_nxt in adj[nxt]:
                if cand_eid != eid and not visited[cand_eid]:
                    eid2, nxt2 = cand_eid, cand_nxt
                    break
            if eid2 is None or edges[eid2].kind != kind:
                break
            cur, eid, nxt = nxt, eid2, nxt2
        new_edges.append(
            Edge(
                u=start,
                v=end,
                weight_s=total_w,
                length_m=total_len,
                shape=tuple(shapes),
                kind=kind,
            )
        )

    for nid in range(len(nodes)):
        if deg[nid] != 2:
            for eid, nxt in adj[nid]:
                if not visited[eid]:
                    walk(nid, eid, nxt)
    # 残余：整条环都是度 2 节点
    for eid, e in enumerate(edges):
        if not visited[eid]:
            walk(e.u, eid, e.v)

    # 重建节点表（丢弃未用节点）
    remap: dict[int, int] = {}
    new_nodes: list[tuple[float, float]] = []
    for e in new_edges:
        for nid in (e.u, e.v):
            if nid not in remap:
                remap[nid] = len(new_nodes)
                new_nodes.append(nodes[nid])
    for e in new_edges:
        e.u = remap[e.u]
        e.v = remap[e.v]
    new_adj: list[list[tuple[int, int, float]]] = [[] for _ in new_nodes]
    for eid, e in enumerate(new_edges):
        new_adj[e.u].append((e.v, e.weight_s, eid))
        if e.v != e.u:
            new_adj[e.v].append((e.u, e.weight_s, eid))
    return new_nodes, new_edges, new_adj


def build_graph(
    roads: list[tuple[BaseGeometry, dict]],
    buildings_classified: list[ClassifiedBuilding],
    water: list[tuple[BaseGeometry, dict]],
    barriers: list[tuple[BaseGeometry, dict]],
    center: tuple[float, float],
    radius_m: float,
) -> WalkGraph:
    """从裁切后的底图层构建步行图（几何均为以 center 为原点的本地米坐标）。"""
    proj = LocalProjection(*center)

    walls = [b.wall for b in buildings_classified if b.wall is not None and not b.wall.is_empty]
    wall_tree = STRtree(walls) if walls else None
    waters = [g for g, _ in water if not g.is_empty]
    water_tree = STRtree(waters) if waters else None
    # 阻隔线只保留真实「行人不可穿越」的屏障（铁路等）。高速/快速路主线
    # （highway 类 barrier）不阻隔：交叉口处以高架/地道形式被穿越，行人
    # 可经立体交叉横穿两侧——现实中两条地面路与快速路不存在平交，几何
    # 相交即为可穿越点（匝道汇入的误放影响远小于整片割裂）。
    barrier_geoms = [g for g, p in barriers if "highway" not in p and not g.is_empty]
    barrier_tree = STRtree(barrier_geoms) if barrier_geoms else None

    # 1) 道路线插交点成网
    raw_lines: list[LineString] = []
    raw_props: list[dict] = []
    for geom, props in roads:
        for line in _extract_lines(geom):
            if len(line.coords) >= 2:
                raw_lines.append(line)
                raw_props.append(props)
    noded = _insert_crossing_vertices(raw_lines) if raw_lines else []

    registry = _NodeRegistry()
    edges: list[Edge] = []

    # 2) 逐段过滤 + 成边
    for line, props in zip(noded, raw_props, strict=True):
        is_bridge = _is_bridge(props)
        is_tunnel = _is_tunnel(props)
        # 桥与人行地道同级的「穿水/穿阻隔」例外（但仍不得穿建筑）
        exempt = is_bridge or is_tunnel
        speed = _speed_m_min(props)
        kind = KIND_BRIDGE if is_bridge else (KIND_TUNNEL if is_tunnel else KIND_ROAD)
        coords = list(line.coords)
        for k in range(len(coords) - 1):
            p0, p1 = coords[k], coords[k + 1]
            seg = LineString([p0, p1])
            hits_wall = _geom_intersects_any(wall_tree, walls, seg)
            if hits_wall:
                continue  # bridge/tunnel 也不得穿建筑
            hits_water = _geom_intersects_any(water_tree, waters, seg)
            if hits_water and not exempt:
                continue
            if _crosses_barrier(barrier_tree, barrier_geoms, seg) and not exempt:
                continue
            u = registry.get(p0[0], p0[1])
            v = registry.get(p1[0], p1[1])
            _add_edge(edges, u, v, [p0, p1], speed, kind)

    # 3) 建筑廊道
    node_points = [Point(x, y) for x, y in registry.coords]
    node_tree = STRtree(node_points) if node_points else None
    for b in buildings_classified:
        if b.geometry.is_empty:
            continue
        if b.category == CATEGORY_ENTERABLE:
            window = b.geometry.buffer(CORRIDOR_LINK_RADIUS_M)
            near = (
                [int(i) for i in node_tree.query(window, predicate="intersects")]
                if node_tree is not None
                else []
            )
            _gen_enterable_corridors(
                b.geometry, registry, near, edges, wall_tree, walls, water_tree, waters
            )
        elif b.category == CATEGORY_PODIUM:
            window = b.geometry.buffer(PODIUM_LINK_RADIUS_M)
            near = (
                [int(i) for i in node_tree.query(window, predicate="intersects")]
                if node_tree is not None
                else []
            )
            _gen_podium_corridor(b.geometry, registry, near, edges)

    # 4) 度 2 收缩 + 重建
    nodes, edges, adj = _contract(registry.coords, edges)

    # 5) 吸附网格索引
    grid: dict[tuple[int, int], list[int]] = {}
    for nid, (x, y) in enumerate(nodes):
        key = (math.floor(x / SNAP_GRID_M), math.floor(y / SNAP_GRID_M))
        grid.setdefault(key, []).append(nid)

    _ = radius_m  # 裁切已在 fabric.clip 完成
    return WalkGraph(nodes=nodes, edges=edges, adj=adj, proj=proj, _grid=grid)


def inject_baidu_polyline(
    graph: WalkGraph,
    path_lnglat: Sequence[tuple[float, float]],
    speed_m_per_min: float = SPEED_WALK_M_MIN,
    weight_factor: float = 1.0,
) -> int:
    """把百度步行折线拆成 ``source="baidu"`` 高权边注入图（对照路径用）。

    - 折线顶点吸附到 30m 内最近图节点（复用 snap 网格），吸附不到则新建节点；
    - 相邻顶点连成一条边，权 = 长度/速度 × ``weight_factor``（默认 1.0，
      忠实于百度实测耗时，与同长 OSM 边同速）；
    - 连续顶点吸附到同一节点时跳过该段；
    - 只增不改：既有边与已算好的 costs 不受影响（主圈不重算）。

    返回实际注入的边数。
    """
    if len(path_lnglat) < 2:
        return 0
    injected = 0
    prev_nid: int | None = None
    prev_xy: tuple[float, float] | None = None
    for lng, lat in path_lnglat:
        x, y = graph.proj.to_xy(lng, lat)
        nid = graph.snap(lng, lat)
        if nid is None:
            nid = len(graph.nodes)
            graph.nodes.append((x, y))
            graph.adj.append([])
            key = (math.floor(x / SNAP_GRID_M), math.floor(y / SNAP_GRID_M))
            graph._grid.setdefault(key, []).append(nid)
        if prev_nid is not None and prev_nid != nid:
            assert prev_xy is not None
            length = math.hypot(x - prev_xy[0], y - prev_xy[1])
            if length > 0.0:
                weight_s = length / speed_m_per_min * 60.0 * weight_factor
                eid = len(graph.edges)
                graph.edges.append(
                    Edge(
                        u=prev_nid,
                        v=nid,
                        weight_s=weight_s,
                        length_m=length,
                        shape=(prev_xy, (x, y)),
                        kind=KIND_LINK,
                        source=SOURCE_BAIDU,
                    )
                )
                graph.adj[prev_nid].append((nid, weight_s, eid))
                graph.adj[nid].append((prev_nid, weight_s, eid))
                injected += 1
        prev_nid, prev_xy = nid, (x, y)
    return injected
