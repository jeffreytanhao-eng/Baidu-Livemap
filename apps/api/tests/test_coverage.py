"""覆盖服务：分类达标规则（convenience/transport）、estimated 标记、matrix 校准、
圈内选取（select_within_pois：只留圈内、education 三段配额、transport 同名站归并）、分母随 X。"""

from __future__ import annotations

from app.coverage import (
    attach_walk_minutes,
    calibrate_walk_minutes,
    category_passed,
    clean_pois,
    compute_coverage,
    select_within_pois,
    set_hot_index_for_tests,
)


def _view(cat, raw, walk=None):
    return {
        "id": f"{cat}-1",
        "name": cat,
        "category": cat,
        "lng": 116.46,
        "lat": 39.88,
        "walkMinutes": walk if walk is not None else (raw if raw is not None else 99.0),
        "within": raw is not None,
        "_raw": raw,
        "_wgs": (116.46, 39.88),
    }


def test_category_passed_rules():
    # 常规类：最近 <= X
    assert category_passed("leisure", 8.0, 1, 10) is True
    assert category_passed("leisure", 12.0, 1, 10) is False
    assert category_passed("leisure", None, 3, 10) is False
    # convenience：数量 >=3 且最近 <= X（继承原 daily 规则）
    assert category_passed("convenience", 5.0, 3, 10) is True
    assert category_passed("convenience", 5.0, 2, 10) is False
    assert category_passed("convenience", 12.0, 5, 10) is False
    # health（含养老）：与常规类一致，最近 <= X
    assert category_passed("health", 5.0, 1, 10) is True
    assert category_passed("health", 25.0, 1, 10) is False
    assert category_passed("health", None, 3, 10) is False


def test_coverage_score_and_pass_follow_minutes():
    # 同组 POI：minutes=10 与 20 达标数与分数不同
    # convenience 需 count>=3 且最近 <= X（3 个：11/12/13，10 分钟时最近 11>10 不达标）
    views = [
        _view("convenience", 12.0),
        _view("convenience", 11.0),
        _view("convenience", 13.0),
        _view("health", 8.0),
        _view("education", 18.0),
        _view("leisure", 6.0),
    ]
    cov10, _ = compute_coverage(views, 10, 1.0e6, 2.0e6)
    cov20, _ = compute_coverage(views, 20, 1.0e6, 2.0e6)
    by10 = {c["id"]: c["passed"] for c in cov10["categories"]}
    by20 = {c["id"]: c["passed"] for c in cov20["categories"]}
    assert by10["convenience"] is False and by20["convenience"] is True
    assert by10["education"] is False and by20["education"] is True
    assert by10["health"] is True and by20["health"] is True
    assert cov10["anchor3Passed"] == 1 and cov20["anchor3Passed"] == 3
    assert cov20["score"] > cov10["score"]


def test_attach_walk_minutes_estimated(ctx):
    bundle = ctx.regions["jinsong"]
    graph = bundle.graph
    costs = [0.0] * graph.node_count
    proj = graph.proj
    on_road = proj.to_lnglat(*graph.nodes[0])
    off_net = proj.to_lnglat(5000.0, -5000.0)  # 路网外，吸不上
    pois = [
        {"id": "a", "name": "路上", "category": "convenience", "lng": on_road[0], "lat": on_road[1]},
        {"id": "b", "name": "网外", "category": "health", "lng": off_net[0], "lat": off_net[1]},
    ]
    views = attach_walk_minutes(graph, costs, pois, on_road, 15)
    road_view = next(v for v in views if v["id"] == "a")
    river_view = next(v for v in views if v["id"] == "b")
    assert "estimated" not in road_view
    assert "edgeArrival" not in road_view  # 路上点位直达路网，无末段接驳
    assert river_view["estimated"] is True
    assert river_view["within"] is False
    # 直线估算：/80m每分钟
    assert river_view["walkMinutes"] > 0


def test_attach_walk_minutes_edge_arrival(ctx):
    """边缘到达：设施内部点位（离路网 >30m、<250m）取「节点耗时+末段接驳」最小值
    并打 edgeArrival 标记——到达设施边缘即算到达，与等时圈视觉语义对齐。"""
    bundle = ctx.regions["jinsong"]
    graph = bundle.graph
    costs = [0.0] * graph.node_count
    proj = graph.proj
    base = graph.nodes[0]
    # 从节点向外偏移找离路网 >30m 的点（模拟公园等设施内部点位）
    point_xy = None
    min_d = None
    for dx, dy in (
        (60.0, 0.0),
        (0.0, 60.0),
        (60.0, 60.0),
        (-60.0, 60.0),
        (90.0, 90.0),
        (120.0, 0.0),
        (150.0, 120.0),
    ):
        cand = (base[0] + dx, base[1] + dy)
        near = graph.nodes_within(*proj.to_lnglat(*cand), 250.0)
        if near and near[0][1] > 30.0:
            point_xy, min_d = cand, near[0][1]
            break
    assert point_xy is not None, "路网 250m 内应存在 >30m 的空隙点"
    lng, lat = proj.to_lnglat(*point_xy)
    center_wgs = proj.to_lnglat(*base)
    pois = [{"id": "park", "name": "青甸湖公园", "category": "leisure", "lng": lng, "lat": lat}]
    views = attach_walk_minutes(graph, costs, pois, center_wgs, 15)
    v = views[0]
    assert v.get("edgeArrival") is True
    # costs 全 0 → 步行分钟 = 最近节点距离 / 70
    assert v["walkMinutes"] == round(min_d / 70.0, 1)
    assert v["within"] is True
    assert "estimated" not in v


def test_attach_walk_minutes_green_area_arrival():
    """面感知边缘到达：POI 标签落在大面积 green 面内且距路网 >250m
    （青甸湖类场景）——标签口径吸不上路网退化为直线估算（漏算），
    面口径按「面边界邻域节点 + 节点到面接驳」计入并标 edgeArrival。"""
    import math

    from geo.coords import LocalProjection
    from geo.graph import WalkGraph
    from shapely.geometry import Polygon

    from app.coverage import GAP_MERGE_M

    proj = LocalProjection(116.46, 39.88)
    # 单节点图：A(0,0)；green 面 (100,100)-(600,600)，面边界距 A ≈141m
    graph = WalkGraph(
        nodes=[(0.0, 0.0)], edges=[], adj=[[]], proj=proj, _grid={(0, 0): [0]}
    )
    costs = [0.0]
    green_poly = Polygon([(100, 100), (600, 100), (600, 600), (100, 600)])
    # POI 在面中心，距 A ≈495m > 250m：标签口径吸不上
    lng, lat = proj.to_lnglat(350.0, 350.0)
    pois = [{"id": "park", "name": "青甸湖公园", "category": "public", "lng": lng, "lat": lat}]
    center_wgs = proj.to_lnglat(0.0, 0.0)

    # 无 green：标签口径失败 → 直线估算（修复前青甸湖漏算的形态）
    plain = attach_walk_minutes(graph, costs, pois, center_wgs, 15)
    assert plain[0]["estimated"] is True
    assert plain[0]["within"] is False
    assert plain[0]["walkMinutes"] > 5.0  # 直线 495m/70 ≈ 7.1 分钟

    # 有 green：面口径生效——A 距外扩面（GAP_MERGE_M=15，buffer 圆弧角点
    # 沿 45° 方向恰好外扩 15m）≈ 126m，/70 ≈ 1.8 分钟
    out = attach_walk_minutes(graph, costs, pois, center_wgs, 15, [green_poly])
    v = out[0]
    assert "estimated" not in v
    assert v["edgeArrival"] is True  # 面口径命中必标，跳过百度校准
    expected = round((math.hypot(100, 100) - GAP_MERGE_M) / 70.0, 1)
    assert abs(v["walkMinutes"] - expected) <= 0.1
    assert v["walkMinutes"] < plain[0]["walkMinutes"]  # 面口径显著优于直线估算
    assert v["within"] is True


def test_calibrate_skips_edge_arrival():
    """校准不覆盖边缘到达值：edgeArrival 条目保持本地估算
    （百度实测走到设施内部点位，与边缘到达口径冲突）。"""
    views = [
        {**_view("leisure", 6.3), "id": "park-1", "edgeArrival": True},
        {**_view("leisure", 5.0), "id": "park-2"},
    ]
    out = calibrate_walk_minutes(views, {"park-1": 12.0, "park-2": 7.0}, 15)
    by_id = {v["id"]: v for v in out}
    assert by_id["park-1"]["walkMinutes"] == 6.3  # edgeArrival 跳过校准
    assert by_id["park-2"]["walkMinutes"] == 7.0  # 正常条目命中校准


def test_calibrate_walk_minutes():
    views = [_view("convenience", 12.0), {**_view("health", None, walk=20.0), "estimated": True}]
    views[1]["id"] = "health-1"
    out = calibrate_walk_minutes(views, {"health-1": 9.5}, 10)
    assert out[0]["walkMinutes"] == 12.0  # 未命中保持原值
    assert out[1]["walkMinutes"] == 9.5
    assert out[1]["within"] is True
    assert "estimated" not in out[1]
    assert out[1]["_raw"] == 9.5


def test_clean_pois_dedup_no_truncate():
    """clean_pois 只清洗去重不截断：30 条去重后 28 条全保留（截断移到 select_within_pois）。"""
    raw = [
        {"id": "1", "uid": "u1", "name": "菜场A", "category": "convenience", "lng": 116.46, "lat": 39.88},
        {"id": "2", "uid": "u1", "name": "菜场A重复", "category": "convenience", "lng": 116.46, "lat": 39.88},
        {  # 无 uid：同名 + <30m 去重
            "id": "3",
            "name": "菜场B",
            "category": "convenience",
            "lng": 116.4601,
            "lat": 39.8801,
        },
        {"id": "4", "name": "菜场B", "category": "convenience", "lng": 116.4602, "lat": 39.8802},
        {"id": "5", "name": "菜场C已关闭", "category": "convenience", "lng": 116.47, "lat": 39.89},
    ]
    # 超 cap 20：clean_pois 不截断，全量保留；「已关闭」由 select_within_pois 排尾
    for i in range(25):
        raw.append(
            {
                "id": f"x{i}",
                "uid": f"ux{i}",
                "name": f"菜场{i}",
                "category": "convenience",
                "lng": 116.40 + i * 0.001,
                "lat": 39.80,
            }
        )
    out = clean_pois(raw)
    markets = [p for p in out if p["category"] == "convenience"]
    assert len(markets) == 28  # 30 条去重（uid 1 + 名称+30m 1）后全保留
    names = [p["name"] for p in markets]
    assert names.count("菜场A重复") == 0  # uid 去重
    assert names.count("菜场B") == 1  # 名称+30m 去重
    assert "菜场C已关闭" in names
    # 真实召回数打内部键
    assert all(p["_catTotal"] == 28 for p in markets)


def test_clean_pois_public_gov_excluded():
    """public：政府机关/社区政务设施/商业广场剔除；其余全量保留不截断。"""
    raw = [
        {"id": "g1", "uid": "g1", "name": "劲松街道办事处", "category": "public", "lng": 116.46, "lat": 39.88},
        {"id": "g2", "uid": "g2", "name": "朝阳区行政管委会", "category": "public", "lng": 116.46, "lat": 39.88},
        {"id": "g3", "uid": "g3", "name": "街道政务服务中心", "category": "public", "lng": 116.46, "lat": 39.88},
        # 社区政务设施（原「社区服务中心」检索词召回）不属于公共空间
        {"id": "g4", "uid": "g4", "name": "劲松街道社区服务中心", "category": "public", "lng": 116.46, "lat": 39.88},
        {"id": "g5", "uid": "g5", "name": "社区服务站", "category": "public", "lng": 116.46, "lat": 39.88},
        {"id": "g6", "uid": "g6", "name": "劲松街道党群服务中心", "category": "public", "lng": 116.46, "lat": 39.88},
        {"id": "g7", "uid": "g7", "name": "双井街道活动中心", "category": "public", "lng": 116.46, "lat": 39.88},
        # 商业健身场所不属于公共空间
        {"id": "y1", "uid": "y1", "name": "乐刻运动健身(劲松店)", "category": "public", "lng": 116.47, "lat": 39.89},
        {"id": "y2", "uid": "y2", "name": "VIP·FIT健身工作室", "category": "public", "lng": 116.47, "lat": 39.89},
        {"id": "y3", "uid": "y3", "name": "拳心拳力搏击俱乐部", "category": "public", "lng": 116.47, "lat": 39.89},
        # 商业广场（购物/百货等冠名）与商业卖场/停车场不属于公共空间
        {"id": "m1", "uid": "m1", "name": "富力购物广场", "category": "public", "lng": 116.47, "lat": 39.88},
        {"id": "m2", "uid": "m2", "name": "银泰百货广场", "category": "public", "lng": 116.47, "lat": 39.88},
        {"id": "m3", "uid": "m3", "name": "环球贸易广场", "category": "public", "lng": 116.47, "lat": 39.88},
        {"id": "m4", "uid": "m4", "name": "潘家园古玩城", "category": "public", "lng": 116.47, "lat": 39.88},
        {"id": "m5", "uid": "m5", "name": "名镜苑眼镜城", "category": "public", "lng": 116.47, "lat": 39.88},
        {"id": "m6", "uid": "m6", "name": "北京眼镜城-地上停车场", "category": "public", "lng": 116.47, "lat": 39.88},
        # 「XX广场」命名的写字楼/商场（百度 scope=2 tag 或名称特征）不属于公共空间
        {"id": "b1", "uid": "b1", "name": "东方广场", "category": "public", "lng": 116.42, "lat": 39.91,
         "baiduTag": "房地产;商业综合体"},
        {"id": "b2", "uid": "b2", "name": "中粮广场", "category": "public", "lng": 116.43, "lat": 39.91,
         "baiduTag": "房地产;写字楼"},
        {"id": "b3", "uid": "b3", "name": "东方广场-东办公楼E3座", "category": "public", "lng": 116.42, "lat": 39.91},
        {"id": "b4", "uid": "b4", "name": "中粮广场-A座", "category": "public", "lng": 116.43, "lat": 39.91},
        {"id": "b5", "uid": "b5", "name": "东方经贸城", "category": "public", "lng": 116.42, "lat": 39.91},
        {"id": "b6", "uid": "b6", "name": "王府井喜悦购物中心", "category": "public", "lng": 116.41, "lat": 39.91},
        # 超 cap 20：clean_pois 不截断（核心优先移到 select_within_pois）
        *[
            {"id": f"p{i}", "uid": f"p{i}", "name": f"社区公园{i}", "category": "public",
             "lng": 116.46, "lat": 39.88}
            for i in range(20)
        ],
        # 广场/公共绿地归入公共空间；带百度 tag 的真公共空间不被 tag 黑名单误剔
        {"id": "sq1", "uid": "sq1", "name": "劲松文化广场", "category": "public", "lng": 116.46, "lat": 39.88},
        {"id": "sq2", "uid": "sq2", "name": "市民休闲广场", "category": "public", "lng": 116.46, "lat": 39.88},
        {"id": "k1", "uid": "k1", "name": "东单公园", "category": "public", "lng": 116.42, "lat": 39.91,
         "baiduTag": "旅游景点;公园"},
        {"id": "k2", "uid": "k2", "name": "CBD文化广场", "category": "public", "lng": 116.43, "lat": 39.91,
         "baiduTag": "休闲娱乐;休闲广场"},
        {"id": "gr1", "uid": "gr1", "name": "西大望公共绿地", "category": "public", "lng": 116.46, "lat": 39.88},
        {"id": "l1", "uid": "l1", "name": "街道图书馆", "category": "public", "lng": 116.46, "lat": 39.88},
        {"id": "c1", "uid": "c1", "name": "文化活动中心", "category": "public", "lng": 116.46, "lat": 39.88},
    ]
    out = clean_pois(raw)
    publics = [p for p in out if p["category"] == "public"]
    names = [p["name"] for p in publics]
    assert len(publics) == 27  # 49 条剔除 22 条噪声后 27 条全保留，不截断
    assert all(p["_catTotal"] == 27 for p in publics)
    assert not any("办事处" in n or "管委会" in n or "政务" in n for n in names)
    assert not any("社区服务" in n or "党群" in n or "街道活动中心" in n for n in names)
    assert not any("健身" in n or "俱乐部" in n for n in names)
    assert not any("购物广场" in n or "百货" in n or "贸易广场" in n for n in names)
    assert not any("眼镜城" in n or "古玩" in n or "停车场" in n for n in names)
    # 「XX广场」写字楼/商场：tag 黑名单（东方广场/中粮广场本体）+ 名称特征（分栋/经贸/购物中心）
    assert not any(
        "东方广场" in n or "中粮广场" in n or "经贸" in n or "购物中心" in n for n in names
    )
    assert "街道图书馆" in names  # 图书馆属于公共空间
    assert "劲松文化广场" in names and "市民休闲广场" in names  # 公共广场纳入
    assert "东单公园" in names and "CBD文化广场" in names  # 带公共类 tag 的不被误剔
    assert "西大望公共绿地" in names  # 公共绿地纳入


def test_clean_pois_transport_noise_excluded():
    """transport：地铁出入口、冠名地铁站关联设施剔除；车站本体保留。"""
    raw = [
        {"id": "s1", "uid": "s1", "name": "劲松", "category": "transport",
         "lng": 116.46, "lat": 39.88, "transportKind": "metro"},
        {"id": "s2", "uid": "s2", "name": "北工大西门地铁站", "category": "transport",
         "lng": 116.46, "lat": 39.88, "transportKind": "metro"},
        # 出入口：逐口计数虚增数量
        {"id": "n1", "uid": "n1", "name": "北工大西门地铁站-A西北口", "category": "transport",
         "lng": 116.46, "lat": 39.88, "transportKind": "metro"},
        {"id": "n2", "uid": "n2", "name": "潘家园地铁站-C1东南口", "category": "transport",
         "lng": 116.46, "lat": 39.88, "transportKind": "metro"},
        {"id": "n3", "uid": "n3", "name": "平乐园地铁站-D西南口", "category": "transport",
         "lng": 116.46, "lat": 39.88, "transportKind": "metro"},
        # 冠名地铁站的关联设施：不是出行设施
        {"id": "n4", "uid": "n4", "name": "如家派柏·云酒店(北京工业大学潘家园地铁站店)",
         "category": "transport", "lng": 116.46, "lat": 39.88, "transportKind": "metro"},
        {"id": "n5", "uid": "n5", "name": "华泰饭店(劲松)地上停车场-出入口",
         "category": "transport", "lng": 116.46, "lat": 39.88, "transportKind": "bus"},
        {"id": "b1", "uid": "b1", "name": "劲松桥东公交站", "category": "transport",
         "lng": 116.47, "lat": 39.89, "transportKind": "bus"},
    ]
    out = clean_pois(raw)
    trans = [p for p in out if p["category"] == "transport"]
    names = [p["name"] for p in trans]
    assert sorted(names) == sorted(["劲松", "北工大西门地铁站", "劲松桥东公交站"])
    assert all(p["_catTotal"] == 3 for p in trans)


def test_select_within_transport_merge_and_metro_priority():
    """transport：同名站台归并（双向站台只计最近一处）+ 地铁优先保留。"""
    views = [
        # 珠江帝景双向站台（前端展示后缀「（公交）」由 transportKind 拼出）：
        # 同基础站名 + 同 kind 只留最近一处，389m 的远站台被归并
        {**_view("transport", 4.7), "id": "zj2", "name": "珠江帝景", "transportKind": "bus"},
        {**_view("transport", 3.9), "id": "zj1", "name": "珠江帝景", "transportKind": "bus"},
        # 地铁站一票制：距离比公交远仍排最前
        {**_view("transport", 8.2), "id": "jls", "name": "九龙山地铁站", "transportKind": "metro"},
        # 通名尾缀不同的同名站视为同一站：「九龙山（地铁）」与「九龙山地铁站」
        {**_view("transport", 9.3), "id": "jls2", "name": "九龙山", "transportKind": "metro"},
        {**_view("transport", 6.7), "id": "b1", "name": "九龙山路口西", "transportKind": "bus"},
    ]
    out = select_within_pois(views, 15)
    order = [p["id"] for p in out if p["category"] == "transport"]
    assert order == ["jls", "zj1", "b1"]  # 地铁优先；珠江帝景/九龙山各只留最近一处
    assert all(p["_withinTotal"] == 3 for p in out if p["category"] == "transport")


def test_select_within_health_branch_merge():
    """health：同院区科室归并（剥「-科室」后缀 + 相距≤200m），分中心/独立机构保留。"""
    main = (116.46, 39.88)
    views = [
        # 孙河社区卫生服务中心主楼 + 同址多科室（驾驶人体检/狂犬疫苗/发热门诊）→ 只留主中心
        {**_view("health", 8.0), "id": "h-main", "name": "孙河社区卫生服务中心", "_wgs": main},
        {**_view("health", 8.0), "id": "h-drive", "name": "孙河社区卫生服务中心-驾驶人体检室", "_wgs": main},
        {**_view("health", 9.3), "id": "h-rabies", "name": "孙河社区卫生服务中心-狂犬疫苗接种门诊", "_wgs": main},
        # 全角连字符变体 → 同样归并
        {**_view("health", 9.4), "id": "h-fever", "name": "孙河社区卫生服务中心－发热门诊", "_wgs": main},
        # 同主机构名但相距 ~780m 的分中心 → 独立物理点，保留
        {**_view("health", 10.9), "id": "h-branch",
         "name": "孙河社区卫生服务中心-第七社区卫生服务站", "_wgs": (116.46, 39.887)},
        # 不同机构同址 → 不归并
        {**_view("health", 2.3), "id": "h-clinic", "name": "传语兰中医诊所", "_wgs": main},
    ]
    out = select_within_pois(views, 15)
    order = [p["id"] for p in out if p["category"] == "health"]
    # 按步行分钟升序；4 条科室全部归并，分中心与独立诊所保留
    assert order == ["h-clinic", "h-main", "h-branch"]
    assert all(p["_withinTotal"] == 3 for p in out if p["category"] == "health")


def test_select_within_transport_metro_priority_in_cap():
    """transport 超 cap 时地铁站优先保留，公交站被挤掉。"""
    views = []
    for i in range(12):
        views.append({**_view("transport", 5.0 + i), "id": f"m{i}", "name": f"地铁站{i}",
                      "transportKind": "metro"})
    for i in range(30):
        views.append({**_view("transport", 1.0 + i * 0.1), "id": f"b{i}", "name": f"公交站{i}",
                      "transportKind": "bus"})
    out = select_within_pois(views, 15)
    trans = [p for p in out if p["category"] == "transport"]
    assert len(trans) == 20  # cap
    metros = [p for p in trans if p.get("transportKind") == "metro"]
    buses = [p for p in trans if p.get("transportKind") == "bus"]
    assert len(metros) == 12  # 全部地铁保留
    assert len(buses) == 8   # 公交只占剩余名额，且按距离取最近
    assert all(p["_withinTotal"] == 42 for p in trans)
    bus_raws = [p["_raw"] for p in buses]
    assert bus_raws == sorted(bus_raws)  # 残余公交按距离升序


def test_select_within_education_stages():
    """education 三段：先圈内幼儿园、再圈内小学、后圈内中学；圈外学校不参与。"""
    views = [
        # 中学（段 2）
        {**_view("education", 3.0), "id": "m1", "name": "第五中学", "eduKind": "school"},
        {**_view("education", 8.0), "id": "m2", "name": "师范大学附属中学", "eduKind": "school"},
        # 小学（段 1）
        {**_view("education", 4.0), "id": "p1", "name": "实验小学", "eduKind": "school"},
        # 幼儿园（段 0）
        {**_view("education", 9.0), "id": "k1", "name": "幼儿园A", "eduKind": "kindergarten"},
        {**_view("education", 5.0), "id": "k2", "name": "幼儿园B", "eduKind": "kindergarten"},
        # 圈外学校：不冲抵任何配额
        {**_view("education", None), "id": "k3", "name": "圈外幼儿园", "eduKind": "kindergarten"},
        {**_view("education", None), "id": "p3", "name": "圈外小学", "eduKind": "school"},
        {**_view("education", None), "id": "m3", "name": "圈外中学", "eduKind": "school"},
    ]
    out = select_within_pois(views, 15)
    order = [p["id"] for p in out if p["category"] == "education"]
    assert order == ["k2", "k1", "p1", "m1", "m2"]  # 段间幼儿园→小学→中学，段内按距离
    assert all(p["_withinTotal"] == 5 for p in out if p["category"] == "education")


def test_select_within_education_cap_stage_quota():
    """education 超 cap：三段依次占用配额（幼儿园满额后小学/中学被挤出），_withinTotal 记截断前圈内数。"""
    views = []
    for i in range(15):
        views.append({**_view("education", 1.0 + i * 0.1), "id": f"k{i}",
                      "name": f"幼儿园{i}", "eduKind": "kindergarten"})
    for i in range(10):
        views.append({**_view("education", 2.0 + i * 0.1), "id": f"p{i}",
                      "name": f"小学{i}", "eduKind": "school"})
    for i in range(3):
        views.append({**_view("education", 1.5 + i * 0.1), "id": f"m{i}",
                      "name": f"中学{i}", "eduKind": "school"})  # 距离更近仍排在小学段之后
    out = select_within_pois(views, 15)
    edu = [p for p in out if p["category"] == "education"]
    assert len(edu) == 20  # cap
    assert [p["id"] for p in edu] == [f"k{i}" for i in range(15)] + [f"p{i}" for i in range(5)]
    # 圈内真实总数 28（15+10+3）：前端据此显示 20+
    assert all(p["_withinTotal"] == 28 for p in edu)


def test_select_within_keeps_only_within():
    """所有类别只保留圈内：圈外条目不进入输出。"""
    views = [
        _view("convenience", 5.0),
        _view("convenience", None),   # 圈外
        _view("health", None),        # 圈外：该类圈外无其余设施 → 整类消失
        _view("leisure", 8.0),
    ]
    out = select_within_pois(views, 15)
    by_cat = {cat: [p["id"] for p in out if p["category"] == cat] for cat in ("convenience", "health", "leisure")}
    assert len(by_cat["convenience"]) == 1
    assert by_cat["health"] == []  # 圈内无设施 → 不输出
    assert len(by_cat["leisure"]) == 1


def test_select_within_cap_and_within_total():
    """常规类超 cap 截到 20，_withinTotal 记录截断前圈内数（前端显示 20+ 的数据基础）。"""
    views = [_view("leisure", 1.0 + i) for i in range(25)]  # 25 个圈内
    out = select_within_pois(views, 15)
    items = [p for p in out if p["category"] == "leisure"]
    assert len(items) == 20  # cap
    assert all(p["_withinTotal"] == 25 for p in items)
    raws = [p["_raw"] for p in items]
    assert raws == sorted(raws)  # 按距离升序保留最近 20
    assert max(raws) == 20.0


def test_select_within_public_core_first():
    """public 超 cap 时公园/绿地/广场核心类优先保留，图书馆/文化中心被挤掉。"""
    views = []
    for i in range(18):
        views.append({**_view("public", 5.0 + i), "id": f"p{i}", "name": f"社区公园{i}"})
    for i in range(6):
        views.append({**_view("public", 1.0 + i * 0.1), "id": f"l{i}", "name": f"街道图书馆{i}"})
    out = select_within_pois(views, 15)
    publics = [p for p in out if p["category"] == "public"]
    assert len(publics) == 20  # cap
    libs = [p for p in publics if p["name"].startswith("街道图书馆")]
    parks = [p for p in publics if p["name"].startswith("社区公园")]
    assert len(parks) == 18  # 核心类全部保留
    assert len(libs) == 2    # 图书馆只占剩余名额


def test_compute_coverage_total_precap_recall():
    """total 透出截断前真实召回数；无 _catTotal 的快照路径回退为 count。"""
    views = [{**_view("convenience", 5.0), "_catTotal": 45} for _ in range(20)]
    views.append(_view("health", 5.0))  # 无 _catTotal：回退
    cov, _ = compute_coverage(views, 15, 1.0e6, 2.0e6)
    by = {c["id"]: c for c in cov["categories"]}
    assert by["convenience"]["count"] == 20
    assert by["convenience"]["total"] == 45
    assert by["health"]["count"] == 1
    assert by["health"]["total"] == 1


def test_compute_coverage_transport_ticket():
    """transport 一票制：地铁达标 → passed=True/mode=metro；无地铁公交达标 → mode=bus；均无 → 缺口。"""
    # 地铁 4 分 < 15 → 达标
    views = [
        _view("transport", 4.0),  # metro
        _view("transport", 22.0),  # bus
    ]
    views[0]["transportKind"] = "metro"
    views[1]["transportKind"] = "bus"
    cov, _ = compute_coverage(views, 15, 1e6, 2e6)
    t = next(c for c in cov["categories"] if c["id"] == "transport")
    assert t["passed"] is True
    assert t["transportMode"] == "metro"

    # 无地铁，公交 14 分 < 15 → 达标（公交）
    views_no_metro = [
        _view("transport", 22.0),  # bus
        _view("transport", 14.0),  # bus closer
    ]
    views_no_metro[0]["transportKind"] = "bus"
    views_no_metro[1]["transportKind"] = "bus"
    cov2, _ = compute_coverage(views_no_metro, 15, 1e6, 2e6)
    t2 = next(c for c in cov2["categories"] if c["id"] == "transport")
    assert t2["passed"] is True
    assert t2["transportMode"] == "bus"

    # 均 > 15 → 缺口
    views_far = [
        _view("transport", 28.0),
        _view("transport", 35.0),
    ]
    views_far[0]["transportKind"] = "metro"
    views_far[1]["transportKind"] = "bus"
    cov3, _ = compute_coverage(views_far, 15, 1e6, 2e6)
    t3 = next(c for c in cov3["categories"] if c["id"] == "transport")
    assert t3["passed"] is False
    assert t3["transportMode"] is None


def test_compute_coverage_within_count_excludes_outside():
    """withinCount 只统计圈内：convenience 达标的 count>=3 同口径。"""
    views = [
        _view("convenience", 5.0),
        _view("convenience", 6.0),
        _view("convenience", 20.0),  # 圈外（minutes=10）
        _view("health", 8.0),
    ]
    views[2]["within"] = False
    cov, _ = compute_coverage(views, 10, 1e6, 2e6)
    by = {c["id"]: c for c in cov["categories"]}
    assert by["convenience"]["withinCount"] == 2
    assert by["convenience"]["count"] == 3
    assert by["convenience"]["passed"] is False  # 圈内数 2 < 3
    assert by["health"]["withinCount"] == 1


def test_compute_coverage_within_count_from_within_total():
    """withinCount 取 select_within_pois 打的 _withinTotal（截断前圈内数，可超 cap → 前端 20+）。"""
    views = [{**_view("education", 5.0), "_withinTotal": 28} for _ in range(20)]
    views.append(_view("health", 5.0))  # 无 _withinTotal：回退为圈内计数
    cov, _ = compute_coverage(views, 15, 1e6, 2e6)
    by = {c["id"]: c for c in cov["categories"]}
    assert by["education"]["withinCount"] == 28
    assert by["health"]["withinCount"] == 1


def test_apply_hot_tags_and_hot_aggregation():
    """重点设施打标：education 匹配重点校、health 匹配三甲、transport 仅地铁站；hot 按圈内聚合。"""
    set_hot_index_for_tests(
        {
            "key_school": ["北京市第五中学", "实验二小"],
            "sanjia": ["首都儿科研究所附属儿童医院"],
        }
    )
    try:
        views = [
            _view("education", 6.0),
            _view("education", 30.0),  # 圈外重点校不进 hot
            _view("health", 8.0),
            _view("health", 9.0),  # 非重点
            _view("transport", 4.0),
            _view("transport", 5.0),
        ]
        views[0]["name"] = "北京市第五中学"
        views[1]["name"] = "北京市第五中学分校"
        views[1]["within"] = False
        views[2]["name"] = "首都儿科研究所附属儿童医院"
        views[3]["name"] = "劲松社区卫生服务中心"
        views[4]["name"] = "劲松地铁站"
        views[4]["transportKind"] = "metro"
        views[5]["name"] = "劲松公交站"
        views[5]["transportKind"] = "bus"
        cov, _ = compute_coverage(views, 15, 1e6, 2e6)
        by = {c["id"]: c for c in cov["categories"]}
        assert by["education"]["hot"] == {"keySchool": ["北京市第五中学"]}
        assert by["health"]["hot"] == {"sanjia": ["首都儿科研究所附属儿童医院"]}
        assert by["transport"]["hot"] == {"metro": ["劲松地铁站"]}
        # 未命中类不出现 hot 键
        assert "hot" not in by["convenience"]
        # POI 视图本身打了 tags
        assert views[0]["tags"] == ["key_school"]
        assert views[4]["tags"] == ["metro"]
        assert "tags" not in views[3]
    finally:
        set_hot_index_for_tests(False)
