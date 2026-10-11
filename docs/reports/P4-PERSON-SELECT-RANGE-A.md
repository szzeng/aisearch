# P4-PERSON-SELECT-RANGE-A — 执行报告

**结果：SCOPED PASS（合成 DOM / 定向 UI 验收）**
**日期：2026-10-11**

## 实施

- 人物相册逐脸整理支持 Shift 连选、全选/反选当前已加载的人脸及清空跨页选择；保留 `face_id` 单脸选择、500 张上限、人物 revision 校验与原有写入入口。
- 人物批量列表与合集成员支持独立 Shift 锚点和已加载项目全选/反选；人物列表继续保存现有人物 snapshot。人物、脸、合集范围超过 500 时整次拒绝并保留已有选择。
- 待整理及已忽略脸使用分开的选择集合。普通列表和展开候选组使用各自范围锚点；闭合候选组、未加载成员不参与范围或全选。清空任一待整理工具栏时清除合并写操作涉及的两组已选脸，避免隐藏选择残留。
- 选择快捷操作仅修改 UI 集合和 snapshot，不发业务写请求。写入/可见性忙时禁用选择工具；列表刷新、筛选、人物/合集切换及候选组折叠会使范围锚点失效。更新 `persons.html` 静态资源版本，保留键盘单项选择和现有 API。

## 修改文件

- `src/photo_search/web/persons.js`
- `src/photo_search/web/persons-albums.js`
- `src/photo_search/web/persons.html`
- `tests/unit/person_ux_simplify_harness.cjs`
- `tests/unit/test_person_ux_simplify_ui.py`
- 更新三个相关 UI 测试中的页面脚本版本断言
- `docs/tasks/P4-PERSON-SELECT-RANGE-A.md`
- `docs/development-status.md`

## 验收

```powershell
$env:UV_CACHE_DIR='.uv-cache'
uv run --no-sync pytest -q tests/unit/test_person_ux_simplify_ui.py tests/unit/test_person_loaded_pages_ui.py tests/unit/test_p4_person_collection_count_ui.py tests/unit/test_photo_view_info_ui.py --basetemp=.pytest-tmp/P4-PERSON-SELECT-RANGE-A --junitxml=test-results/P4-PERSON-SELECT-RANGE-A.xml --tb=short
```

- 定向 pytest：**14 passed / 0 failed / 0 errors / 0 skipped**；结果见 `test-results/P4-PERSON-SELECT-RANGE-A.xml`。
- 合成 UI 场景覆盖相册两页范围、人脸选择全选/反选/清空、人物及 snapshot、候选组范围隔离、已忽略域隔离、合集成员、超过 500 的整次拒绝、忙时禁用及选择无业务写请求。
- `node --check src/photo_search/web/persons.js`、`node --check src/photo_search/web/persons-albums.js`、`node --check tests/unit/person_ux_simplify_harness.cjs` 通过。
- `git diff --check` 通过。

## 未验证

- 未做真实浏览器手动验收、真图库或完整端到端浏览器套件；未访问或修改 `PhotoSearchData`。
- 未运行全仓 pytest、GPU/模型、性能或打包验证；本卡未改后端 API、数据库或识别逻辑。
- 工作区开始时已有 `src/photo_search/geo/dataset.py` 未提交改动，已保留，未纳入本任务。
