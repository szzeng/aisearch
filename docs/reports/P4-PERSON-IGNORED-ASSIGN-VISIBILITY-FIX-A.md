# P4-PERSON-IGNORED-ASSIGN-VISIBILITY-FIX-A — 实施报告

日期：2026-10-15（Asia/Shanghai）
基线：`HEAD` 未变更，开始时工作树干净；本次未提交。

## 结果与根因

已忽略读取面此前只检查 `face_inbox_hidden`，没有排除已形成有效人物成员关系的人脸。因此，归属写入和目标人物相册可以正确成功，但这张脸仍作为待整理的已忽略记录返回。修复复用 `MEMBER_PREDICATE` 构成的 `_NO_MEMBER`，在平铺接口和隐藏分组 CTE 的数据源处先行排除已归属脸；候选照片数、组降级、组代表脸和组详情均从过滤后的集合计算。历史 `face_inbox_hidden` 标记保留；撤销归属后，记录重新符合无成员条件并回到已忽略列表。

“已忽略”查询现在表示带忽略标记且尚无有效成员归属的待整理人脸。此行为有意调整旧 `P4-PERSON-INBOX-HIDDEN-GROUP-A` 中“有隐藏标记即展示”的读语义；没有修改隐藏存储、权威归属写入、人工锁、撤销协议、schema 或索引。

前端在确认候选和手动归属写入成功后等待人物视图及已忽略视图刷新。顶层已忽略列表和展开候选组详情的 GET 均纳入刷新结果；旧请求由列表序号丢弃，隐藏组请求使用现有 `read_filter_revision` 游标绑定。刷新失败保留已保存的确认状态，并提示用户重试刷新而不要重复提交；失败的展开组可关闭后重开重试。

## 改动

- `src/photo_search/persons/albums.py`：新增统一隐藏待处理谓词，并用于三个隐藏读取路径及代表脸读取。
- `src/photo_search/web/persons-albums.js`：等待隐藏列表和重新展开分组完成，区分归属写入成功与视图刷新失败。
- `tests/integration/test_p4_person_albums.py`：新增隔离合成 API 用例，覆盖两照片候选组、合照中另一脸、无建议脸、目标相册成员、三个隐藏读路径、组降级、旧组游标 409、平铺 keyset 继续读取以及 undo 后恢复。
- `tests/unit/test_person_inbox_confirm_ui.py`：验证确认后等待已忽略详情刷新、组内 GET 失败提示和重开重试；同时校准原 DOM 断言以使用实际 `hiddenBatchMode` 控件状态。

## 验证

- `uv run --no-sync pytest -q tests/integration/test_p4_person_albums.py::test_ignored_assignment_visibility_group_cursor_and_undo tests/unit/test_person_inbox_confirm_ui.py --basetemp=.pytest-tmp/P4-PERSON-IGNORED-ASSIGN-VISIBILITY-FIX-A --junitxml=test-results/P4-PERSON-IGNORED-ASSIGN-VISIBILITY-FIX-A.xml`：2 passed / 0 failed / 0 errors / 0 skipped。JUnit：`test-results/P4-PERSON-IGNORED-ASSIGN-VISIBILITY-FIX-A.xml`。
- `uv run --no-sync ruff check src/photo_search/persons/albums.py tests/integration/test_p4_person_albums.py`：通过。
- `node --check src/photo_search/web/persons-albums.js`：通过。
- `git diff --check`：通过。
- 独立复核：GPT-6 Luna / high 聚焦复核。首轮发现展开候选组详情刷新失败未传递到保存成功后的刷新状态；已修复并加入故障注入/重试用例。修复轮确认原 finding 已关闭，本轮无新增 finding；审查范围限于刷新结果传播/重试及本任务定向 JUnit。

## 未验证与验收边界

按用户明确要求跳过浏览器验证，记录 `BROWSER_NOT_RUN_USER_REQUEST`。页面真实点击确认/手选后的视觉消失、F5 与重启后的真实浏览器观察未执行；DOM 测试不替代浏览器证据。未访问真实照片库，未运行全仓、GPU/模型、性能或打包验证。

因此代码和任务卡定向检查已完成，但整卡记录为 `IMPLEMENTED / ACCEPTANCE PARTIAL`，不标 PASS。
