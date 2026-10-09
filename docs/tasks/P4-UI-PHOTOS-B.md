# P4-UI-PHOTOS-B — 照片浏览与图库管理层级

日期：2026-10-10。状态：**PLANNED / 未实施**。规划收口 HEAD `c19749a`；实施前要求 [A](P4-UI-SHELL-A.md) 已验收，执行时检查实际 Git/报告。

推荐 GPT-6.1 Sol / High；A 级前端展示小改，独立 DS-H 核对全部受影响 UI 与原管理动作接线。

## 视觉参考（执行前必须看图）

打开[照片页模拟图](../mockups/P4-UI-PERSONAL-photos.png)与[图库管理模拟图](../mockups/P4-UI-PERSONAL-management.png)，再查看[HTML草图](../mockups/P4-UI-PERSONAL.html#photos)的照片/管理区域及[参考规则](../mockups/README.md)。实际看图后实施网格和主次操作；示例数字/图形不照搬。报告保存对应产品合成截图，说明重要偏离；不为靠近画面改变任务状态/业务接口。

## 现状盘点（备份参考）

[已有页面信息盘点](../plans/P4-UI-INFORMATION-CHECKLIST.md)供查阅和设计取舍，不要求119项全部保留、逐项汇报或逐项验收。用户此前明确强调必须增加/保留的项目，按盘点第0节的需求证据落实；其余展示可按方便、清新、少改核心接口的目标精简或调整。图中未展示不自动等于必须新增。此次纠正优先于本卡对参考信息作出的泛化“全部保留”解读；业务/人工状态及接口正确性边界不变。

## 读取与范围

AGENTS → status → 本卡 → [总计划](../plans/P4-UI-PERSONAL.md)§4照片/§6/§8；架构§5.2、§6.1、§10.1～10.3；策略§2.2～2.4、§3.2～3.4。直接读 `web/index.html/app.js/styles.css`、来源/浏览 API 参数及原测试，不重读全仓。

允许上述三个前端文件局部展示改动、必要定向 UI 测试。禁止业务 Python、API、schema、扫描/任务状态派生逻辑、依赖与模型变化。

## 唯一闭环与要求

- 照片网格为主；图库/排序/筛选一条工具栏；长技术说明进入展开区。
- 保留 `time_desc/capture_desc/asset_id` 的准确语义及当前游标；「最近加入」不能改成拍摄时间。高级排序/日期条件折叠但仍能使用，结束时间仍准确标为不含；本卡不改日期序列化。
- 原有缩略图点击打开、load more、来源筛选、滚动返回都保持。不做新时间轴/瀑布流，不从当前页计算全库总量。
- 管理视图来源卡突出看照片、开始建库/刷新；改名次级、删除/完整校验扫描收进更多。保留原 ID 与处理函数，仅重排展示；准确说明移除记录不会删除照片。
- 没有来源、未建库、无符合筛选的照片、获取失败分别显示；状态只来自真实响应。不把 `0/0` 当完成，不以布局变更重启、重算或清空任务。
- 已保存的 filter/cursor/returnContext 不因折叠工具或进入管理而丢失；失败不清表单和选择。共享按钮样式沿用 A。

## 必须验证

相关原测试：`tests/unit/test_photo_view_info_ui.py`、`tests/integration/test_api_sources_assets.py`、`tests/unit/test_api_source_rename_ui.py`；必要新增 `tests/unit/test_p4_ui_photos.py` 用行为覆盖三种空状态、来源筛选、两页返回、原管理按钮请求不变。

```powershell
$env:UV_CACHE_DIR = '.uv-cache'
uv run --no-sync pytest -q tests/unit/test_p4_ui_photos.py tests/unit/test_photo_view_info_ui.py tests/unit/test_api_source_rename_ui.py tests/integration/test_api_sources_assets.py --basetemp=.pytest-tmp/P4-UI-PHOTOS-B --junitxml=test-results/P4-UI-PHOTOS-B.xml
node --check src/photo_search/web/app.js
uv run --no-sync ruff check tests/unit/test_p4_ui_photos.py
git diff --check
```

隔离小型合成浏览器验来源切换、两页照片、详情/返回、空筛选、改名错误及删除提示；管理写操作只针对合成来源，核对与旧按钮相同 payload/CSRF。不能只截图替代事件验证。不运行全仓/真实图库/模型/打包；若出现业务变化按总计划§8升 B/D 另卡，不能用本 A 门槛通过。

独立 DS-H 完整覆盖新增 DOM/事件到原 API 的直接链路，核对原请求/任务状态映射与授权，无新高风险未决则 SOL_NOT_REQUIRED。报告 `docs/reports/P4-UI-PHOTOS-B.md`，记录未验范围；完成本卡停止，下一候选 [C](P4-UI-SEARCH-C.md)。
