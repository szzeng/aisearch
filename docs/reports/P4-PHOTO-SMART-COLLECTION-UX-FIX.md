# P4-PHOTO-SMART-COLLECTION-UX-FIX 执行报告

**日期：** 2026-10-20
**状态：** IMPLEMENTED / ACCEPTANCE PARTIAL
**边界：** 本地未提交；仅使用隔离 synthetic 数据，未访问真实图库；未改后端/schema/依赖。

## 目的与改动

修复智能合集目录与详情页视觉/状态问题，统一缩略图卡片，接入自动分页和可重试回退，并隔离目录、metadata、结果页、排除页与刷新观察的异步身份。FILTER 默认使用拍摄时间排序；目录类型与状态改为中文短文案，NO_CACHE 不再显示候选数 0。

改动文件：

- `src/photo_search/web/app.js`：分页单飞/独立 cursor、409 停止与重载入口、视图身份守卫、刷新观察、排除和恢复、缩略图路径及状态文案。排除成功重置 epoch 后曾使 `mutationInFlight` 永久保留；已修复释放逻辑并加入回归测试。
- `src/photo_search/web/index.html`：详情哨兵与重载控件、智能合集专用类名及资源版本号。
- `src/photo_search/web/styles.css`：仅作用于智能合集的卡片、缩略图框、焦点与窄屏布局；普通合集创建表单在智能目录隐藏。
- `tests/unit/test_smart_collection_frontend.py`：独立游标、图片 URL、状态文案、分页单飞/409 保留结果、迟到 metadata 丢弃及排除互斥锁释放的前端测试。

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

所有最终命令退出码为 0。早期 `round1` 与 `round2` JUnit 仍保留：前端 harness 初版断言/状态 stub 不足，另一个未修改的 scroll 测试 harness 缺少 `window.addEventListener`/status DOM；`frontend-r9/r10` 是新增 pager 回退测试脚本未等待异步 retry 完成，调整后 `frontend-r11` 与最终受影响组合 `r12` 均通过。未触碰未修改 scroll 测试的生产代码或断言。

## 浏览器与合成数据

Codex 内置浏览器通过新鲜一次性本地会话访问 `127.0.0.1`，使用隔离 FILTER 合集。合成源目录有 **521 个 JPEG 文件**；重新编码为唯一图案并从图库管理界面同步后，SQLite 有 **521 个 file_locations、854 个唯一 assets**。在约 466×740 的窄视口确认：智能目录不显示普通合集创建表单；NO_CACHE 不误报候选数 0；FILTER 选择拍摄时间；横/竖图占位框规整；viewer 查看并关闭返回；滚动追加 50 张分页并持续上滚检查已加载卡片；排除两项并逐项恢复，最后恢复至任务开始时已有的单项排除状态。随后恢复该项，数据库排除表为空。继续验收时将浏览器 viewport 设为 1280×900，在隔离 PhotoSearchData 内临时创建两张同规则卡片，观察到目录为三列且卡片宽度受限；把一张临时卡片名设为 80 字并在宽/窄视口检查，标题以省略号截断、窄屏单列且无水平溢出。随后用服务函数删除两张临时卡片，确认合成库恢复为原有 1 个智能合集，并重置浏览器 viewport。

未导出浏览器截图：当前 UI 工具只返回截图视图，不提供本地文件导出。合成目录及 PhotoSearchData 位于 `.tmp/P4-PHOTO-SMART-COLLECTION-UX-FIX-20261020/`，文件合计约 **12.9 MB**；内容为本任务可重建的 521 张合成 JPEG 与隔离 catalog/cache，目的为分页、缩略图和滚动验收。夹具保留在 ignored `.tmp` 供复测，不影响安装目录、真实照片或用户数据。

## 未关闭验收

状态保持 **ACCEPTANCE PARTIAL**。受浏览器能力与夹具条件限制，以下项仍待验：

- 已补验目录响应式布局：1280×900、3 个合成卡片时显示三列；466×740 时显示单列；80 字合集名在两种宽度均截断且不产生横向溢出。测试卡片已从隔离库清理。
- 未构造 CR2 内嵌预览或离线源/缩略图失败场景。
- 浏览器级网络失败重试与无 IntersectionObserver 回退、受控刷新延迟竞态未直接执行；相关有限状态由前端测试部分覆盖。
- 已检查 500 项以上的滚动和上回看，没有明显卡顿；没有记录活动 DOM 精确数或图片请求风暴计数，不作资源容量保证。
- Luna High 独立有界复核与新增测试跟进完成，均无阻断 finding；确认 epoch 重置后的互斥锁释放及回归测试，并确认同规则刷新幂等、503 失败保留卡片、无 IntersectionObserver 手动重试均有覆盖。复核提示“请求失败且 epoch 同时变化”没有单独用例覆盖，虽共用同一收尾链，仍记作未单独验证的分支。

未运行全仓回归、GPU/模型、真实照片库、30 万数据或正式性能基准；这些不作为本卡的声称证据。
