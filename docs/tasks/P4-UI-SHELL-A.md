# P4-UI-SHELL-A — 统一相册外壳与日常入口

日期：2026-10-10。状态：**PLANNED / 未实施**。规划收口基线 `master` / `c19749ae011a02a8fc449832cd3533daa3840ec0`；执行时重新核对工作树。

推荐 **GPT-6.1 Sol / High**。这是一张前端 A 级小改卡；独立 DS-H 展示专项复核。模型由用户手动选择。

## 视觉参考（执行前必须看图）

打开[桌面外壳模拟图](../mockups/P4-UI-PERSONAL-desktop.png)与[窄窗口模拟图](../mockups/P4-UI-PERSONAL-narrow.png)，再查看[HTML草图](../mockups/P4-UI-PERSONAL.html#photos)与[参考规则](../mockups/README.md)。必须实际用图片工具或浏览器看画面，不只读取文件名/源码。参考导航、布局、留白、配色；A只实施外壳，图中其他功能按后续卡完成。实施报告保存产品合成截图并对照说明，业务不变量优先。

## 现状盘点（备份参考）

[已有页面信息盘点](../plans/P4-UI-INFORMATION-CHECKLIST.md)供查阅和设计取舍，不要求119项全部保留、逐项汇报或逐项验收。用户此前明确强调必须增加/保留的项目，按盘点第0节的需求证据落实；其余展示可按方便、清新、少改核心接口的目标精简或调整。图中未展示不自动等于必须新增。此次纠正优先于本卡对参考信息作出的泛化“全部保留”解读；业务/人工状态及接口正确性边界不变。

## 入口与读取

依次读 AGENTS → development-status → 本卡 → [UI 总计划](../plans/P4-UI-PERSONAL.md) §2/3/5/6/8。架构只读 §10.1～10.3；策略只读 §2.2～2.4、§3.2～3.4、§4.1；§9.4只用于确认它不覆盖本新任务。

人物新建已完成用户指定的同会话工程复核与迁移修复：[复核报告](../reports/P4-PERSON-INBOX-CREATE-A-review-current-session.md)，真实个人试用待验。用户明确启用 UI 卡后才实施；不自动重开原复核，不把其代码归入本卡。只完成本卡，不顺带实现 B～F。

## 用户闭环

打开应用看到照片；五页的导航位置、名称一致；可以去人物、地点、搜索和图库管理，再回照片。没有来源时知道从哪添加。清爽外壳不是整页重写。

## 允许范围与实现要求

- `web/index.html/search.html/persons.html/places.html/map.html` 的页头/导航外壳；`styles.css` 和页级 CSS 仅做外壳兼容。
- `web/app.js` 仅改视图入口与必要当前页标记：普通 `/` 默认 browse；`/?view=sources` 到原 sources；`/#/asset/{id}` 优先打开原详情，已有详情返回上下文优先恢复。无来源的明确添加入口链接管理视图，不把请求失败当作零来源。
- 五页导航采用「照片 / 人物 / 地点」，独立「搜索照片」入口与次级「图库管理」；地图仍保留 `/map`，与地点互链。人物能力门控由各页原逻辑保持，缺人脸模型不等于已有数据不可浏览。
- 全局搜索在本卡先提供固定可见的 `/search` 入口；不引入第二个检索表单或自动发查询。文字输入跨页交接在 C 卡完成。
- 保留 DOM ID、原 script 次序和资源加载模式、原主内容 skip link、`hidden` 状态及事件绑定；地图容器布局变化后必要调用既有地图 resize，不能把地图裁成空白。
- 扩展已有颜色/间距/布局变量，尽量不新增公共 JS；保持系统暗色和键盘焦点。共享 CSS 不用无范围的 `header/main/section` 覆盖所有页。
- 不显示虚构总数、状态或无实现的设置按钮。资源版本同步更新；新本地静态资源须核对实际可被现有 package-data/web 路由提供，不改 Python 配置来迁就设计。

## 禁止与不变量

不改 `api/**`、业务 Python、schema、依赖、模型；不重构搜索、人物/地点写请求、CSRF、revision、request_id、游标。旧详情/人物/地点/地图深链可访问；认证仍用原会话。初始浏览不能启动建库/刷新/识别。只按实际状态显示入口。

## 验收（A + 小型合成浏览器）

必要时新增 `tests/unit/test_p4_ui_shell.py`：行为验证默认页、管理视图、未知参数、详情深链优先、返回状态、能力门控、无自动写请求；该文件是计划新增，须真实创建再运行。

```powershell
$env:UV_CACHE_DIR = '.uv-cache'
uv run --no-sync pytest -q tests/unit/test_p4_ui_shell.py tests/unit/test_api_frontend.py tests/integration/test_api_frontend_routes.py tests/unit/test_photo_view_info_ui.py --basetemp=.pytest-tmp/P4-UI-SHELL-A --junitxml=test-results/P4-UI-SHELL-A.xml
node --check src/photo_search/web/app.js
uv run --no-sync ruff check tests/unit/test_p4_ui_shell.py
git diff --check
```

如页级 JS 有最小兼容改动，逐一加 `node --check` 并纳入报告；不得用未运行测试名代表成功。

项目内 `.tmp/P4-UI-SHELL-A` 用少量合成记录/图片；禁止真实图库、用户服务与模型。浏览五条路由，1440×900 / 1024×768 / 640宽及200%缩放：导航当前项正确、无横向溢出、可键盘访问、无来源有入口、缩略图打开并能返回、暗色可读。只运行隔离服务，无需启动模型/worker。

独立 DS-H 审完整前端 diff、薄路由、深链/认证边界、实际浏览器及 JUnit 证据；纯展示可 `SOL_NOT_REQUIRED`。若触及新写操作/权限/错误信封/高风险状态，扩大为完整受影响面主审并按总计划§8升验收，不在 A 卡悄悄扩接口。max 只按策略§2.2证据。

## 停止与交接

必测未完写 IMPLEMENTED / VALIDATION PENDING。报告存 `docs/reports/P4-UI-SHELL-A.md`；status 只写实际摘要。无业务变化、工程复核及本卡必测完成才写本卡 PASS；下一候选 [B](P4-UI-PHOTOS-B.md)，不自动执行。不要提交/推送或修改原人物任务状态。
