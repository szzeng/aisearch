# P4-TEST-BASELINE-PLACE-RESIDUAL-A — 地点残余旧夹具与 API 最小整理

日期：2026-10-11。状态：**READY / 未实施**。执行序号 2/4；推荐 **GPT-6 Medium**，由[连续总控](P4-TEST-BASELINE-CONTINUE-A.md)编排。

## 目标与范围

以 LITE-A 已新增的 `tests/p2_search_fixtures.py::install_source_places()` 和正式 stage→validate→activate 微型合成来源包为起点，针对旧全量报告中**32 项地点输入失败、10 项 API/operation/迁移类差异及 1 项同名选择**，先证实哪些确实因旧 fixture 造成。**这些数字是历史分类，不能视为当前仍失败的确数**。

优先从 `tests/unit/test_p2_place_directory.py`、`tests/integration/test_p2_place_api.py`、`test_p2_acceptance_search.py`、`test_p2_search_api.py`、`test_place_package_startup_recovery.py`、`test_p4_place_cn.py` 中挑真实失败节点；具体范围以历史 JUnit nodeid 和新复现为准，不机械全文件批量改造。

## 修改规则

1. 选 3～5 个有代表性的旧失败先单独复跑、分类，优先解决共享的来源资格 fixture；确认其余调用方才扩展。测试使用活动、经过校验的合成源 place ID，不造假 place_id，不 mock 资格判定。
2. 只对测试目标是搜索、ASSIGN、批量、UNDO、幂等/备份等**与来源资格无关**的合法输入做 fixture 替换；保留 parent 范围、计数、权限、expected_revision 和错误码断言。
3. **历史 USER/custom 人工归属**与“当前新指定被拒绝”要分开：历史关系用明确旧版本合成状态，验证可读/撤销/恢复；新无来源 ASSIGN 要拒绝且零写入。
4. `test_p4_place_cn::test_cn_same_name_requires_parent_choice` 先核对现行 OSM 优先、同名父子规则和测试活动包；可能是旧 oracle，也可能是真实消歧缺陷。未经证据不得强行改 expected。
5. 任何来源包启动恢复、人工归属、UNDO 的真实产品缺陷保留反例并单列，不在这张测试卡修改 `src/` 或旧 ADR。

## 最小验证

- 每种确认的根因至少 1 个成功用例＋1 个拒绝/历史保护反例；选少量节点先快速复现。
- 只跑**实际修改的文件与复用 shared fixture 的直接调用方**，不强制全部地点测试或全仓。
- Ruff / py_compile（修改的 Python），`git diff --check`，本卡独立 JUnit `test-results/P4-TEST-BASELINE-PLACE-RESIDUAL-A.xml` 与退出码。
- 不跑真实地名库下载、模型、真实图库或 30 万照片。

## 完成 / 交付

只对本卡**实际修改范围**要求定向通过；未复现或未修复的历史节点列出 UNKNOWN/PENDING，不以“估算减少32项”写成果。输出 `docs/reports/P4-TEST-BASELINE-PLACE-RESIDUAL-A.md`、status 简短记录及独立提交；不自动将全量门禁标 PASS。按总控进入 UI 阶段。
