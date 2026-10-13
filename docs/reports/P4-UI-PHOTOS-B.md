# P4-UI-PHOTOS-B 实施与验证报告

日期：2026-10-14
结论：**FUNCTIONAL SCOPED PASS / BROWSER AND PERF_200K PENDING**。首页照片默认进入统一时间轴，保留列表与图库管理原操作；合成行为和定向 API/UI 测试通过，用户指定的 LUNA High 受影响面复核已完成且无剩余 findings。真实浏览器闭环、照片首屏可见时间与 CORE 的 `PERF_200K` 证据仍未完成，因此本卡不标记整卡 PASS。

用户明确要求先执行 P4-UI-PHOTOS-B，覆盖了任务卡所列 A/C/CORE 前置验收尚未关闭的现状。此决定允许开始本卡实现；不将前置任务、20 万性能、浏览器或审查状态改写为通过。

## 实施范围

- `src/photo_search/web/index.html`：照片页以“全部照片”时间轴为默认主视图，保留“列表与高级日期条件”；管理页增加操作层级说明。首页相关样式/脚本使用 PHOTOS-B 资源版本。
- `src/photo_search/web/app.js`：来源卡突出“看照片”和建库；时间轴不再硬编码拍摄时间，从全局偏好读取拍摄/入库时间与分组。区分空来源、未建库、年月无结果、未知/待就绪桶为空、来源读取失败等空状态；不覆盖请求错误。
- `src/photo_search/web/photo-timeline.js` / CSS：增加范围快捷入口和数量、全局偏好持久化、来源范围内年月/桶/锚点会话恢复；未知/待就绪标题标明加入图库日期。继续使用 CORE 游标和分页接口，没有改后端/API/扫描/模型/写协议。
- 定向测试覆盖偏好与来源隔离、年月筛选和直接定位参数、未知/待就绪入口、31 页回收窗口与跨页同日组头、来源变化后丢弃迟到响应、详情返回；现有来源改名/扫描 API 测试继续核对原写操作。

## 验证

任务卡命令，修复 LUNA High findings 后 JUnit：**28/0/0/0**。

```powershell
$env:UV_CACHE_DIR = '.uv-cache'
uv run --no-sync pytest -q tests/unit/test_p4_ui_photos.py tests/unit/test_photo_view_info_ui.py tests/unit/test_api_source_rename_ui.py tests/integration/test_api_sources_assets.py --basetemp=.pytest-tmp/P4-UI-PHOTOS-B --junitxml=test-results/P4-UI-PHOTOS-B.xml
```

其他检查：`node --check src/photo_search/web/app.js`、`node --check src/photo_search/web/photo-timeline.js`、`uv run --no-sync ruff check tests/unit/test_p4_ui_photos.py`、`git diff --check` 均通过。`tests/integration/test_p4_ui_timeline_integration.py` 关联验证 **1/0/0/0**。其余顺带运行的 `tests/unit/test_api_frontend.py` 有 7 项与本次首页时间轴/来源层级无关的旧人物/搜索断言失败（人物页面内容、导航、CSRF/API 词汇和搜索 URL 断言）；没有据此扩大本卡范围。

JUnit 文件：`test-results/P4-UI-PHOTOS-B.xml`。31 页检查使用合成元数据和少量模拟缩略图，仅证明组件活动页≤8、明细缓存≤10、锚点≤64、离屏占位合并；它不是 Chromium 浏览器证据或大图库性能测量。

## LUNA High 受影响面复核与修复

独立只读复核发现并推动修复 4 项 P2：

1. 时间轴详情返回原先直接清除 pending 标记，没有重开资产锚点；现已将返回 anchor/asset ID 传给组件，恢复时按当前页面滚动位置加卡片 viewport 偏差校正，兼容详情页将窗口滚至顶部。扩展详情返回合成测试模拟当前 scrollY=0、恢复卡片 top=620、原 top=20，断言最终恢复 600。
2. 时间依据、桶、应用年月和直接定位原先等摘要完成后才 reset；现改为先立即 reset/递增分页 generation，再单独请求摘要。新增桶切换时迟到旧照片页不能覆盖新范围的行为测试。
3. 摘要加载/失败原先会覆盖照片分页错误；现摘要状态与分页状态使用独立提示区，年份刷新摘要不清除分页重试。新增年份刷新时保留分页错误的行为测试。
4. revision 不一致曾把摘要错误写入分页状态，显示冲突版本计数且摘要重试不能刷新照片页；现无论摘要先返回还是照片页先返回，revision 冲突都设置摘要错误与重试入口、清除不可信计数。重试会重新载入当前锚点附近的照片页和摘要，版本一致后计数恢复。新增两种响应顺序及重试恢复行为测试。

LUNA High 最终只读受影响面复核完成：此前的滚动恢复、日期切换 generation、摘要/分页状态隔离及 revision mismatch 计数与重试 findings 均关闭；未发现剩余 P2 或阻塞项。本结论是用户指定的 LUNA High review，不是 DS-H review 或 PASS。

## 未完成验收门

- `BROWSER_PENDING`：没有本卡合成产品浏览器的点击/键盘/浅暗主题观察和保存截图；首图可见耗时、详情返回误差、真实浏览器滚动/资源节点曲线均未测。
- `PERF_200K_PENDING`：沿用 CORE 当前报告的待验状态；没有重建夹具、没有测量或声称 20 万性能达标。30 页 Node 合成结果不代替 CORE 的实际 API/浏览器性能证据。
- `REVIEW_COMPLETE`：LUNA High 受影响面复核完成，无剩余 findings；不是 DS-H 结论，也未自动切换主线程模型。
- A/C/CORE 的浏览器、性能和独立复核门仍按各自报告状态保留。没有访问或修改真实照片库。

未提交变更、未推送。完成本卡后停止；D 卡已存在，暂不启动，待上述验收门关闭后再按开发状态继续。
