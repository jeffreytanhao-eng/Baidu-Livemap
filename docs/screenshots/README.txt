路演截图说明
============

本目录截图为 Playwright 实拍（非手动截图），由 apps/web/e2e/screenshots.spec.ts 产出：
DEMO_MODE 快照路径（劲松默认中心，无需 AK），viewport 1440x900，每张断言 >30KB 防白图。

重拍：

    cd apps/web
    # 先起 API(8000, DEMO_MODE=true) 与 Web(3100)，或直接由 playwright webServer 拉起
    npx playwright test e2e/screenshots.spec.ts

必拍 6 张清单（docs/comparison-report.md 按文件名引用；当前截图仍基于早期合成底图快照，劲松/中关村/南苑均已换成真实 OSM 裁片且快照已重建，路演前必须整体重拍）：

1. 01-overview-15min.png          首屏：劲松默认中心 + 默认 15 分钟自动出圈（等时分层、真实 OSM 路网与建筑、演示数据角标）
2. 02-straight-circle-compare.png 打开「直线对照圆」：圆穿楼穿河 vs 约束等时斑贴路走（对照冲击画面）
3. 03-buildings-enterability.png  建筑可进入性：住宅封闭绕开、北京富力广场等商场青色可穿行（关等时圈与 POI）
4. 04-sector-gap.png              报告栏「扇区缺口」8 向条形（当前版本已无地图扇区联动，条与诊断句高亮即可）
5. 05-blindspot-filter.png        【历史留档】盲区单类筛选：图层面板的「盲区灰块」图层与三类勾选控件均已移除，此图无法重拍
6. 06-report-print.png            报告打印版（print 媒体）：只留报告与关键图，黑白可辨

拍摄后保留本说明文件（不影响评审）。
