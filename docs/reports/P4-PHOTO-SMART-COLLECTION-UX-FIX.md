# P4-PHOTO-SMART-COLLECTION-UX-FIX 执行报告

**日期：** 2026-10-20
**状态：** IMPLEMENTED / ACCEPTANCE PARTIAL
**边界：** 已按用户要求本地提交，未推送；仅使用隔离 synthetic 数据，未访问真实图库；未改后端/schema/依赖。

## 目的与改动

修复智能合集目录与详情页视觉/状态问题，统一缩略图卡片，接入自动分页和可重试回退，并隔离目录、metadata、结果页、排除页与刷新观察的异步身份。FILTER 默认使用拍摄时间排序；目录类型与状态改为中文短文案，NO_CACHE 不再显示候选数 0。后续按用户要求，搜索页新建智能合集时默认以当前确认搜索关键词填入命名框，替换规则时保留原合集名。

改动文件：

- `src/photo_search/web/app.js`：分页单飞/独立 cursor、409 停止与重载入口、视图身份守卫、刷新观察、排除和恢复、缩略图路径及状态文案。排除成功重置 epoch 后曾使 `mutationInFlight` 永久保留；已修复释放逻辑并加入回归测试。
- `src/photo_search/web/index.html`：详情哨兵与重载控件、智能合集专用类名及资源版本号。
- `src/photo_search/web/styles.css`：仅作用于智能合集的卡片、缩略图框、焦点与窄屏布局；普通合集创建表单在智能目录隐藏。
- `src/photo_search/web/search.js` / `search.html`：新建智能合集名称取当前已确认快照的 `query.text`（最多 80 个 Unicode 字符），输入保持可编辑；更新脚本资源版本。替换规则仍使用原合集名称。
- `tests/unit/test_smart_collection_frontend.py`：独立游标、图片 URL、状态文案、分页单飞/409 保留结果、迟到 metadata 丢弃及排除互斥锁释放的前端测试。
- 本轮刷新生命周期补修仅改 `src/photo_search/web/app.js` 与 `tests/unit/test_smart_collection_frontend.py`：观察身份跨排序/标签切换保持并按真实合集访问结束；按队列/运行、失败、新 build 发布与无法确认分类；有界错误清理；首屏失败保留旧卡片及其快照身份，并在成功新第一页校验后才切换。
- `tests/unit/test_search_text_mode_ui.py`、`tests/unit/test_photo_view_info_ui.py`：覆盖保存对话框默认名称、80 个 Unicode 码点截断、替换时保留原名和脚本版本。

## 验证

最终受影响 JUnit：**77 passed，0 failed，0 errors，0 skipped**。原始结果：`test-results/P4-PHOTO-SMART-COLLECTION-UX-FIX-affected-r12.xml`。前端专项：**3/3 passed**，`test-results/P4-PHOTO-SMART-COLLECTION-UX-FIX-frontend-r11.xml`。命令：

```powershell
$env:UV_CACHE_DIR='.uv-cache'
uv run pytest -q tests/unit/test_smart_collection_frontend.py tests/unit/test_photo_smart_collections.py tests/integration/test_p4_photo_smart_collections.py tests/unit/test_photo_collections.py tests/integration/test_p4_photo_collections.py --junitxml=test-results/P4-PHOTO-SMART-COLLECTION-UX-FIX-affected-r12.xml --basetemp=.pytest-tmp-smart-collection-ux-r12
uv run ruff check tests/unit/test_smart_collection_frontend.py
node --check src/photo_search/web/app.js
node --check src/photo_search/web/scroll-pager.js
git diff --check
```

智能合集命名跟进的定向命令：`uv run pytest -q tests/unit/test_search_text_mode_ui.py --junitxml=test-results/P4-PHOTO-SMART-COLLECTION-UX-FIX-name-focused-r3.xml --basetemp=.pytest-tmp-smart-name-focused-r3`（退出码 0，1 passed）；`node --check src/photo_search/web/search.js`、`uv run ruff check tests/unit/test_search_text_mode_ui.py`、`git diff --check` 均通过。扩展命令 `uv run pytest -q tests/unit/test_search_text_mode_ui.py tests/unit/test_photo_view_info_ui.py --junitxml=test-results/P4-PHOTO-SMART-COLLECTION-UX-FIX-name-r1.xml --basetemp=.pytest-tmp-smart-name-r1` 退出码 1（5 passed、2 failed）：一项共享查看器探针因测试桩缺 `document.createElementNS` 失败；另一项共享查看器资源版本断言先在未修改页面的既有版本预期处失败。失败 XML 保留；未扩大为共享查看器修复。内置浏览器在隔离数据中运行文字搜索 `p520` 并打开保存对话框，确认名称输入值为 `p520` 后取消，未提交写操作。Luna High 对生产改动和首轮交互测试只读复核无 blocker；80 码点边界断言追加后的跟进复核已完成，无新 finding。

刷新生命周期补修最终定向命令：`uv run pytest -q tests/unit/test_smart_collection_frontend.py --junitxml=test-results/P4-PHOTO-SMART-COLLECTION-UX-FIX-refresh-r10.xml --basetemp=.pytest-tmp-smart-refresh-r10`（退出码 0，5 passed、0 failed/errors/skipped）。测试覆盖 `RUNNING→FAILED` 保留旧照片且不提示新结果、确认 build_id 改变后提示发布、未知终态不误报、60 次轮询网络错误后的清理、视图切换期间观察者与自动 POST 幂等、真实合集重入重置自动刷新预算；另覆盖首屏自动刷新 POST 在 `state.photoSmart.collection` 尚空时仍启动观察，以及新 build 首屏失败时旧卡片仍可打开、旧 snapshot 与 stale guard 保持，后续成功首屏才替换并清除重载保护。r8 有 1 个断言失败（测试未先建立 stale guard），r9 虽通过但独立复核发现赋值时机未覆盖失败路径；调整到请求前后由 r10 通过。r7/r8/r9 XML 原样保留。`node --check src/photo_search/web/app.js`、`uv run ruff check tests/unit/test_smart_collection_frontend.py`、`git diff --check` 均通过。隔离浏览器（合成 FILTER 合集）打开详情、切结果/排除、返回目录再进入均正常；此夹具没有 TEXT/COMBINED 有效模型，未在浏览器直接触发排名刷新成功或失败。Luna High 确认旧卡片身份 finding 关闭，并确认首次进入 POST 竞态修复无新增产品 finding；最后指出的 stale guard 测试缺口已修正，跟进确认已关闭。

此前范围命令的所有最终命令退出码为 0。早期 `round1` 与 `round2` JUnit 仍保留：前端 harness 初版断言/状态 stub 不足，另一个未修改的 scroll 测试 harness 缺少 `window.addEventListener`/status DOM；`frontend-r9/r10` 是新增 pager 回退测试脚本未等待异步 retry 完成，调整后 `frontend-r11` 与最终受影响组合 `r12` 均通过。未触碰未修改 scroll 测试的生产代码或断言。

## 浏览器与合成数据

Codex 内置浏览器通过新鲜一次性本地会话访问 `127.0.0.1`，使用隔离 FILTER 合集。合成源目录有 **521 个 JPEG 文件**；重新编码为唯一图案并从图库管理界面同步后，SQLite 有 **521 个 file_locations、854 个唯一 assets**。在约 466×740 的窄视口确认：智能目录不显示普通合集创建表单；NO_CACHE 不误报候选数 0；FILTER 选择拍摄时间；横/竖图占位框规整；viewer 查看并关闭返回；滚动追加 50 张分页并持续上滚检查已加载卡片；排除两项并逐项恢复，最后恢复至任务开始时已有的单项排除状态。随后恢复该项，数据库排除表为空。继续验收时将浏览器 viewport 设为 1280×900，在隔离 PhotoSearchData 内临时创建两张同规则卡片，观察到目录为三列且卡片宽度受限；把一张临时卡片名设为 80 字并在宽/窄视口检查，标题以省略号截断、窄屏单列且无水平溢出。随后用服务函数删除两张临时卡片，确认合成库恢复为原有 1 个智能合集，并重置浏览器 viewport。

未导出浏览器截图：当前 UI 工具只返回截图视图，不提供本地文件导出。合成目录及 PhotoSearchData 位于 `.tmp/P4-PHOTO-SMART-COLLECTION-UX-FIX-20261020/`，文件合计约 **12.9 MB**；内容为本任务可重建的 521 张合成 JPEG 与隔离 catalog/cache，目的为分页、缩略图和滚动验收。夹具保留在 ignored `.tmp` 供复测，不影响安装目录、真实照片或用户数据。

## 未关闭验收

状态保持 **ACCEPTANCE PARTIAL**。受浏览器能力与夹具条件限制，以下项仍待验：

- 已补验目录响应式布局：1280×900、3 个合成卡片时显示三列；466×740 时显示单列；80 字合集名在两种宽度均截断且不产生横向溢出。测试卡片已从隔离库清理。
- 未构造 CR2 内嵌预览或离线源/缩略图失败场景。
- 浏览器级网络失败重试与无 IntersectionObserver 回退未直接执行；刷新运行/失败/新 build/未知状态与切换清理由可控前端测试覆盖，但浏览器没有可用的 TEXT/COMBINED 运行模型直接执行刷新成功/失败流程。
- 已检查 500 项以上的滚动和上回看，没有明显卡顿；没有记录活动 DOM 精确数或图片请求风暴计数，不作资源容量保证。
- Luna High 独立有界复核与新增测试跟进完成，均无阻断 finding；确认 epoch 重置后的互斥锁释放及回归测试，并确认同规则刷新幂等、503 失败保留卡片、无 IntersectionObserver 手动重试均有覆盖。复核提示“请求失败且 epoch 同时变化”没有单独用例覆盖，虽共用同一收尾链，仍记作未单独验证的分支。

未运行全仓回归、GPU/模型、真实照片库、30 万数据或正式性能基准；这些不作为本卡的声称证据。

## 最终补修与增量复核（2026-10-20）

本轮基于用户指定的 `99bc2042395c0b75a998f33e10668bc3891eb1e0` 核对；执行时 HEAD 为其后续提交 `01b151c`。仅新增 `app.js` 与智能合集前端定向测试差异。切换标签/排序时清空旧卡、释放分页锁并使用 view epoch/request version 拒绝迟到结果；失败提供重试。仅同视图、排序及快照失败首屏重读保留旧卡及其点击身份；已知新 build 或返回身份变化则不显示旧卡。每次刷新状态 GET 局部 8 秒 timeout 并尽力 abort，timeout 计入既有 60 次预算；停止、离页和超限路径清理观察 timer/请求状态并恢复按钮。全局 `request` 只透传显式传入的 `AbortSignal`，现有调用未改变。

增量命令：

```powershell
uv run pytest -q tests/unit/test_smart_collection_frontend.py --junitxml=test-results/P4-PHOTO-SMART-COLLECTION-UX-FIX-closeout-r2.xml --basetemp=.pytest-tmp-smart-closeout-r2
uv run ruff check tests/unit/test_smart_collection_frontend.py
node --check src/photo_search/web/app.js
git diff --check
```

真实结果：JUnit **6 passed / 0 failed / 0 errors / 0 skipped**，退出码 0；Ruff、Node 语法、diff-check 各退出码 0。JUnit 覆盖标签切换两方向失败、待完成标签/排序请求迟到、切换时解除锁、同快照失败保留旧照片并可操作、新快照失败清理旧卡、8 秒状态请求超时/AbortController、连续 60 次超时后观察状态与刷新按钮清理、停止观察后迟到成功响应不更新页面。原受影响组合 `affected-r12.xml`（77/77）复用为未修改服务端/列表合同的既有证据，本轮称增量定向验证，不称全套重跑。

内置隔离浏览器使用一次性本地会话与当前任务合成 FILTER 库：打开合集，确认结果/排除标签切换后各自显示正确内容或空状态，再切回结果确认列表恢复。由于合成库没有有效 TEXT/COMBINED 模型，没有在浏览器直接注入网络故障或执行排名刷新成功/失败。CR2/离线缩略图、真实 TEXT/COMBINED 刷新流程依然 PENDING；未访问真实图库、未做 GPU/模型、全仓、30 万图库或正式性能认证。独立复核：Luna High 最终有界复核无 blocker/non-blocker finding，确认结果与测试证据一致。当前仍为 **IMPLEMENTED / ACCEPTANCE PARTIAL**。
