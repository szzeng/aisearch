# P4-ORPHAN-ASSET-CLEANUP-FIX

状态：**IMPLEMENTED / SOL REVIEW PASS / BROWSER PENDING**。基线 `8f18a69`；未访问真实图库，未清理用户现场的 7,182 项。用户明确指定由独立 Sol 完成全受影响面复核。

## 原因和修复

- 搜索页原来默认及重置为 `any`。当前基线的搜索权威候选已要求存在登记图片位置；旧版本的现场现象不能直接当作当前版本实测。页面首次打开和重置改选 `present`，文字、语义及上传图片使用同一筛选；显式 `any`、`missing` 与 API 既有默认值保留。历史会话保留原筛选和游标绑定，从旧相似图会话切回文字或上传时恢复普通默认值。
- 来源注销删除位置但保留资产及向量；既有 MISSING 清理依赖可信观察和成功扫描，零位置资产因而无法获得该资格。新增独立 `ZERO_LOCATION` 人工预览、确认及持久 scope，不伪造扫描或观察时间，也不向自动清理开放。新迁移 0044 保存 scope 并增加有界查询索引；旧 run 默认为 MISSING。该迁移不改地点包契约，兼容桥经检查扩至 44。
- 按点击时身份快照、每批最多 20 项处理；执行前及最终删除时重新检查位置、任务、人工记录和未知引用，沿用屏障、DELETE_VECTOR 回执和持久重试。人工人物封面、人物操作及撤销历史、未知外键及级联引用均受保护；清理途中恢复位置或出现人工保护时撤销屏障并按既有协议安排向量修复。自动清理在最终阶段仍重新检查 30 天观察期和来源证据。
- 不可删除的既有地点解析记录、人工记录、未知引用、活动任务、恢复的位置及未确认的向量事件可能使资产保留；历史人脸快照太多时预览回退到精确检查。原图不在删除路径。来源暂时离线和 MISSING 继续遵守原边界。

主要实现：`src/photo_search/library_retention.py`、`src/photo_search/api/retention_runner.py`、`api/context.py`、`api/routes.py`、`catalog/migrations/0044_retention_run_scope.sql`、`geo/dataset.py`、`web/app.js` / `index.html`、`web/search.js` / `search.html`。新增回归覆盖搜索切换、人工保护、未知级联、恢复、迁移及向量重放。

## 验证

- 最终产品代码全量 JUnit `test-results/orphan-final-full2.xml`：**3,879 项，3,804 passed / 23 failed / 52 skipped / 0 errors，退出码 1**。与既有 `test-results/p4-build-stability-a2/final-full-r5.xml` 的 24 个失败节点比较，新增失败 0，1 个历史失败不再出现；不能称全量通过。此后仅增强了单个测试的关闭重开步骤，没有改产品代码；`orphan-review6.xml` 重跑索引与清理组合 **65 passed**。最终定向组合 `orphan-review5.xml` **169 passed / 0 failed / 0 skipped**。原有全量首轮 `orphan-full2.xml` 的 93 个失败保留，其中新增的 69 个节点在修复后已在 `orphan-r5.xml` / `orphan-r6.xml` 重验通过。
- 使用隔离合成库：部分语义建库（一个成功、一个模拟失败）→ 删除图库 → `present` 搜索不返回孤立项 → 人工确认清理两项；源文件大小和 mtime 未变化，清理过程未新增模拟编码。另验其他来源的 PRESENT 共用资产、收藏、人物封面与可撤销操作、未知级联表、不可删除地点解析以及活动任务的保护。
- 故障测试覆盖：应用实例关闭后恢复持久清理 scope；Lance 删除发生但 SQLite 回执未确认时关闭并重开 catalog，重复处理回执，确认旧 UPSERT 不复活；扫描证据失效并重新成功扫描后，自动清理不会跳过新的观察期。两类重启是隔离组件/应用实例测试，未模拟整台机器断电。
- 混合合成工作量：7,182 个零位置资产、500 张人脸、100 条人物历史、10,000 个任务、7,182 条历史批次行和 5,000 条向量索引/事件记录；本机一次预览 0.267 秒，20 项预检、建屏障和事件入队 0.066 秒，后者不含向量 drain 和最终删除。不是容量或延迟保证；大量人物历史下的逐脸精确回退仍未作规模测量。
- 按用户要求独立 Sol 对完整受影响面及最终修复复核，无剩余代码阻断项。Ruff、Python 编译、Node 语法与 `git diff --check` 通过。浏览器面板可打开，但当前会话的内置浏览器控制接口不可用；真实页面布局、窄窗口和确认/完成后交互 **PENDING**。不得将模拟测试当作用户图库已清理。

原始 JUnit、首次失败和运行日志保留在忽略的 `test-results/`，隔离夹具位于 `.tmp/`。复现：按 `docs/testing.md` 设置项目本地 `TEMP`、`TMP`、`UV_CACHE_DIR`、`PYTHONPATH` 与隔离 `PHOTO_SEARCH_DATA_ROOT`，再运行：

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/integration/test_p4_library_retention.py tests/integration/test_p2_index_submission.py tests/unit/test_catalog_migrations.py tests/unit/test_search_text_mode_ui.py tests/unit/test_p4_ui_search_entry.py tests/integration/test_p4_02y_i_source_build.py::test_partial_build_source_removal_then_confirmed_orphan_cleanup tests/integration/test_p4_02y_i_source_build.py::test_deleting_a_source_keeps_the_asset_shared_with_another_source tests/integration/test_place_dataset_schema17.py --junitxml=test-results/orphan-review5.xml --basetemp=.pytest-tmp/orphan-review5
```
