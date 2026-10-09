# P4-PERSON-INBOX-HIDE-A 实现与定向验证

日期：2026-10-09。实际基线：8b78b3b6fd165280f4d7cc2ed23017c7f02eb208。结论：**IMPLEMENTED / 本卡定向验证通过 / 用户前端试用待完成**。仅覆盖隔离合成图库和人物页 DOM 行为。

## 实现

- 新增 schema 23 迁移 0023_face_inbox_hidden.sql，仅添加 face_inbox_hidden(face_id)，外键在原脸清理时级联删除。
- 待整理专用查询在分页、候选分组、展开人脸和普通统计前排除已忽略脸；候选组继续按不同 asset_id 计照片数。重检测 hold 统计保留原口径。其他人脸详情、人物归属、匹配与搜索查询未加隐藏过滤。
- 新增 POST /api/v1/faces/inbox/visibility 和分页 GET /api/v1/faces/inbox/hidden。批量写入仅接收 1–500 个互异合法 face_id，先在一个 SQLite 写事务中检查全批存在性和隐藏资格，再统一写入；重复隐藏/恢复幂等。恢复只要求脸和标记存在。
- 人物页复用原批量模式，人物与人脸使用独立 Set 和提交接口。候选合集标题仅展开/折叠，脸卡勾选按钮独立于头像预览；分页追加、收起/展开保留所选 ID。已忽略脸列表有独立分页与恢复选择。取消确认或接口失败不清空人脸选择；成功只清理刚提交的人脸选择并刷新待整理/已忽略列表。

## 变更文件

- src/photo_search/catalog/migrations/0023_face_inbox_hidden.sql
- src/photo_search/catalog/schema.py
- src/photo_search/persons/albums.py
- src/photo_search/api/models.py
- src/photo_search/api/routes.py
- src/photo_search/web/persons-albums.js
- src/photo_search/web/persons.html
- src/photo_search/web/persons.css
- tests/integration/test_p4_person_albums.py
- tests/unit/test_person_inbox_hide_ui.py

## 验证

- 后端定向集成：test_inbox_face_visibility_is_atomic_persistent_and_scoped_to_selected_faces，**1 passed**。覆盖同照多脸、候选组不同脸、按不同照片数分组、选中两张后其余脸与建议仍在、分组降级、summary 变化、隐藏分页、重开 SQLite 持久性、恢复、重复操作和未知 ID 整批回滚。
- 前端 Node DOM：test_inbox_face_selection_paging_preview_and_hide_restore_keep_explicit_ids，**1 passed**。覆盖候选组跨页勾选、头像预览不选脸、标题收起/展开不重载、确认取消和接口失败保留选择、隐藏请求只带明确 ID、已忽略分页与精确恢复。
- 任务卡指定定向 pytest：**2 passed / 0 failed / 0 errors / 0 skipped**；JUnit：test-results/p4-person-inbox-hide-a.junit.xml。
- 修改面 Ruff、node --check src/photo_search/web/persons-albums.js、git diff --check：通过。
- 未执行全仓/30万/GPU/模型/打包/浏览器矩阵；未启动用户服务、读取或修改 PhotoSearchData，未提交或推送。

## 现场交接

用户仍需在个人前端完成一次「整理一下 → 候选合集逐脸勾选 → 忽略 → 已忽略人脸 → 恢复」操作。本地测试验证了隔离 DOM/API 行为和 SQLite 重开持久性，不代表真实前端现场验收。