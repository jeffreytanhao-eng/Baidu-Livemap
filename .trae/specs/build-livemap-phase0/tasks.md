# Tasks

## 阶段一：工程骨架与百度封装（可并行起步）

- [x] Task 1: 工程骨架（Epic 0）
  - [x] 1.1 monorepo 结构：`apps/api`（FastAPI）、`apps/web`（Next.js 15 + TS + Tailwind + shadcn/ui）、`packages/geo`
  - [x] 1.2 `.env.example`（`BAIDU_SERVER_AK`、`NEXT_PUBLIC_BAIDU_JS_AK`、`DEMO_MODE`）+ `scripts/dev.sh` / `scripts/dev.ps1`（起 API:8000 与 Web:3000）
  - [x] 1.3 GitHub Actions CI：ruff/pytest + web lint/build（不用 docker build）
  - [x] 1.4 MIT LICENSE、`.gitignore`（禁 `.env` 与 AK）、`GET /health`（版本 + AK 配置布尔）

- [x] Task 2: 百度服务端封装（Epic 1，mock 先行）
  - [x] 2.1 geocode / reverseGeocode / geoconv，latlng 与 lnglat 顺序按接口写死并单测
  - [x] 2.2 placeSearch（检索半径 `max(1500, 90*X)`）
  - [x] 2.3 walkingMatrix（50 条切批）+ walkingRoute（DirectionLite 折线）
  - [x] 2.4 令牌桶 2 QPS、超时 8 s、429 退避；可选 official isochrone 无权限明确报错
  - [x] 2.5 fixture mock，全部单测不打真网

## 阶段二：主创新——约束步行底图与等时圈（核心，依赖 Task 1）

- [x] Task 3: 底图数据与可进入性（Epic 2 上半）
  - [x] 3.1 离线脚本从 OSM 裁劲松 3 km，导出路/建筑/水/快速路 GeoJSON 至 `data/map_fabric/jinsong`
  - [x] 3.2 `MapFabricService`：启动时读本地包入内存（带空间索引），按中心+R 裁切；覆盖不到时 Overpass 补全，失败降级预置包+百度折线
  - [x] 3.3 `EnterabilityService`：商场可进（1.1× 速度穿行）/住宅不可穿（外扩 1.5 m 墙厚）/办公+底商只走外墙内侧 8 m 廊道/开放场地按路网；未知建筑默认不可穿越
  - [x] 3.4 建步行图：删穿墙穿水边、保留桥、快速路作阻隔、可进入建筑内廊道连入口；边权=长度/速度（80/50/70 m/min）

- [x] Task 4: Dijkstra 等时圈与快速出圈（Epic 2 下半）
  - [x] 4.1 带截止 Dijkstra：一次搜索产出全部时间分层（禁止每层各跑一次）；中心吸附最近可走节点 ≤30 m
  - [x] 4.2 可达边 25–35 m 缓冲融合成斑，让开不可进入建筑与水面；主块+过桥飞地分开画
  - [x] 4.3 度 2 收缩；吸附 25 m 网格；缓存键 `nodeId+minutes`
  - [x] 4.4 快/精两拍：快拍 <300 ms（边包络或 15 m 栅格掩膜，不穿河穿楼），精拍 <1.5 s（建筑让位多边形+扇区）
  - [x] 4.5 主圈不打 RouteMatrix；百度折线注入 `source=baidu` 高权边与校准走异步第三拍
  - [x] 4.6 硬单测（Story 2.10）：「河+桥+河边住宅+河边商场」夹具——圈必须过桥、进商场、不穿住宅、不淌河；锯齿圆/24 边形不得作主圈；2 万边合成图 Dijkstra <100 ms

- [x] Task 5: 扇区缺口（Story 2.9）
  - [x] 5.1 `SectorGapService`：8 方位 isochroneArea/circleArea/gapRatio
  - [x] 5.2 诊断指认阻隔物（如「东南被通惠河+快速路切断」）；单测：正圆 gapRatio≈1，切东南 90° 后该向明显下降
  - [x] 5.3（P1）IDW 对照层，默认关

## 阶段三：覆盖、盲区、编排（与阶段二后半可并行）

- [x] Task 6: POI 覆盖与打分（Epic 3）
  - [x] 6.1 8 类关键词表；uid 去重/名称+30 m 去重/偏移过滤/已关闭降权/每类 cap 20
  - [x] 6.2 步行耗时 ≤ X 记圈内；综合分纯函数分母为 X；单测 minutes=10 与 20 同耗时不同达标
  - [x] 6.3 诊断模板（硬指标/面积比/最差扇区）3–6 条，LLM 可选可关缺 Key 走模板

- [x] Task 7: 1 km 盲区（Epic 4）
  - [x] 7.1 中心 1 km、100 m 网格（写死 1000 m，不随 X）
  - [x] 7.2 直线口径缺菜场/药店/小学；增强开关「步行 >X 分钟不可达」
  - [x] 7.3 4-连通聚合区域 + 带状「灰色走廊」，输出 GeoJSON 灰块

- [x] Task 8: 编排与缓存（Epic 5）
  - [x] 8.1 `POST /api/v1/checkup`：minutes 5–30 整数校验（否则 400）；返回 `meta.minutes`、`sectorGaps[]`、`meta.cityWhitelist`、快拍/精拍字段
  - [x] 8.2 `POST /api/v1/geocode`、`GET /api/v1/samples`、`GET /health`
  - [x] 8.3 缓存 `geohash5+minutes+engine` TTL 24 h，磁盘 JSON 默认，不依赖 Redis/Docker
  - [x] 8.4 DEMO_MODE/无 AK 加载最近样例；白名单六城标记，名单外 method-only 提示
  - [x] 8.5 日志：minutes、api_calls、matrix_routes、elapsed、degraded

## 阶段四：Web GUI（D2 起用假 JSON 先行，依赖 Task 8 契约）

- [x] Task 9: 三槽骨架与参数状态机（Epic 6 上半）
  - [x] 9.1 顶栏参数/地图/报告三槽；品牌「生活圈体验/Livemap」；首屏 3 s 内见图与默认劲松点
  - [x] 9.2 分钟分段 5/10/15/20（15 默认选中）+ 自定义 5–30（点「自定义」才展开，非法内联提示）
  - [x] 9.3 「开始体验」唯一计算入口；改参后黄点「参数已改，尚未体验」+ 报告水印「以下为上次结果」；拖点不请求
  - [x] 9.4 地址搜索（地理编码+逆地理回填）；分享 URL `?lat=&lng=&minutes=` 回填

- [x] Task 10: 图层与报告（Epic 6 下半）
  - [x] 10.1 图层：约束等时分层（随 X 变图例）/路网/建筑可进（青）与不可进（灰暖）/水域/快速路阻隔/POI/盲区默认开；直线圆、IDW 对照默认关
  - [x] 10.2 POI 弹窗步行折线；盲区单类/复合筛选灰块
  - [x] 10.3 扇区描边与诊断双向定位互跳
  - [x] 10.4 报告：综合分、扇区缺口条、雷达、柱状、最近分钟表、诊断；打印样式 + JSON 下载
  - [x] 10.5 人话进度；失败保留上次图层 +「用演示数据」出口；演示角标与样例切换
  - [x] 10.6 <768 px 底栏固定分钟档+开始、报告抽屉；出行方式「步行·比赛版」禁用芯片

## 阶段五：样例、文档、打磨

- [x] Task 11: 样例与开源包装（Epic 7）
  - [x] 11.1 `scripts/build_sample.py`；劲松 15 完整快照必交；中关村 15、南苑或沪杭 15；劲松 5/20 可出图
  - [x] 11.2 `docs/comparison-report.md`（三社区 15 分钟面积比/三类耗时/盲区占比/扇区最差方向 + 劲松 5/20 截图 + 10 个 POI 抽查 vs 百度 App）
  - [x] 11.3 README：本地四步（复制 env→装依赖→起 API→起 Web）、X 分钟、白名单、AK 勾选、配额、版权；Docker 仅附录
  - [x] 11.4 3 分钟口播稿：直线圆误导→穿不过的河与住宅→可进的商场→改 5/20→灰块

- [x] Task 12: 打磨（Epic 8，P1 可裁）
  - [x] 12.1 官方等时圈描边（有权限才画）
  - [x] 12.2 Playwright 冒烟：默认 15 出分；切 20 未点开始不出新结果
  - [x] 12.3 色盲纹理与打印黑白可辨；LLM 诊断开关

# 验收修复任务（第一轮核验：1 不通过 + 4 部分）

- [x] Fix 1: 百度步行折线注入（对应 checklist 不通过项）
  - [x] F1.1 geo 图 Edge 增加 source 字段；提供 inject_baidu_polyline(graph, polyline_lnglat) 拆折线为 `source=baidu` 高权边（速度按 80 m/min、权=长度/速度×0.9 之类更高优先）
  - [x] F1.2 编排层第三拍：DEMO 无 AK 时用合成折线（沿现有路网取 2-3 条门口→商场路径）演示注入；有 AK 时调 DirectionLite 注入；诊断固定加一条「与百度步行大体重合；本圈更保守，因住宅不可穿」
  - [x] F1.3 响应新增 baiduRoutes:[{name, path:[[lng,lat]...]}]；前端加「百度步行对照」图层（默认关），虚线描边可叠看；types.ts 同步
  - [x] F1.4 单测：注入后该路径上图且 source=baidu；诊断含保守说明

- [x] Fix 2: 人行地道支持（checklist 部分项）
  - [x] F2.1 graph.py：properties tunnel=yes 的 footway/path 边允许穿越水域与 barrier（与 bridge 同级别的例外）
  - [x] F2.2 硬单测：河+一条人行地道（无桥）时圈必须经地道过河

- [x] Fix 3: 盲区单类/复合筛选（checklist 部分项）
  - [x] F3.1 checkup 请求加 blindFilter:["market","pharmacy","primary_school"] 可选参数，blindSpots 只算所选类
  - [x] F3.2 前端图层面板「盲区灰块」展开三类 checkbox（默认全选=复合），改动后随下次「开始体验」生效；单测覆盖过滤逻辑

- [x] Fix 4: 对比报告证据补强（checklist 部分项）
  - [x] F4.1 用 Playwright（chromium 已装）起 API+Web 实拍 6 张截图到 docs/screenshots/（按 README.txt 清单：默认15全景/直线圆对照/建筑分色+桥/扇区缺口/灰块筛选/移动端或打印）
  - [x] F4.2 comparison-report.md 嵌入截图引用；10 POI 表补注「有 AK 后用 RouteMatrix 校准填真实对照列」的操作步骤

# Task Dependencies

- Task 2 依赖 Task 1（.env 与骨架）
- Task 3、Task 4 依赖 Task 1；Task 4 依赖 Task 3
- Task 5 依赖 Task 4（等时多边形）
- Task 6 依赖 Task 2（placeSearch/walkingMatrix）；Task 6 与 Task 3/4 可并行
- Task 7 依赖 Task 2；与 Task 6 可并行
- Task 8 依赖 Task 4、5、6、7（编排聚合）
- Task 9、10 依赖 Task 8 接口契约（可用假 JSON 提前开工）
- Task 11 依赖 Task 8、10（快照需完整链路）
- Task 12 依赖 Task 10
