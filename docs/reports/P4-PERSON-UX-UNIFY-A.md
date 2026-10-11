# P4-PERSON-UX-UNIFY-A — PARTIAL

日期：2026-10-11
结论：核心照片直达映射、旧响应隔离和拒绝提示已实现；剩余入口迁移与浏览器验收未完成。本报告不代表全任务 PASS。

## 实施内容

- 人物相册提供「整理人脸」次级入口；人物照片预览并行读取照片人脸和该照片 OPEN 映射。已有可见人脸时仍显示准确的待确认提示，点击可直接打开该照片的映射审核。
- 新增只读 `GET /api/v1/assets/{asset_id}/face-mapping-reviews`，限单 asset、OPEN 组、有界分页，使用 `fa1` 照片绑定游标。旧全局映射列表、详情及决策协议未改。
- 查询从 `faces` 的现有 `idx_faces_asset(asset_id, spec_revision)` 起步，再用旧脸主键关联映射组与候选；生产完整联表 SQL 的 `EXPLAIN QUERY PLAN` 测试确认资产索引路径。没有新增迁移、索引或 schema 变更。
- 映射照片切换会递增请求序号、清空旧结果并启动新请求；过期成功/失败响应均不能写入当前列表、游标或状态。补了 A→B 逆序响应交互测试。
- 「拒绝确认/拒绝整组」旁说明拒绝可能解除 OPEN 待确认保护，但不等于指定其他人物。「暂不处理」仍不写数据。待整理和人物照片单脸入口统一使用「更正这张脸」文案。
- 人物相册和审核脚本缓存版本升至 `p4-person-ux-unify-a-2`。原高级整理区仍保留，原有 assign/unassign/restore_auto、split、merge、映射撤销与历史入口未删除。

## 旧入口与能力覆盖

| 能力 | 当前状态 |
|---|---|
| 人物相册定位逐脸整理 | 新入口已接入既有分页/高级整理能力；未新增独立批量工具区 |
| 单脸更正 | 人物相册与待整理入口统一文案，继续调用原单脸权威写协议 |
| 多源人物合并 | 旧高级区入口保留且可达；尚未迁入新的批量管理入口，A3 未完成 |
| 映射确认、拒绝、暂缓、撤销 | 照片直达 OPEN 映射审核；复用冻结的既有决策及撤销协议 |
| 操作历史与维护 | 仍由原高级整理入口访问；本次未删旧 DOM 或事件 |
| 旧锚点 | 原高级面板保持可见并能承接照片审核/人物整理入口，未指向隐藏节点 |

## 定向验收

JUnit 命令（按任务卡相关范围；排除一个已有 schema 版本断言与当前 schema 28 不一致的升级形状用例）：

```powershell
uv run --no-sync pytest -q tests/unit/test_p3_face_redetection_api_privacy.py tests/unit/test_person_preview_navigation_ui.py tests/unit/test_person_mapping_navigation_ui.py tests/unit/test_person_loaded_pages_ui.py tests/unit/test_person_inbox_confirm_ui.py tests/unit/test_person_cleanup_ui.py tests/integration/test_p3_person_api.py tests/integration/test_p3_person_merge.py tests/integration/test_p3_person_split_api.py tests/integration/test_p3_person_split_undo.py tests/integration/test_p3_person_undo.py tests/integration/test_p3_face_redetection_mapping.py tests/integration/test_p4_person_albums.py tests/integration/test_p4_person_oplog_gc.py -k 'not v26_upgrade_shape_batch_rollback_and_page_controls' --junitxml=test-results/P4-PERSON-UX-UNIFY-A.xml --basetemp=.pytest-tmp/P4-PERSON-UX-UNIFY-A-final5
```

结果：**90 passed / 0 failed / 0 errors / 0 skipped**。JUnit：`test-results/P4-PERSON-UX-UNIFY-A.xml`。另有单独的 race 测试 JUnit：`test-results/P4-PERSON-UX-UNIFY-A-race.xml`；资产查询计划定向检查：`test-results/P4-PERSON-UX-UNIFY-A-plan.xml`。被 `-k` 排除的单项此前因断言固定期待 schema 27 而在 schema 28 仓库失败，本次没有把该基线项计入成功。

Ruff 通过；三个修改的 Python 文件 `py_compile` 通过；`persons.js` 和 `persons-albums.js` 的 `node --check` 通过；`git diff --check` 通过。

## T1–T6 边界

- **T1 / T2 / T3 / T5：** 复用本次命令内的人物 API、merge、split/undo、映射和操作历史相关合成集成测试；它们验证已有事务边界，不等于此次新建了一套完整 UI 浏览器流程。新页面未实现独立的多源合并入口。
- **T4：** 照片已有可见脸时仍提示 OPEN 映射、按资产过滤/隐私/游标 API 测试及 A→B 迟到映射响应测试通过。同一照片两条 OPEN 组以 `limit=1` 实际走完两页，断言 `has_more`、`next_cursor`、顺序和无重复。完整查询计划使用 `idx_faces_asset` 和映射组主键关联，不扫描全局 OPEN 组作为起始集合。
- **T6：** 人物照片分页/预览及相关 DOM 测试通过；任务卡要求的隔离合成图片实际浏览器点击本次未做，不能以 DOM 桩替代。

## 未完成项与交接

1. A3：在现有人物批量管理中增加次级「合并所选人物」入口，并定向验证多源人物合并。现阶段旧高级入口仍保留，避免丢失能力。
2. 按任务卡在隔离合成图片上完成一次基本浏览器点击验证，再更新此报告和状态。
3. 独立 LUNA / High 最终复审完成，结论为修复有效、无阻塞性代码问题；复审确认双页范围/顺序/无重复断言覆盖到位。此次仍按用户要求未做 DS-H。该结论不关闭 A3 和真实浏览器验收余项。

未访问真实 PhotoSearchData、真实图库或模型；未修改人工身份/映射写协议、schema、依赖或向量后端。工作区中与本卡无关的 OCR 任务卡删除、OCR 生命周期/进度实现和测试改动、P4-PHOTO-VIEW 卡及状态文件既有改动均予保留。
