# P4-TEST-BASELINE-TAIL-A — 地点测试基线最小收尾

日期：2026-10-11。状态：**SCOPED PASS / 本卡三个尾项完成**。

## 结果

- **合成父级**：`install_source_places()` 现在将北京挂在中国下，并新增 country_code 为 JP 的东京作为日本子级。搜索 authority 用例核验中国祖先包括北京、不包括东京；中国筛选不带入东京，组合中国/日本后两个范围均可命中。acceptance 用东京替换原先误作跨国根地点的北京。原杭州/西湖祖先和同名查询断言仍在直接调用方中。
- **同名地点消歧**：`test_cn_same_name_requires_parent_choice` 核验统一意图解析的 `needs_scope_choice` 和两个不同完整父级/来源身份候选；分别提交其 `intent_plan` 后，各自只返回对应的合成 GPS 地点成员。未改 API 或 UI。检查发现仓库没有其他直接断言 `needs_place_choice` 的测试；其旧解析 fallback 和 `search.js` 展示分支保留，但本卡没有把它描述成已由运行时测试覆盖。
- **历史 USER/custom**：复用 `test_p2_dataset_migration.py::build_v4()` 的旧版迁移 fixture。升级后断言历史 USER/custom 人工归属可读，引用中的地点不能退休。现有 `test_manual_osm_district_assignment_uses_existing_lock_membership_undo_and_rejects_bypass` 验证当前非活动来源地点的单条/批量 ASSIGN 返回 `PLACE_NOT_FOUND` 且 assignment、history、operation、filter revision 零变化。
- 没有发现需要扩大为产品修复的问题。

## 验证

- 定向 pytest，JUnit：`test-results/P4-TEST-BASELINE-TAIL-A.xml`，**6 tests / 0 failures / 0 errors / 0 skipped**，退出码 0。覆盖共享 fixture 的 authority、acceptance、P3 人物搜索直接调用方，同名地点选择，旧 v4 状态及当前来源拒绝零写入。
- Ruff：5 个修改的 Python 文件通过。
- `uv run python -m py_compile`：5 个修改的 Python 文件通过。
- `git diff --check`：通过。

## 边界与剩余

- 仅修改 5 个测试/测试 fixture 文件；没有修改 `src/`、SQL、schema、正式 migration、依赖或用户数据。
- 未复跑完整 unit/integration 或全仓回归；GC-A 的 2,338 项（70 failures / 43 errors / 49 skipped）仍只是历史运行结果。OCR 原生运行时仍为 `RUNTIME_UNCONFIRMED`。没有访问真实 `PhotoSearchData`、照片库、下载真实地名包或加载 GPU/ONNX。
- 旧 `needs_place_choice` fallback 没有现成直接测试，本卡只检查其 API 分支和前端渲染代码仍存在；若今后需要冻结旧客户端兼容行为，应另开有明确请求场景的定向测试。
