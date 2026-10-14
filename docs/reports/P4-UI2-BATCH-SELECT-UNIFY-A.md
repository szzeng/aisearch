# P4-UI2-BATCH-SELECT-UNIFY-A 执行报告

日期：2026-10-14
结论：**SCOPED PASS（代码与定向行为）**；`BROWSER_NOT_RUN_USER_REQUEST`。

地点建议与人物合集成员两个现有批量区域已补齐统一勾选行为。地点使用 `asset_id` 状态，按当前已加载行的 DOM 顺序 Shift 连选；全选、反选与清空只触及已加载行。手工更改选择会解除冻结快照提示；重新加载或建议 revision 失效时清除锚点和旧选集，快照和既有 500 条分批写入协议未改变。

合集成员的文本选择按钮已替换为带 `aria-label` 的原生 checkbox，人物详情仍通过独立卡片按钮打开。Shift 范围以 `person_id` 为单位，保留全选/反选/清空和一次原子移出入口；写入或 pending write 时禁止更改选择。地点和合集选择均只改本地状态，不调用 API。

## 选择域和只读入口

- 地点建议列表：`asset_id` checkbox、已加载 Shift、全选/反选/清空；冻结全部建议快照仍是单独命令。
- 人物合集成员：`person_id` checkbox、已加载 Shift、全选/反选/清空；单击独立人物按钮仍打开人物相册。
- 保留并回归：`persons.js` 逐脸相册选择；`persons-albums.js` 人物列表、待整理人脸、已忽略人脸和候选组选择。没有重写其 Set、revision、写入或撤销逻辑。
- `READ_ONLY_NO_BATCH_ACTION`：`app.js` 首页时间轴、`search.js` 搜索结果、`photo-timeline.js` 的人物/合集/地点时间轴、`map.js` 地图视口与聚合照片列表。地点相册 `places-albums.js` 仍是单张更正，不是批量入口。未发现需要纳入本卡的其他现有照片批量写入口。

## 检查结果

- `SHIFT_DOM=PASS`：合成 DOM 覆盖 10 条地点建议的第 2 至第 6 项 Shift 范围、单项取消、全选/反选/清空；覆盖 8 名人物的 checkbox、Shift 范围、全选/反选/清空、Enter、busy 防护。
- `WRITE_SAFETY=PASS（合成 DOM）`：地点选择未发请求；合集选择阶段没有额外请求；已有移出写入口和请求语义未改。
- `PERSON_REGRESSION=PASS`：既有逐脸、人物列表、待整理候选组、跨已加载项、超 500 整次拒绝及 Escape 相关选择场景通过。
- `SELECTION_STYLE=PASS（静态）`：地点和人物选择样式分别限制在 `#suggested-list`、合集成员卡片；checkbox 不覆盖头像或文件名。按用户要求未做浏览器目视检查。
- `BROWSER_NOT_RUN_USER_REQUEST`：无浏览器交互、截图或视觉 PASS 声明。

验证命令：

```powershell
$env:UV_CACHE_DIR='.uv-cache'
$env:PYTHONPATH='.'
uv run --no-sync pytest -q `
  tests/unit/test_p4_ui2_batch_select_unify.py `
  tests/unit/test_person_ux_simplify_ui.py `
  tests/unit/test_person_loaded_pages_ui.py `
  tests/unit/test_p4_person_collection_ui.py `
  tests/unit/test_person_small_fixes_ui.py `
  tests/unit/test_p4_person_collection_count_ui.py `
  tests/unit/test_photo_view_info_ui.py `
  tests/unit/test_p4_place_cn_ui.py `
  --basetemp=.pytest-tmp/P4-UI2-BATCH-SELECT-UNIFY-A `
  --junitxml=test-results/P4-UI2-BATCH-SELECT-UNIFY-A.xml --tb=short
node --check src/photo_search/web/places.js
node --check src/photo_search/web/persons-albums.js
git diff --check
```

结果：**23 passed / 0 failed / 0 errors / 0 skipped**。JUnit：`test-results/P4-UI2-BATCH-SELECT-UNIFY-A.xml`。两份 JS 语法检查及 `git diff --check` 均通过。未跑全仓、模型/GPU、真实图库、性能或打包检查；没有修改后端/API/schema。

任务卡标注的基准 HEAD 是 `79db38a`；开工时本地 master 为 `64cb71a`。任务期间另一个时间轴控件任务提交使 HEAD 前进到 `8112c7b`；本任务未修改其文件。
