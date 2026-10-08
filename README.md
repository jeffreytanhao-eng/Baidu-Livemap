# 百度地图15分钟生活圈（Livemap）

**指定社区中心，在「不能穿墙、不能淌水、不能上封闭汽车道」的约束下，画出沿真实路网步行的 X 分钟生活圈（缺省 15），并给出 7 类民生设施体检报告。**

产品围绕两个场景：**在特定地点附近找小区**（附近小区生活圈），以及**给特定小区出生活圈评价**（评估小区生活圈）。

2026 上海开源软件应用创新大赛 · 开源 AI 工具赛道 · 百度地图企业命题参赛作品。

版本支持**北京六环内区域高精度结果**；六环外区域自动降级为走廊近似（见「系统技术架构」）。

## 一、使用方法

### 1.1 引导页面与两个场景

打开 http://localhost:3000 首先是**场景选择页**，两个入口：

**场景 A · 附近小区生活圈（`/nearby`）**——「我想在这个位置附近找小区」

1. 地图空白处**单击即选点**（POI 层默认关闭，避免设施点拦截选点），400 ms 防抖后查询；
2. 右侧列出周边 **1 km 内小区**：名称 + 直线距离，按距离升序；
3. 每条小区可「**在地图定位**」（地图平移至该小区并挂高亮标记）或「**取消定位**」；
4. 一键跳转场景 B（URL 带小区坐标），自动开始评估；从评估页回退时用 sessionStorage 保留列表与滚动位置。

**场景 B · 评估小区生活圈（`/evaluate`）**——「给这个小区的生活圈打分」

1. 地图上选点即评估中心；点落在住宅小区（residential 面）内时自动匹配内置小区库，命中后在定位点右侧弹出**小区资讯框**：照片、售卖均价、一居/两居/三居租金区间、简介（右上角可关闭）；
2. 支持 URL 参数 `?lat=&lng=&minutes=` 回填；样例中心（劲松/中关村/南苑 150 m 内）+ 默认 15 分钟**自动跑一次**，URL 指定坐标（如附近页跳转）也自动跑；
3. 其余操作见 1.2。

### 1.2 评估场景操作流程

1. **调参数（不发请求）**：顶栏分段控件 `5 / 15`（15 带 `*` 是默认）；地图上单击选点或拖动红色中心标记（松手生效）。改完出现「参数已改，尚未体验」，报告区盖「以下为上次 X 分钟结果」水印——看到的仍是旧结果。
2. **点「开始体验」（唯一计算入口）**：两拍出圈——快拍「正在快速出圈…」约 300 ms 铺出沿路等时斑；精拍「正在叠加建筑与设施检索…」出建筑让位后的分层多边形、覆盖打分与体检报告。同一中心 + 同一分钟再点命中缓存，毫秒级返回。
3. **读报告（右侧栏 5 张卡 + 操作行）**：
   1. **综合得分**：0–100 居中展示；演示/降级时附对应徽标；
   2. **七类设施达标雷达**：连续近度分（越近分越高），60 分线即达标线，形状与下方条形图逐条对应；
   3. **最近设施步行分钟**：横条图，红色虚线 = X 分钟阈值；
   4. **达标明细**：类别 / 数量 / 最近步行分钟 / 是否达标；**行悬停弹出设施浮层**（设施名 + 步行距离 + 步行分钟，重点设施带徽标），点设施名即在地图上定位高亮；
   5. **体检诊断**：3–6 条白话结论，带方位徽标指认缺口方向。

   末尾操作行：打印 / 下载 JSON / 复制分享链接（链接带 `lat,lng,minutes`，打开即回填）。
4. **图层（左下「图层」面板）**：等时分层、路网、建筑可进入性、水域、快速路/阻隔、设施点六项默认全开，开关即时生效；面板附等时图例（5 分钟绿 / 10 分钟蓝 / 15 分钟琥珀）、建筑四色与 7 类设施色例。**建筑四色**：青 = 可进入（商场）、深青描边 = 裙楼底商、灰 = 封闭院落（住宅，不可穿）、浅绿 = 开放空间。

### 1.3 口径与规则

- 等时分层随 X 变化：15 → 5/10/15 三层；其它取值 2–4 层且必含 X；覆盖达标阈值 = X 分钟，直线对照圆半径 = 80 m/min × X；
- 七类设施：便利生活（≥3 个且最近 ≤ X）、娱乐休闲、商超购物、学习教育、医疗养老、公共空间、交通出行（一票制：地铁站 ≤ X 即达标，否则公交站 ≤ X 兜底达标）；
- 综合分公式：`0.40×覆盖7 + 0.20×锚点三项（便利生活/医疗养老/学习教育） + 0.15×(1 − 锚点最近均值/X) + 0.15×交通分量（地铁 1.0 / 仅公交 0.6 / 缺失 0） + 0.10×min(1, 等时圈面积/直线圆面积)`，分母恒为当前 X；
- 白名单六城（北京/上海/杭州/成都/广州/深圳）完整体验；名单外照算，顶栏标「未做设施词表校准，仅验证方法」；境外坐标拒绝。

启动与部署见「五、部署方法」。

## 二、设计思想

### 1. 两个场景回答两个问题

- 「我打算在这一带活动/居住，**附近有哪些小区**，离我多远」——场景 A 用轻量列表直接回答；
- 「**这个小区的生活圈到底怎么样**」——场景 B 用同一套引擎出等时圈 + 体检报告 + 综合分。

两个场景共用同一份小区基础数据（名称、坐标、售卖均价、租金区间、简介、照片）与同一个地理计算引擎，口径完全一致。

### 2. 等时圈沿真实路网生长，不是扣一个圆

「步行 15 分钟能到哪」的真实答案受三重约束：住宅楼穿不过、未架桥的河过不去、无步道快速路上不去。本项目把这三种约束显式建模成图论问题：

- **底图四层**：可步行路网（roads）、建筑面（buildings）、水域（water）、硬阻隔（barriers，快速路/铁路/封闭汽车道），全部来自真实 OpenStreetMap 裁片；
- **建图规则**：与墙厚区或水域相交的边直接删除（天桥、人行地道除外）；阻隔线两侧的边不得跨越；建筑不是障碍而是「可进入性」问题（见下一条）；
- **建筑可进入性四类判定**：商场可穿行（1.1× 步速走大厅/底商廊道）、办公只走底商 8 m 廊道、住宅不可穿（外扩 1.5 m 墙厚）、开放场地按内部路网走。

在这个图上跑一次**截止 Dijkstra**（截止时间 = X×60 秒），一次搜索同时出 5/10/15 全部分层——等时斑天然贴路、让位建筑与水面。

### 3. 主圈只依赖本地底图，不等百度

等时圈测时**只用图上边权**；百度能力（POI 检索、RouteMatrix 实测耗时校准、步行折线对照）走**第三拍异步**，不阻塞出圈。快/精两拍先行：快拍约 300 ms 铺出沿路等时斑，精拍叠加建筑让位与设施检索。缓存命中毫秒级返回。

### 4. 地图渲染：百度只出瓦片，矢量全部自绘

百度地图只承担底图瓦片与手势；等时分层、路网、建筑四色、水域、阻隔、设施点全部由一张覆盖在地图容器上的自绘 `<canvas>` 绘制。路径按经纬度构建一次缓存为 `Path2D`，平移缩放只改变换矩阵。几千个矢量要素若走「一要素一 overlay」会撞上 BMapGL WebGL 管线的 framebuffer 崩溃，自绘方案 overlay 数为 0。未配 JS AK 或加载失败时自动降级为内置 SVG 底图，功能不受影响。

### 5. 报告可解释

雷达分、达标明细、体检诊断全部由同一份「最近步行分钟」数据推导，口径一致、逐条对得上；诊断句直接指认「哪一侧被什么挡住」，而非只报一个面积比。

## 三、创新点

1. **OSM 建筑外沿全量引入**：建筑不是简单障碍，而是按「可进入性」四类判定（商场可穿行 / 办公走底商廊道 / 住宅封闭外扩 1.5 m 墙厚 / 开放场地按内部路网），等时圈绕行行为与真实步行体验对齐；
2. **路网/水域隔断之后的连通恢复**：`bridge=yes` 天桥与 `tunnel=yes` 人行地道（footway/path/steps）允许跨越水域与阻隔，车行隧道不进步行图；高架道路类 barrier 行人在交叉口处以高架/地道形式穿越、不阻隔；河流、铁路、快速路等长线要素切片时按片 bbox 裁剪几何，避免整条河被复制进沿途每张片导致串片；
3. **设施点「边缘到达」判定步行时间**：面状设施（公园等）的 POI 标签常在设施内部，普通 30 m 吸附够不到路网——在 250 m 接驳半径内取「节点耗时 + 末段直线接驳」最小值，近似「到达设施边缘即算到达」；公园/绿地类面设施标签贴边 ≤60 m 时直接以「到达面边界」为准；这类点打 `edgeArrival` 标记，第三拍 RouteMatrix 实测校准自动跳过（百度实测走到内部点位，口径天然偏大）；
4. **设施在地图上一键定位**：达标明细悬停浮层内点设施名，地图 `panTo` 并挂高亮标记与名称标签（百度模式 3D 倒圆锥 / SVG 模式标签，双底图实现同款交互）；附近页列表「在地图定位」同款效果；
5. **重点设施打标加亮**：对 education/health/transport 三类按词表打标 `key_school`（重点学校）/ `sanjia`（三甲医院）/ `metro`（地铁站），在达标明细与悬停浮层中以徽标突出显示；
6. **一次搜索出全部分层**：截止 Dijkstra 一次搜索同时输出 5/10/15 全部分层，快拍约 300 ms；
7. **降级是显式契约**：六环外不硬算，走廊近似 + 「已降级」徽标，不把近似结果伪装成高精度；
8. **小区维度数据联动**：内置小区库（均价/租金/照片/简介）与路网评估打通——点哪评哪，资讯框即看小区基本信息，附近页与评估页互相跳转且状态保留。

## 四、系统技术架构

### 1. 分层结构

```
apps/web    Next.js 15 + TS + Tailwind + shadcn/ui + ECharts
  app/page.tsx          场景选择引导页
  app/nearby/           场景 A：附近小区生活圈
  app/evaluate/         场景 B：评估小区生活圈
  components/map/       MapView（百度 GL / 内置 SVG 双底图实现）、
                        自绘 canvas 矢量层、小区资讯框
  components/report/    ReportPanel（得分/雷达/明细悬停/诊断/打印）
apps/api    FastAPI BFF：编排、两拍出圈、缓存、百度代理、降级、
            SQLite 小区库查询（community/locate、community/nearby）
packages/geo 地理计算引擎包：底图加载/可进入性/建图/等时圈/打分/盲区/扇区
data        map_fabric 预切四层底图 + SQLite 小区库 + 演示快照
```

### 2. 北京六环内：真实 OSM 全量网格片（高精度）

六环内（覆盖面积约 3200 km²）全部使用真实 OSM 路网与建筑，按 4 km 网格预切，离线数据管线：

```
data/beijing-260927.osm.pbf（Geofabrik 北京抽取）
  → scripts/import_osm_fabric.py --ring6     # PBF → 六环全量四层 GeoJSON
  → scripts/tile_fabric.py --source ...      # 全量 → 4km 网格片（bbox 外扩 2200m 邻域）
  → data/map_fabric/b6r_rXXcYY/              # 每片 roads/buildings/water/barriers 四层 GeoJSON
```

关键细节：

- **长线要素按片裁剪**：见「创新点 2」；water/barriers 两层按片 bbox 裁剪几何后再落盘；
- **按片名直取，不按点猜**：建图与底图接口均先算请求点所属网格片名（`tile_indices`），再对指定片裁切（`clip_region`），杜绝相邻片边界框重叠导致的错选；
- **TileManager 懒加载 + LRU 常驻 8 片**：首次点入新片现场建图约 2–5 s，命中缓存 <300 ms；
- **覆盖已验证**：ring6 边界多边形内均匀采样 11,167 点，逐点核对网格片，缺片 0 个；
- 三个样例区域（劲松/中关村/南苑）另备**全量精修裁片**，启动即预热入内存，体验毫秒级。

### 3. 六环外与名单外区域：走廊近似（自动降级）

1. **动态走廊生成**：以请求中心为原点，向 8 个方向各取一条百度 DirectionLite **真实步行路线**，按 80 m/min 沿折线在各时间层截止处截断，缓冲 50 m 并取中心 100 m 缓冲的并集，作为可达区域近似；
2. **POI 与耗时仍走百度实测**：设施检索与 RouteMatrix 算路与六环内同一套第三拍逻辑，缺口结论依然真实；
3. **明确标注**：报告附「已降级」徽标，吸附说明注明「走廊近似」。

### 4. 一次「开始体验」内部发生了什么

```
参数 → 中心吸附到最近可走节点（≤30 m；超出则提示「中心不在可走道路上，已吸附到最近可走点（约 X m）」）
     → 内存图上跑一次截止 Dijkstra（X×60 秒，一次搜索出全部分层）
     → 快拍：可达边包络 / 栅格掩膜（不穿墙、不穿水）
     → 精拍：多边形让位建筑与水面、覆盖打分、模板诊断
     → 第三拍（不挡前两拍）：百度折线对照注入、POI 检索、RouteMatrix 校准
```

### 5. API 摘要

| 接口 | 方法 | 说明 |
|---|---|---|
| `/api/v1/checkup` | POST | X 分钟生活圈体检主接口（`minutes` 校验 5–30，`phase=fast/full` 两拍） |
| `/api/v1/fabric` | GET | 底图裁片（路网/建筑分色/水域/阻隔，BD09LL GeoJSON） |
| `/api/v1/geocode` | POST | 地理编码（无 AK 时内置劲松/中关村/南苑映射） |
| `/api/v1/reverse` | POST | 逆地理编码 |
| `/api/v1/community/locate` | GET | 点击定位小区：residential 面名精确匹配 → 500 m 内坐标兜底 → 未命中，供资讯框展示 |
| `/api/v1/community/nearby` | GET | 附近小区列表（半径 100–5000 m，按距离升序，回传 BD09 坐标） |
| `/api/v1/samples` | GET | 演示样例列表 |
| `/health` | GET | 状态、版本、AK/演示模式、图规模 |

### 6. 目录结构

```
apps/
  api/            FastAPI BFF（见上）
    app/          main/checkup/coverage/community/db/engine/baidu/cache/llm...
    tests/
  web/            Next.js 15 前端（见上）
    e2e/          Playwright 冒烟测试（不进 CI）
packages/
  geo/            地理计算引擎包
    tests/
data/
  map_fabric/     预裁底图包（四层 GeoJSON：roads/buildings/water/barriers）
    jinsong/        劲松：真实 OSM 精修裁片（939 路网 / 2303 建筑 / 4 水域 / 253 阻隔）
    zhongguancun/   中关村：真实 OSM 精修裁片（2886 / 2615 / 29 / 30）
    nanyuan/        南苑：真实 OSM 精修裁片（387 / 358 / 7 / 97）
    b6r_rXXcYY/     六环 4km 全量网格片（生成物，不进 Git，合计 ~300MB）
    *_synth/        劲松/中关村/南苑结构合成包（引擎回归测试与对照）
  samples/        5 份演示快照 JSON（劲松 5/15/20、中关村 15、南苑 15）
  livemap.db      SQLite 小区库（名称/坐标/售卖均价/租金区间/简介/照片索引）
  uploads/communities/  小区照片（资讯框展示）
  hot_facilities.json   热门设施词表（达标明细悬停提示）
  *.osm.pbf       Geofabrik 原始 OSM 抽取（北京 35 MB 等），仅用于离线重切底图，不进 Git
scripts/
  dev.ps1 dev.sh          本地一键启动
  import_osm_fabric.py    真实 OSM 底图管线：PBF → 四层 GeoJSON（pyosmium，单处理器双扫）
  tile_fabric.py          全量底图 → 4km 邻域 pad 网格片（water/barriers 按片裁剪）
  import_hot_facilities.py  热门设施词表导入
  build_map_fabric.py     结构合成底图包生成
  build_sample.py         快照离线生成
  build_demo_routes.py    演示步行折线预生成
  compare_report.py       对比报告取数（docs/comparison-report.md 的来源）
docs/
  algorithm.md         引擎算法说明（技术深度）
  comparison-report.md 约束底图 vs 直线圆对比报告（真实计算数字）
  demo-script.md       3 分钟路演口播稿
  screenshots/         路演截图
.env.example           服务端环境变量样例（复制为 .env；AK 不进 Git）
apps/web/.env.example  前端环境变量样例（复制为 apps/web/.env.local；AK 不进 Git）
.github/workflows/ci.yml
```

## 五、部署方法

### 1. 环境要求

Python 3.12+、Node.js 20+。Docker 可选。

### 2. 本地启动（四步）

```bash
# 1. 复制环境文件（无 AK 也能演示）
cp .env.example .env                          # Windows: copy .env.example .env
cp apps/web/.env.example apps/web/.env.local  # Windows: copy apps\web\.env.example apps\web\.env.local

# 2. 装依赖
cd apps/api && python -m venv .venv
.venv/Scripts/pip install -r requirements.txt   # Windows
# .venv/bin/pip install -r requirements.txt     # macOS/Linux
.venv/Scripts/pip install -e ../../packages/geo # Windows
# .venv/bin/pip install -e ../../packages/geo   # macOS/Linux
cd ../web && npm install && cd ../..

# 3. 起 API（终端 1，端口 8000，cwd 必须在 apps/api）
cd apps/api && .venv/Scripts/uvicorn app.main:app --port 8000
# macOS/Linux: cd apps/api && .venv/bin/uvicorn app.main:app --port 8000

# 4. 起 Web（终端 2，端口 3000）
cd apps/web && npm run dev
```

**环境文件说明**：API 读仓库根 `.env`；Next.js 只读 `apps/web/.env.local`，把 `NEXT_PUBLIC_*` 写在根 `.env` 里不会生效。改完前端 env 必须重启 `npm run dev`（`NEXT_PUBLIC_*` 只在启动时注入）。端口被占用时 `npm run dev -- -p 3100` 换端口即可（API 的 CORS 已放行 localhost 任意端口）。

### 3. 配置百度 AK（可选）

在[百度地图开放平台](https://lbsyun.baidu.com/)创建两个 AK（服务端与浏览器端分离，均不进 Git）：

| AK | 填到哪个文件 | 需勾选能力 |
|---|---|---|
| 服务端 AK | 仓库根 `.env` → `BAIDU_SERVER_AK` | 地理编码、逆地理编码、地点检索、批量算路（RouteMatrix）、步行规划轻量（DirectionLite）、坐标转换 |
| JS API AK | `apps/web/.env.local` → `NEXT_PUBLIC_BAIDU_JS_AK` | JavaScript API GL（**浏览器端类型**）；Referer 白名单本地填 `localhost`（评审可临时填 `*`） |

两个 AK 相互独立：只填服务端 AK 出真实设施与耗时，底图仍是内置 SVG；只填 JS AK 有百度底图，设施走演示快照。服务端 AK 为空（或 `DEMO_MODE=true`）即为演示模式：共用同一套引擎，设施来自预计算快照 + 确定性合成，断网可演示，界面带「演示数据」徽标。切换模式改 `.env` 后重启 API 进程生效。

### 4. Docker Compose（可选）

```bash
docker compose up --build
# api → 8000，web → 3000
```

- AK 从根目录 `.env` 注入 api 容器，不进镜像；
- web 的 `NEXT_PUBLIC_*` 走 build args（`NEXT_PUBLIC_API_BASE`、`NEXT_PUBLIC_BAIDU_JS_AK`），改 AK 需重建 web 镜像。

### 5. 底图数据重建

见「系统技术架构 · 2」的管线命令；三个样例精修裁片由 `import_osm_fabric.py --bbox` 切出（劲松 `--bbox 116.436,39.868,116.480,39.901`，中关村/南苑见脚本内同名参数）。

## 六、其它与开源协议

### 测试与 CI

```bash
# 后端 + 引擎（全离线，不依赖 AK）
pytest apps/api/tests packages/geo/tests -q
# 代码风格
ruff check apps/api packages/geo scripts
# 前端
cd apps/web && npm run lint && npm run build
# E2E 冒烟（Playwright，不进 CI）
cd apps/web && npx playwright install chromium   # 首次
npx playwright test
```

- E2E 需先起好 API；Web 由 Playwright 自动拉起到 **3100** 端口（避开常见 3000 占用），`/api/*` 请求由测试代理转发到 8000；离线装不上浏览器时跳过即可，其余测试不受影响；
- GitHub Actions（`.github/workflows/ci.yml`）：`ruff` + `pytest`（Python 3.12）与 `npm run lint` + `npm run build`（Node 20）。

### 许可

- **代码**：MIT（见 [LICENSE](LICENSE)）；
- **地图展示与 POI、步行算路数据**：版权归**百度**所有；
- **底图数据（OSM 裁片）**：`data/map_fabric/{jinsong,zhongguancun,nanyuan}/` 均为**真实 OpenStreetMap 裁片**（ODbL，© [OpenStreetMap](https://www.openstreetmap.org/) contributors），由 `data/beijing-260927.osm.pbf` 经 `scripts/import_osm_fabric.py` 切出；`*_synth/` 为结构合成的演示数据（字段与标签体系同 OSM 裁切一致，供回归测试与对照）。再分发或再次裁切时请保留 ODbL 署名。
