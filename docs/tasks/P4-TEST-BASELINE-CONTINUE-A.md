# P4-TEST-BASELINE-CONTINUE-A — 轻量测试基线连续执行总控

日期：2026-10-11。状态：**READY / 仅计划，未实施**。立卡基线：`63628a7`，开工必须核对最新 HEAD、工作区及各卡现状。推荐：**GPT-6 Medium**，如遇需人工切换高推理或按 V3.1 必须独立审核的高风险分支，停在安全交接点。个人相册，**优先最小定向测试**。

## 1. 背景与准确基线

前一任务 [P4-TEST-BASELINE-LITE-A](P4-TEST-BASELINE-LITE-A.md) 报告：50 项定向 **48 passed / 2 failed**；曾有 43 个 `PLACE_NOT_FOUND` setup errors，相关修改后的定向集中 41 个执行通过、2 个进入独立断言失败；**没有复跑 2,338 项全仓测试**。全量 70 failures / 43 errors / 49 skipped 仍是 GC-A **历史运行结果**，不可当成当前剩余数量；52 个 failure 签名变化仍是 UNKNOWN。

已有 [旧七阶段基线方案](../plans/P4-TEST-BASELINE.md) 和 [旧地点专项](P4-TEST-BASELINE-PLACE-A.md) 继续 DEFERRED，不自动重启。GC-A/GC-B 自己的验收和路由结论由各自报告决定；本组不改写。

## 2. 本次用户明确授权的连续工作流

用户希望**同一个开发会话连续处理四张小卡**。这是针对本组的会话编排授权，**不修改 AGENTS.md 所述其他任务默认一任务一会话的规则**。

1. [LITE-B：收口已知两项](P4-TEST-BASELINE-LITE-B.md)；
2. [PLACE-RESIDUAL-A：地点来源旧用例及关联 API](P4-TEST-BASELINE-PLACE-RESIDUAL-A.md)；
3. [UI-A：前端静态/DOM 与人物计数](P4-TEST-BASELINE-UI-A.md)；
4. [CORE-A：Schema/迁移/人物 fixture/OCR 与其余小组](P4-TEST-BASELINE-CORE-A.md)。

每阶段独立：核对最新代码与报告 → 定位真实失败 → 修改最小的测试/fixture → 跑本阶段定向 JUnit＋必要语法/Ruff → 记录报告与开发状态 → **仅提交本阶段自身改动** → 下一阶段。工作区有其他任务改动时，不纳入提交；无法隔离时停止并交接。若前一阶段 PARTIAL 但后续互不依赖，可以带着明确遗留清单继续；**不把 PARTIAL 写成 PASS**。

## 3. 通用防误修规则

- 限于 `tests/`、测试 helper、报告和开发状态。**不得修改 `src/`、schema、migration、历史 ADR 或契约文件、依赖/锁文件/正式 UI 来让旧测试通过**。
- 凡需改生产逻辑、冻结身份/哈希契约、人工归属/UNDO/数据库恢复的情况：保存最小反例和证据，记录 `PRODUCT_FIX_REQUIRED / DECISION_REQUIRED`；该分支暂停，独立部分可以继续。高风险后续按 V3.1 审查和手动选模型，绝不声称自动切换。
- 不用 `skip/xfail/deselect`、吞异常、删测试、放宽断言或绕过来源验证来造绿。
- 不访问真实 `PhotoSearchData`、真实照片、用户服务、真实模型/GPU；仅使用隔离合成数据和现有小型 fixture。
- `pytest` 用项目本地 `.pytest-tmp` 与 JUnit XML 存证；运行工具遵守 `AGENTS.md` Windows 环境规则。共享 fixture 改动要跑其**直接调用方**，不要求全仓矩阵。
- 不自动启动额外产品任务，不试图在同一轮修复全部 113 个旧非通过项。

## 4. 验证与停机点

**每卡只要求：** 失败代表节点复现、修改文件/直接依赖的定向 pytest、相关静态检查（Ruff / py_compile / Node 仅在适用时）、`git diff --check`，保存退出码和 XML 摘要。不要求每卡全仓 `unit/integration`，不跑 GPU、浏览器矩阵、30 万图库或打包。

四卡结束后汇总四份报告、提交 SHA、已证实修复的 nodeid、未解决/未知清单，**默认不再跑完整 2,338 项**；如用户之后需要“新的全量剩余失败数”，再单独明确启动**一次**完整回归，不能沿用历史计数或说全量已 PASS。

## 5. 交付和启动指令

阶段报告分别写 `docs/reports/<任务ID>.md`；`docs/development-status.md` 只保留简短状态、当前阶段与链接。不得互相覆盖其他阶段报告。阶段被阻塞时记录原因并判断下阶段是否独立可继续；若发生高风险代码变更需求、环境无法隔离或必须切换模型，则停止当前执行并交接，等待用户手动安排。

**执行入口：按本总控和四张任务卡顺序开展；卡片当前均为计划，不表示测试已经运行。**
