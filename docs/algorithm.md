# 引擎算法说明：约束步行底图等时圈

本文面向评委讲清主引擎的技术选择与实现细节。代码位置：`packages/geo`（纯计算包，44 个测试）+ `apps/api/app/engine.py`（预热编排）。

## 1. 底图层与来源

| 层 | 来源 | 用途 |
|---|---|---|
| 步行路 / 桥 / 隧道 | OSM `highway=footway/path/steps/residential/living_street/tertiary/secondary/primary` + `bridge=yes` | 图的边 |
| 建筑平面 | OSM `building=*` 多边形 | 墙体与占地 |
| 水域 | `natural=water` / `waterway=river,canal` / `landuse=reservoir` | 硬阻隔 |
| 汽车阻隔 | `highway=motorway,motorway_link` 及无步道 `trunk` | 硬阻隔线 |
| 可进入性 | OSM 建筑标签 + 落在轮廓内的 POI 类别 | 四类判定 |

仓库预置 `data/map_fabric/{jinsong,zhongguancun,nanyuan}/` 四个 GeoJSON 裁片（roads/buildings/water/barriers）。**三个样例均为真实 OSM 裁片**：由 `data/beijing-260927.osm.pbf` 经 `scripts/import_osm_fabric.py` 切出（劲松 `--bbox 116.436,39.868,116.480,39.901`：939 条可步行道路 / 2303 个建筑面 / 4 片水域 / 253 条硬阻隔；中关村 2886/2615/29/30；南苑 387/358/7/97），早期合成包保留在 `data/map_fabric/*_synth/` 供回归测试与对照。`scripts/build_map_fabric.py --from-osm` 是另一条 osmnx 在线拉取实验路径。设计目标：评审现场不依赖在线拉取，本地包永远可用。

请求期**禁止**从 GeoJSON 现场建图：`build_engine()` 在服务启动时完成「裁片 → 可进入性分类 → 建图 → 载入内存」，请求只做「吸附 → Dijkstra → 缓冲」。

## 2. 建筑可进入性：四类判定

默认保守——**未知建筑 = 不可穿越**：

| 类别 | 判定规则 | 步行语义 | 图上颜色 |
|---|---|---|---|
| `enterable` 可进入 | `shop=mall` / `building=retail` / 轮廓内有商场、超市、菜场 POI | 轮廓内按 1.1× 步行速度生成穿行廊道，连到入口 | 青色 |
| `podium` 底商可进 | `building=office` 且轮廓贴边有底商/餐饮 POI | 只生成沿外墙内侧 **8 m** 的廊道环，禁止穿楼心 | 青描边 |
| `blocked` 不可进入 | 住宅、无底商办公主体、未知建筑 | 多边形即墙，外扩 **1.5 m** 墙厚，图边不得穿越；`entrance` 连到最近人行道 | 灰 |
| `open` 开放场地 | 有 `highway`/`foot` 标签的公园广场 | 按内部路网走，不把整块绿地当自由平面 | 浅绿 |

## 3. 建图规则（`geo.graph.build_graph`）

1. 裁切：以中心 `R = 1.4 km × (X/15)`（夹紧 0.5–2.8 km）取路、桥、建筑、水；
2. 人行候选边：人行道/居住区路/次干及有 `sidewalk` 的主干；桥与人行隧道保留；
3. **删边**：与「blocked 建筑外扩 1.5 m」或水域相交的边删除，除非 `bridge=yes` 且不与建筑相交——这是「不淌水、不穿墙」的图论保证；
4. **阻隔线**：快速路/无步道主干记为 barrier，边不得跨越（人行天桥/地道除外）；
5. **廊道**：enterable 建筑内生成简易网格连到最近入口（20 m 连接半径）；podium 生成 8 m 偏移廊道环（30 m 连接半径）；
6. **度 2 收缩**：无分叉的连续同类路段并成一条边（节点只留路口与端点），形状点保留仅供绘制——劲松真实裁片收缩后 1710 节点 / 2417 边（含预热注入的百度对照折线边）；
7. 边权 = 长度 / 速度：步行 80 m/min，台阶 50，室内廊道 70（商场内 1.1× 以 88 m/min 等效实现）；
8. 百度 DirectionLite 步行折线可拆边注入为 `source=baidu` 高权边，补 OSM 缺口（见 §7）。

吸附：中心先量化到 **25 m** 网格再查最近图节点（≤30 m），缓存键 `nodeId + minutes`——同一门口的邻点点击直接命中；吸不上时全表扫描兜底并在报告注明「已吸附到门口」。

## 4. 一次 Dijkstra，多层输出

等时圈是「一个源点到截止时间内的全部点」问题：边权非负，**带截止的 Dijkstra 就是正确且渐进最优的解法**。实现（`geo.isochrone.cutoff_dijkstra`）：二叉堆 + 截止 `X×60` 秒提前退出，返回全节点代价数组 `costs`。

- 5/10/15(/20) 各分层**不各跑一次**：从同一个 `costs` 数组按不同截止取可达边子集（`layers_from_one_run`）；
- `costs` 同时服务 POI 步行分钟（POI 吸附节点直接查表），主圈**绝不**为测时打 RouteMatrix；
- 节点级缓存：`node_costs[(region, nodeId, minutes)]`。

**为什么不用 CH / 双向 Dijkstra / A\***：双向与 A\* 服务点对点查询，对 one-to-all 等时圈没有收益；CH/HL 预处理重，且只有 PHAST 适合 one-to-all——对一块城区几千边的收缩图，普通 Dijkstra 已是毫秒级，上 CH 开发量换不来评委看得见的差异。Phase 1 做点对点路径时再评估。

## 5. 成斑：缓冲融合 + 让位，快/精两拍

- **精拍** `build_isochrone`：可达边逐条 **30 m** 缓冲（`quad_segs=8`）→ `unary_union` 融合成斑 → `difference` 让开 blocked 墙厚区与水域（允许覆盖可进入商场）→ 连通域拆分：含中心主块为主圈，**过桥飞地**单独输出并在报告说明；
- **快拍** `fast_mask`：可达边凸包化包络（粗），**仍 difference 水域与 blocked 区**——快拍可以简化几何，绝不取消约束（不穿河穿楼）；
- 两拍共用同一次 Dijkstra：`phase=fast` 目标 <300 ms 先铺一层，`phase=full` 目标 <1.5 s 出精拍多边形 + 扇区 + 报告；POI 检索与百度校准走第三拍异步，不堵第一层圈。

实测（本机，真实裁片）：劲松 15 分钟冷算引擎约 0.55 s / 编排总 0.73 s；20 分钟 0.90/1.19 s；南苑 0.07 s；命中缓存 1–5 ms（引擎侧数字，不含百度检索与 RouteMatrix 校准）。详见 `docs/comparison-report.md`。

## 6. 扇区缺口与盲区

- 8 方位扇区（`geo.sector`）：以吸附节点为圆心，比较等时斑与直线圆（半径 `80×X`）各扇面面积，`gapRatio = 等时/圆`（**达成率**，最差方向 = min）；阻隔物（水域/快速路按 100 m 里程采样挂到方位）写入 `barrierNote`，支撑「西被水域+东二环切断，需绕桥」式指认（前端只展示为文字与方位徽标，不再联动地图）；
- 1 km 盲区（`geo.blindspot`）：中心 1 km、100 m 网格，**网格范围写死不随 X 缩放**；每格判定「直线 1 km 内无该类设施」为缺，提供步行分钟时启用增强口径「步行 >X 分钟不可达」才算缺（`enhanced=True`，**前端默认开启** `includeBlindWalk=true`），因此盲区占比会随 X 变化（劲松 15→20 分钟档 100%→86.4%）；4-连通聚合成区域、长宽比 >3 标 `corridor=true`，输出为 `blindSpots` GeoJSON，报告只消费聚合后的三类占比（地图不再绘制灰块）；引擎保留 `categories` 单类/复合口径。

## 7. 与百度的关系：套合、注入、校准

| 环节 | 做法 | 约束 |
|---|---|---|
| 坐标系 | 底图 WGS84 → 计算前转 **BD09LL** 与百度底图套合；本地等距投影（米）做几何运算 | 套合误差 5–15 m，报告如实标注 |
| 折线注入 | DirectionLite 步行折线拆边，`source=baidu` 高权，纠正 OSM 缺口 | demo/无 AK：引擎**预热时**注入（图状态从启动起确定）；有 AK：第三拍按请求中心实时取折线注入 |
| RouteMatrix 校准 | 第三拍异步用实测步行分钟替换估算/图上值（`calibrate_walk_minutes`），偏差大标「底图与百度不一致」 | **绝不挡主圈**；主圈测时只用图上边权 |
| 官方等时圈 | 可选开关；接口未对公众 AK 开放，无权限时明确报错降级，以自建引擎为准 | 不作主结果 |

限流与降级：令牌桶 2 QPS、8 s 超时、429 指数退避（≤3 次）、结果缓存 24 h（内存 + 磁盘 JSON）；无 AK / `DEMO_MODE=true` 走 `data/samples` 快照 + 确定性合成 POI，界面带「演示数据」角标。

## 8. 已知边界（诚实声明）

- 真实街区精度取决于底图裁片质量与建筑标签完整度（劲松/中关村/南苑均为真实 OSM 裁片，POI 为确定性合成设施或百度检索）；
- 度 2 收缩后节点只在路口，POI 吸附存在 ±1 分钟级误差；
- 套合误差 5–15 m；跨 BD09LL/WGS84 输入统一在后端转换；
- 在线 Overpass 补全未实装（预留接口），本地包不覆盖时用最近 region 降级并标注 `degraded`。
