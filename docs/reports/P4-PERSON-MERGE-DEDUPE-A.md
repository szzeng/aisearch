# P4-PERSON-MERGE-DEDUPE-A 诊断与合成回归

日期：2026-10-09。实际基线：`3fda522`（任务卡记载的 `22dd79c` 已过期）。状态：**DIAGNOSED / 合成回归通过 / 现场待验证**。

## 结果

用户确认反馈指的是合并后相册里的大照片卡片重复。

新增的隔离合成回归建立人物 A、B，三张照片（共享合照 X、A 单人照、B 单人照），其中 X 含不同 `face_id` 分别属于 A 与 B；通过正式 `POST /api/v1/persons/merge` 执行 B → A。合并前两个人物各有 2 张照片。合并后逐页以 `limit=1` 读取目标 A：得到 X、A、B 共 3 个唯一 `asset_id`，响应计数为 3；旧 ID B 也解析到同一 canonical 人物和同样的 3 张照片。X 上原有两张有效脸都保留。正式撤销后 A 与 B 各恢复到原来的两张照片，四张脸关联均存在。

合成结果没有复现用户报告的大照片卡片重复，因此没有证据支持修改 SQL 或产品逻辑。当前前端把 `asset_id` 转成数字后，以 `state.photoIds` 跳过已渲染照片，并以请求序号丢弃旧响应；该检查只是源码路径，不代表真实浏览器复现。两张有效脸头像在同一照片卡片下属于预期成员数据，与用户确认的大照片卡片问题不同。

## 验证

- 定向集成：`$env:UV_CACHE_DIR='.uv-cache'; $env:PYTHONPATH='src;tests;.'; uv run --python 3.12.10 pytest -q tests/integration/test_p4_person_albums.py --junitxml=test-results/p4-person-merge-dedupe-a.xml`：**14 passed / 0 failed / 0 errors / 0 skipped**。
- 新增 `test_merge_shared_photo_is_unique_across_pages_and_undo_restores_membership` 覆盖正式合并、三页完整遍历、旧 ID 解析、共享合照双脸保留和正式撤销。
- Ruff（修改的集成测试文件）通过；`git diff --check` 通过。
- 第一次运行只因新增测试误从单张照片项读取人物摘要而失败；已将断言改为分页响应根部并完整重跑，最终 JUnit 为 14/0/0/0。

## 变更与范围

只新增 `tests/integration/test_p4_person_albums.py` 回归用例。本任务没有修改照片查询 SQL、合并/撤销语义、前端或数据库。工作区原有未跟踪 `.tmp/` 与 `docs/reports/tmp-*` 文件均保留。

## 待验证

合成 API 和当前去重代码路径未复现反馈。用户需在原个人相册重复 B → A 操作，确认是否仍出现重复大照片卡片；若复现，再记录重现步骤、响应分页的 `asset_id` 列表及页面卡片数，按任务卡定位后续最小修复。本次没有访问 `PhotoSearchData`、真实照片或用户现有服务，没有运行真实浏览器验收；因此本任务不标 PASS，也不生成后续任务卡。
