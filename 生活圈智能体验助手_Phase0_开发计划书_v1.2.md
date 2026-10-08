# 生活圈智能体验助手 Phase 0 开发计划书 v1.2

**适用对象**：可直接导入 vibe coding 应用进行开发任务拆解与执行  
**版本**：v1.2  
**日期**：2026-09-21  
**阶段目标**：10 月 11 日前交「复制 .env + 本地脚本」可跑的单页 Web 服务；10 月 29 日路演。比赛阶段不商用。Docker 非强制。  
**前置文档**：`生活圈智能体验助手_Phase0_开发规格说明书_v1.1.md`  
**赛事**：2026 上海开源软件应用创新大赛 · 开源 AI 工具赛道 · 百度地图企业命题  
**官网**：https://www.oschina.net/os2026/  
**产品名**：生活圈智能体验助手（赛题官方名仍为「智能体检与规划助手」，文档与界面一律用产品名）

相对 v1.0 的变更：对齐官网赛程与双套评分；15 分钟缺省 + X 分钟可配（5/10/20/自定义 5–30）；GUI 易用与前瞻写入 Epic 6；主创新「扇区缺口画像」进 P0；1 km 盲区不随 X 缩放。  
相对 v1.1：部署主路径改为本地 Python venv + Node，Docker 非前置。  
相对 v1.1.1：主引擎改为约束步行底图（路网 + 建筑可进入性 + 水域/快速路），IDW 降为对照。工期上调。  
相对 v1.2：快速出圈——预热图、一次搜索多层、快/精两拍、主圈不打百度矩阵。

---

## 1. 项目概览与里程碑

### 总体目标

- 单页：选点 → 配 X 分钟 → 开始体验 → **沿路且不穿墙的等时圈** + 覆盖 + 1 km 灰块 + 扇区缺口 + 报告
- 缺省 15 分钟；快捷 5/10/20；自定义 5–30。改参不自动请求。
- 百度经服务端代理；无 AK 用快照走完整页
- MIT、README、`.env.example`、本地启动脚本、GitHub Actions、对比报告、3 分钟口播稿

### 技术栈（锁定）

- 前端：Next.js 15 (App Router) + TypeScript + Tailwind + shadcn/ui + 百度地图 JS API GL + ECharts
- 后端：Python 3.12 + FastAPI
- 地理：numpy + shapely + networkx（或 igraph）；OSM 预裁切 GeoJSON；可选 osmnx 仅用于离线出包
- 缓存：磁盘 JSON 默认；Redis 可选
- 本地运行：Python 3.12 venv + Node 20，`scripts/dev.sh` / `scripts/dev.ps1`
- Docker Compose：可选附录，开发机未装 Docker 不影响
- 测试：pytest；Playwright 冒烟为 P1
- CI：GitHub Actions（runner 上直接装 Python/Node，不强制 build image）

### 工期（卡死提交日）

| 编制 | 建议日历 | 人日 |
|------|----------|------|
| 1 名全栈 | 16–20 天，10 月 8 日前封板 | 16–20 |
| 1 前端 + 1 后端 | 10–12 个日历日 | 20–24 |

硬节点：10-11 提交，10-16 入围，10-29 海科路 1650 号路演。

个人 AK 须勾选：地理编码、逆地理、地点检索、批量算路、步行规划（轻量）、坐标转换、JS API。

### 里程碑

| 里程碑 | 时间 | 目标 | 交付物 |
|--------|------|------|--------|
| M0 | D0–D0.5 | 仓库、.env.example、本地启动脚本、CI 空跑 | `scripts/dev.sh` 能起 API+Web |
| M1 | D0.5–D2 | 百度客户端 + mock + 限流 | 单测不打真网 |
| M2a | D2–D4 | 劲松底图包 + 可进入性 + 步行图 | `data/map_fabric/jinsong` |
| M2b | D4–D6 | Dijkstra 约束等时圈 | 沿路斑，让开住宅/水面 |
| M2c | D6 | 百度折线注入 + 校准 + 扇区 | 对照层 |
| M3 | D6–D8 | POI、打分、1 km 盲区 | `/checkup` 完整 JSON |
| M4 | D7–D10 | GUI：底图分色 + 分钟档 + 报告 | 可看见不穿墙 |
| M5 | D10–D11 | 快照 + 对比报告 | 劲松 15；5/20 截图 |
| M6 | D11–D12 | 降级、缓存、README、CI | 克隆即跑 |
| M7 | D12–D13 | 投影打磨、口播 | 3 分钟能指着河和门禁讲 |

---

## 2. Epic 与 Story

### Epic 0: 工程骨架（P0）

- Story 0.1 monorepo：`apps/api`、`apps/web`、`packages/geo`
- Story 0.2 `.env.example`（`BAIDU_SERVER_AK`、`NEXT_PUBLIC_BAIDU_JS_AK`、`DEMO_MODE`）+ `scripts/dev.sh` / `scripts/dev.ps1`（起 API:8000 与 Web:3000）
- Story 0.3 Actions：ruff/pytest + web lint/build（不用 docker build）
- Story 0.3b（P2，可选）`docker-compose.yml`，仅文档附录，不进验收
- Story 0.4 MIT、`.gitignore`（禁止 `.env` 与 AK）
- Story 0.5 `GET /health`：版本 + AK 是否配置（布尔）

### Epic 1: 百度封装（P0）

- Story 1.1 geocode / reverseGeocode / geoconv
- Story 1.2 placeSearch（radius 随 X：`max(1500, 90*X)`）
- Story 1.3 walkingMatrix，50 条切批
- Story 1.4 walkingRoute（DirectionLite，单点折线）
- Story 1.5 可选 official isochrone，无权限明确报错
- Story 1.6 令牌桶 2 QPS、超时 8 s、429 退避
- Story 1.7 fixture mock，单测不打网
- Story 1.8 latlng / lnglat 按接口写死并单测

### Epic 2: 约束步行底图 + 等时圈（P0，主创新）

- Story 2.1 离线脚本：从 OSM 裁劲松 3 km，导出路、建筑、水、快速路 GeoJSON
- Story 2.2 `MapFabricService` 读本地包，按中心+R 裁切
- Story 2.3 `EnterabilityService`：商场可进、住宅不可穿、办公+底商只走外廊
- Story 2.4 建步行图：删穿墙/穿水边，保留桥，快速路作阻隔
- Story 2.5 可进入建筑内廊道连接到入口
- Story 2.6 Dijkstra 截止 X 分钟，可达边缓冲成斑，必须让开阻隔
- Story 2.7 百度 DirectionLite 折线注入为 `source=baidu` 边
- Story 2.8 RouteMatrix 抽查校准，偏差写入报告
- Story 2.9 扇区缺口指认阻隔物
- Story 2.10 单测（硬）：人造「河 + 一座桥 + 河边住宅 + 河边商场」——圈必须过桥、进商场、不穿住宅、不淌河；外圈 24 点连线的结果不得当作主圈
- Story 2.11（P1）IDW 对照层，默认关
- Story 2.12 启动加载预构建图到内存；请求禁止从 GeoJSON 现场建图
- Story 2.13 度 2 收缩；中心吸附 25 m 网格；缓存键 `nodeId+minutes`
- Story 2.14 一次 Dijkstra 产出全部时间分层；先快拍包络/栅格，再精拍多边形
- Story 2.15 主圈不调用 RouteMatrix；百度校准异步，不挡第一层圈

### Epic 3: 覆盖与打分（P0）

- Story 3.1 8 类关键词表
- Story 3.2 去重、黑名单、每类 cap 20
- Story 3.3 步行耗时 ≤ X 记圈内
- Story 3.4 综合分纯函数，分母为 X，禁止写死 15
- Story 3.5 诊断模板：硬指标、面积比、最差扇区；LLM 可选可关

### Epic 4: 盲区（P0）

- Story 4.1 1 km、100 m 网格（不随 X）
- Story 4.2 直线口径：>1 km 无菜场/药店/小学
- Story 4.3 增强开关：步行 > X 分钟
- Story 4.4 4-连通区域 + 带状走廊
- Story 4.5 GeoJSON 供灰块

### Epic 5: 编排与缓存（P0）

- Story 5.1 `POST /checkup`：校验 minutes∈[5,30]；返回 sectorGaps、cityWhitelist
- Story 5.2 geocode、samples
- Story 5.3 缓存 `geohash5+minutes+engine`，TTL 24 h
- Story 5.4 DEMO_MODE / 无 AK 加载最近样例
- Story 5.5 白名单六城标记；名单外加 method-only
- Story 5.6 日志：minutes、api_calls、matrix_routes、elapsed、degraded

### Epic 6: Web GUI（P0，易用 + 前瞻）

- Story 6.1 三槽骨架：顶栏参数 / 地图 / 报告。品牌名「生活圈体验」，不要写死只等于 15
- Story 6.2 分钟分段 5/10/15/20，15 默认选中；自定义 5–30
- Story 6.3 「开始体验」为唯一计算入口；未应用参数黄点 + 报告水印「以下为上次结果」
- Story 6.4 默认劲松；拖点不请求
- Story 6.5 地址搜索
- Story 6.6 等时分层随 X 变图例；路网/建筑可进与不可进/水域/快速路图层
- Story 6.7 POI 图层与弹窗折线
- Story 6.8 盲区筛选
- Story 6.9 直线圆默认关
- Story 6.10 扇区描边与诊断双向定位
- Story 6.11 报告：分、扇区条、雷达、柱状、最近分钟、诊断
- Story 6.12 人话进度
- Story 6.13 演示角标、样例切换
- Story 6.14 打印、JSON、复制 `?lat=&lng=&minutes=`
- Story 6.15 <768 底栏固定分钟档 + 开始；报告抽屉
- Story 6.16 出行方式位：步行禁用芯片，标注比赛版（不实现骑行）

### Epic 7: 样例、报告、开源包装（P0）

- Story 7.1 `scripts/build_sample.py`
- Story 7.2 快照：劲松 15 完整必交；中关村 15、南苑或沪杭 15；劲松 5/20 能出图即可
- Story 7.3 `docs/comparison-report.md`（含扇区最差方向）
- Story 7.4 `docs/screenshots/`
- Story 7.5 README：本地四步启动（复制 env → 装依赖 → 起 API → 起 Web）、X 分钟、白名单、AK 勾选、配额、版权；Docker 只作为「若已安装」附录
- Story 7.6 口播：直线圆误导 → 穿不过的河与住宅 → 可进的商场 → 改 5/20 → 灰块

### Epic 8: P1

- Story 8.1 官方等时圈描边
- Story 8.2 主圈附近加密采样
- Story 8.3 Playwright：默认 15 出分；切 20 后必须再点开始
- Story 8.4 色盲纹理与打印
- Story 8.5 LLM 诊断开关

---

## 3. 开发顺序

```
Epic 0
  → Epic 1（先 mock）
      → Epic 2 ∥ Epic 3 ∥ Epic 4
          → Epic 5（minutes 校验 + sectorGaps）
              → Epic 6（D2 起用假 JSON 铺 GUI，含分钟档状态）
                  → Epic 7
                      → Epic 8
```

前端第二天就用假数据把「改分钟未体验」状态做对，避免后期返工。

---

## 4. vibe coding 任务块（可复制）

```
在 packages/geo 实现采样器。
输入 center(lng,lat)、minutes、rings=6、sectors=24。
R=1.4km*(minutes/15)，夹紧[0.5,2.8]km。
输出 144 点。pytest：minutes=15 时 R≈1.4km；minutes=5 时 R 约为 15 分钟的 1/3。
不要访问网络。
```

```
实现 minutes 校验与达标函数：
passed = nearestWalkMin <= minutes。
综合分分母用 minutes 不用字面量 15。
单测 minutes=10 与 minutes=20 同一组耗时，达标结果不同。
```

```
前端参数条：
分段 5/10/15/20，默认 15；自定义 5–30。
改档后不请求，显示「参数已改，尚未体验」。
只有「开始体验」调用 POST /api/v1/checkup。
```

```
在 packages/geo 实现带截止的 Dijkstra 等时圈。
图在测试夹具里预构建，不要在函数里读 OSM。
一次搜索返回 5/10/15 分钟可达节点；禁止循环三次。
pytest：2 万边合成图 < 100 ms；河对岸无桥则不可达。
```

```
实现 SectorGapService：
输入等时多边形与直线圆、中心点。
输出 8 个扇区的 isochroneArea、circleArea、gapRatio。
单测：正圆时各扇区 gapRatio≈1；人为切掉东南 90° 后该向 gapRatio 明显下降。
```

---

## 5. 工作量（人日）

| Epic | 人日 | 备注 |
|------|------|------|
| 0 骨架 | 0.5–1 | |
| 1 百度封装 | 1.5–2 | mock 先行 |
| 2 约束底图+等时圈 | 5–7 | 主创新，含预裁切与硬单测 |
| 3 覆盖打分 | 1–1.5 | 分母随 X |
| 4 盲区 | 1–1.5 | 1 km 固定 |
| 5 编排缓存 | 0.5–1 | |
| 6 GUI | 3.5–4.5 | 分钟状态机是新工作 |
| 7 样例文档 | 1–1.5 | 含 5/20 截图 |
| 8 打磨 | 1 | 可裁 |
| **合计** | **17–22** | 中位 19，仍按 10 月 8 日封板，GUI 可略收 |

---

## 6. 风险

| 风险 | 对策 |
|------|------|
| 地点检索日配额约 100 | mock + fixture + 预计算快照 |
| 官方等时圈无权限 | 不进关键路径 |
| X=30 半径变大、变慢 | 上限 30；进度条连续；缓存按 minutes 分键 |
| 评委把上次 15 分钟结果当成 20 | 「未应用」状态必须做 |
| 1 km 盲区被误随 X 缩放 | 代码与文案写死 1000 m |
| JS AK Referer 导致地图空白 | README；评审 AK 可临时 `*` |
| 现场无网 | DEMO 快照覆盖默认 15 全流程 |
| 只画直线圆或锯齿采样圆 | 验收直接失败；单测 2.10 卡死 |
| OSM 中国区域缺失 | 仓库预置劲松包；百度折线补边 |
| 底图与百度底图套合偏移 | 转 BD09LL；报告写 5–15 m |
| 评委认为未按「插值近似」出题 | 口播：真实可走优先；IDW 仍作对照层 |
| 开发机装不了 Docker | 主路径本来就不需要 Docker；用 venv + npm |

---

## 7. 验收与路演清单

1. 按 README 复制 `.env`、装依赖、起 API 与 Web，默认劲松 15 分钟出圈出报告
2. 切 5、再切 20，不点开始则报告仍标 15；点开始后圈明显变
3. 打开建筑分色：指住宅不可穿、商场可进；圈不得淌河，须从桥过
4. 开直线圆，讲「圆会穿过楼和河」
4. 筛「缺小学」，灰块可见
5. 换中关村样例
6. 断 AK 仍可走完，演示角标
7. 复制带 minutes 的 URL 能回填
8. Actions 绿；对比报告可翻
9. 口播不超过 3 分钟

---

## 8. 三视角审查

### 产品经理

差异点必须能投影看见：墙、水、桥、门禁。讲不清楚就只是换了个引擎名字。

### 项目经理

封板仍按 10 月 8 日。先锁劲松底图和「不穿墙不淌河」单测，再铺六城。IDW、官方等时圈、LLM、Docker 都不进关键路径。

### 用户体验经理

建筑分色图例比再加一张雷达图重要。评委三秒内要看出青的能进、灰的穿不过。
