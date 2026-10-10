# P4-TEST-BASELINE-LITE-B — 关闭已知两项人物搜索测试断言

日期：2026-10-11。状态：**READY / 未实施**。执行序号 1/4；依赖前一 [LITE-A 报告](../reports/P4-TEST-BASELINE-LITE-A.md)。推荐 **GPT-6 Medium**；由[连续总控](P4-TEST-BASELINE-CONTINUE-A.md)编排。

## 目标

收口上次 50 项定向中**2 个已明确失败**，只修有证据的旧测试预期，不修改正式产品代码或契约字节。

- `tests/integration/test_p3_person_search_filter.py::test_the_frozen_contracts_migrations_and_schema_version_are_untouched`：旧 `p2-contracts.v1.json` 哈希 `ec7de5f0…`，当前 `4270997c…`。先阅读 [P4-02Y-L Sol 已批准加性扩展](../reports/P4-02Y-L-sol-contract-freeze.md) 及其证据，区分**历史冻结版本**与**当前获批准的 structured_model 扩展**；历史字节校验以固定 Git 历史证据为准，当前校验使用经证据确认的固定字节哈希和必要的不变量。**不能动态计算当前文件哈希当 expected**，不能改 `docs/decisions/contracts/`。如审批证据不足，保留该项 BLOCKED 并记录。
- `...::test_the_person_list_page_still_counts_the_same_members`：C 只有 SUGGESTED 与 MANUAL_UNASSIGNED，`member_assets(C)=0`。先核对 `src/photo_search/persons/read.py::person_page` 和已批准列表语义：当前 `kind="all"` 仅展示已命名或有有效成员的人物；`kind="unnamed"` 可以含零成员未命名者。测试改为验证默认 all 不含 C、unnamed 中 C 的照片数为 0，A/B/D 仍与权威 `MEMBER_PREDICATE` 一致。不得为了旧断言改人物 API。

## 最小验证

1. 只复跑两个 nodeid 确认修复前后差异。
2. 跑 `tests/integration/test_p3_person_search_filter.py` **完整文件**（前轮 50 项中的直接影响组），并保留来源合法/拒绝关键回归；如修改 shared helper，再跑其直接调用方。
3. 修改文件 Ruff、py_compile、`git diff --check`；以本卡独立 `--junitxml=test-results/P4-TEST-BASELINE-LITE-B.xml` 记录 tests/failures/errors/skipped 和退出码。

不跑全仓/GPU/模型/真实图库。目标是相关文件和两个断言通过；如发现授权契约或 API 语义冲突，记录最小反例并停止该分支，**不要凭断言失败修改正式产品逻辑**。

## 交付

简短报告 `docs/reports/P4-TEST-BASELINE-LITE-B.md`：两个 nodeid 的处置依据、相关 JUnit 与未决项；更新 status，提交本卡隔离改动。完成或留下明确 PARTIAL 后，按总控进入 PLACE 卡。
