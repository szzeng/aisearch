# P4-PERSON-ORGANIZE-UX-CLEANUP 验收报告

日期：2026-10-16

实现基线：`b619252b7fe885844015f36ebe3b260fbee519db`；该源码对应 PR #5 合流前提交。上游 `origin/master` 当前为 `200c8911c999ec9c80b82e75a5f1409522303e99`，与实现基线之间仅更新了本任务卡。
验收结论：实现与定向代码验收通过；浏览器写入/恢复路径未闭合，故整体浏览器验收仍 PENDING。

## 交付内容

- 精简未识别人脸默认页；头像保留照片查看入口，移除重复“查看整张照片”按钮；低频整理、质量、历史和帮助操作归入默认折叠的“更多操作”。批次处理中及失败/重试状态仍可见。
- “已忽略”作为独立导航页，支持人物/人脸对象及 TEMPORARY/EXCLUDED 筛选。默认查询仅为 TEMPORARY；EXCLUDED 人脸用平铺列表。
- hidden flat/groups/group-detail API 支持受限的 `ignore_state`；无参旧接口语义保持兼容。筛选游标绑定状态、当前筛选修订和（组详情）组键；不匹配或数据筛选修订变化的游标返回 409。EXCLUDED 不允许走分组接口。
- 限制已忽略页刷新后的请求范围；仅未识别人脸页读取普通收件箱和补识别 capability。人物恢复、脸加入合集、从忽略脸创建人物均刷新当前 scope，不额外预载普通收件箱。隐藏分组代表脸读取改为当前页批量读取。

## 验证

受影响面覆盖前端状态和请求、API 路由与数据库查询、旧接口/游标兼容、跨状态/跨端点/跨组游标拒绝、身份归属变更后的游标失效、人物可见性与 undo，以及当前隐藏列表刷新。

```powershell
uv run --python 3.12.10 pytest -q tests/unit/test_person_inbox_hide_ui.py tests/unit/test_person_ux_simplify_ui.py tests/unit/test_person_loaded_pages_ui.py tests/unit/test_person_detail_batch_ui.py tests/unit/test_p4_person_collection_count_ui.py tests/unit/test_photo_view_info_ui.py tests/unit/test_p4_ui2_batch_select_unify.py tests/integration/test_p4_person_albums.py tests/integration/test_p4_person_ignore_state.py --junitxml=test-results/p4-person-organize-ux-cleanup-20261016-final-r3.xml --basetemp=.pytest-tmp/p4-person-organize-ux-cleanup-final-r3
node --check src/photo_search/web/persons-albums.js
uv run --python 3.12.10 ruff check src/photo_search/api/routes.py src/photo_search/persons/albums.py tests/integration/test_p4_person_albums.py tests/unit/test_person_inbox_hide_ui.py tests/unit/test_person_ux_simplify_ui.py tests/unit/test_person_loaded_pages_ui.py tests/unit/test_person_detail_batch_ui.py tests/unit/test_p4_person_collection_count_ui.py tests/unit/test_photo_view_info_ui.py tests/unit/test_p4_ui2_batch_select_unify.py
git diff --check
```

结果：**52 passed、0 failed、0 errors、0 skipped，pytest exit 0**；JUnit 为 `test-results/p4-person-organize-ux-cleanup-20261016-final-r3.xml`。`node --check`、受影响文件 Ruff 和 `git diff --check` 均通过。Luna High 独立复核发现并促成修复了状态游标修订绑定，以及忽略页刷新、恢复、加入合集和创建人物路径中的普通收件箱隐式读取；最终复核通过，无剩余 finding。

## 浏览器与未验边界

使用项目内隔离合成图库和 loopback 内置浏览器，确认未识别人脸默认页简洁、可进入已忽略页、TEMPORARY/EXCLUDED 状态切换可见。最后一次批量隐藏合成人脸操作时，浏览器自动化在输入派发阶段超时；没有将该次交互或写入/恢复路径计为 PASS，也没有保存截图（当前工具未提供截图导出）。因此批量隐藏、浏览器端恢复及对应的跨页面可见刷新仍 PENDING。未访问真实照片库，未做大规模性能、GPU、发行测试；没有因此触发全量回归条件。

前次合流遗留的 `P4-UPSTREAM-REVIEW-REMAINDER` 双页面冲突浏览器验收保持独立，本任务未关闭它。JUnit、临时测试数据和日志保留在忽略目录中，不纳入 Git。
