# P4-TEST-BASELINE-TAIL-A — 地点测试基线最小收尾

日期：2026-10-11。状态：**READY / 待实施**。立卡基线 `4b8d66e`（开工重核最新 master、工作区）；推荐 **GPT-6 Medium**。个人相册，**只做必要定向测试**，不开展全仓测试修绿工程。

关联：[四阶段总控](P4-TEST-BASELINE-CONTINUE-A.md)、[PLACE 残余报告](../reports/P4-TEST-BASELINE-PLACE-RESIDUAL-A.md)、[CORE 报告](../reports/P4-TEST-BASELINE-CORE-A.md)。四阶段定向各自通过，但 PLACE/CORE 为 PARTIAL；GC-A 全量历史结果 2,338 项（70 failures / 43 errors / 49 skipped）**未重跑**，不是当前已证实的剩余数。本卡不改写其他任务验收状态。

## 1. 目标和明确边界

只收口三项与地点测试可信度直接相关的问题：

1. **修正合成地点父子关系**：`tests/p2_search_fixtures.py::install_source_places()` 当前 `北京`（`country_code=CN`）却以 `country-jp` 日本作为父级，属于与测试地理语义不符的 fixture。恢复北京→中国；原测试若依赖“不同国家地点”，用独立的**东京→日本**等明确合成项替代，更新直接调用方。杭州、西湖、同名杭州及祖先展开的原测试意义不得丢失。测试地理数据应使用自洽层级和 country_code，不能为过滤结果造假的父子关系。
2. **同名地点消歧测试适配当前协议**：`tests/integration/test_p4_place_cn.py::test_cn_same_name_requires_parent_choice` 旧断言读取 `needs_place_choice`，而报告记录新接口返回 `needs_scope_choice` / `scope_choices`。先阅读 `src/photo_search/api/routes.py::post_place_aware_search`、统一解析器及 `search.js`；确认这两个字段分别对应统一解析/旧地点解析的正常分支。若新响应契约成立，测试断言**两个候选的完整上级/身份不同**，选中某一 `intent_plan` 后重试可获得相应权威地点范围，且另一个不混入。若行为并不符合项目已批准语义，保存最小反例并记 PRODUCT_FIX_REQUIRED，**不在此卡改产品 API**。
3. **历史人工归属/退休保护**：在 `tests/unit/test_p2_place_directory.py` 或最近的直接测试中，用**明确标注的历史合成状态**构造旧版合法 USER/custom 归属，核对旧归属可读、必要取消/撤销、退休引用保护；并保留“当前无有效来源不能新 ASSIGN、且零写入”的反例。优先复用现有历史测试 helper 或固定旧迁移状态，避免另一套数据库协议实现。不得经当下被禁止的入口新造 USER/custom 分配；不能伪造有效来源资格或更改生产逻辑。

## 2. 修改原则

- 原则上只修改 `tests/` 的测试数据、定向用例和少量 helper，以及本卡报告/开发状态。**不修改 `src/`、正式 schema/migration、ADR、契约、依赖、索引、用户图库**。
- 不删除测试，不使用新增 `skip/xfail/deselect`、捕获异常吞失败或弱化断言换绿色。
- 若需正式行为变更，保留原失败 nodeid、请求/响应和最小合成复现，记录 `BLOCKED / PRODUCT_FIX_REQUIRED`，该分支停止；其他两项独立可继续。
- 禁止访问真实 `PhotoSearchData`、下载真实地名包、加载 GPU/ONNX 模型或重建真实图库。

## 3. 个人项目最小测试与验收

1. **T1 合成父级**：一项北京归中国、中国祖先包含北京的测试；一项东京归日本且不混入中国的测试；原有同名/组合查询受修改直接测试继续成立。
2. **T2 双路径消歧**：复跑 `test_cn_same_name_requires_parent_choice`，检查候选父级/身份不相同，并至少验证一次选择后按权威范围返回；同时保留旧 `needs_place_choice` 分支的现有相关定向测试（不强求跑整个搜索模块）。
3. **T3 旧人工事实**：至少一个历史归属可读/受保护反例和一个当下非法新指定零写入反例；仅跑新增/修改节点与共用 fixture 的直接调用方。

运行修改的 Python 文件 Ruff、`python -m py_compile`、`git diff --check`；使用项目 `.pytest-tmp`、JUnit `test-results/P4-TEST-BASELINE-TAIL-A.xml`，记录退出码、tests/failures/errors/skipped。**不要求完整 unit/integration、UI 浏览器矩阵、OCR、GPU、30 万图库、打包**；如代表用例不通过，不写 PASS。

## 4. 交付和停止

简短报告 `docs/reports/P4-TEST-BASELINE-TAIL-A.md` 包含：实际变更文件、上述三项结论、最小复现/定向 JUnit、尚未核实内容、原全量结果仍是历史基线的说明。更新 `docs/development-status.md` 简要状态；仅提交本卡隔离改动。若需要产品修改只留下后续缺陷说明，不自动开新产品任务。

完成后结束这轮轻量测试基线整理，恢复日常相册开发/试用；OCR 原生运行时 `RUNTIME_UNCONFIRMED` 和未运行的新全量回归仍如实保留。**本卡 SCOPED PASS 不等于全仓 PASS**。
