# Development status

更新：2026-10-11。当前规则：主架构需求修订 10、执行策略修订 12 / §9.4个人待审去重。较早逐项记录归档于[本次执行前快照](development-status-archive-2026-10-10-pre-P4-STORAGE-GC-A.md)、[2026-10-10 快照](development-status-archive-2026-10-10.md)、[2026-10-09 快照](development-status-archive-2026-10-09.md)及[2026-09-27 归档](development-status-archive-2026-09-27.md)。

## P4-PERSON-AVATAR-REUSE-A — A+B 定向验收通过（2026-10-11 修复列表渲染回归）；C 后置

[任务卡](tasks/P4-PERSON-AVATAR-REUSE-A.md)、[执行报告](reports/P4-PERSON-AVATAR-REUSE-A.md)。用户指定从 `f140e82` 执行此卡；60 秒内无变化返回复用列表 DOM，人物卡片刷新复用稳定头像 `<img>`，同模式重复点击不重载。2026-10-11 修复真实 DOM `HTMLCollection` 不支持 `.find()` 导致人物卡片渲染中断的问题，并更新脚本版本 URL 以避开旧缓存；Node 测试桩覆盖 DOM 差异并检查版本 URL。任务卡 Node 语法、两份定向 UI pytest（JUnit 2/0/0/0）和 `git diff --check` 通过。真实浏览器图片缓存/用户图库观察未做，C 后置；工作区变更未提交。原 `P4-TEST-BASELINE-LITE-A` 状态和其后续入口保持不变。

## P4-TEST-BASELINE-LITE-A — READY / 轻量测试基线整理（仅立卡）

[任务卡](tasks/P4-TEST-BASELINE-LITE-A.md)。针对 GC-A 全量 2,338 项中 70 failures、43 errors、49 skipped：优先核实并小范围修复地点 `PLACE_NOT_FOUND` 共享测试夹具，对其余失败按 nodeid/签名归类并区分既有、新回归、未知；只做定向验证，不强制一次修绿全仓。不修改生产代码、不放宽地点安全规则、不跳过失败；此前七阶段完整基线计划仍为 DEFERRED。本次仅提交计划，**未执行修复/测试**。本卡范围 SCOPED PASS 不能代替 GC-A 全量门禁或独立 DS-H 安全审查。

## P4-STORAGE-GC-A — IMPLEMENTED / 定向验收通过；GC-B 前置条件按路由例外满足

[任务卡](tasks/P4-STORAGE-GC-A.md)、[执行报告](reports/P4-STORAGE-GC-A.md)。GC-A 相关定向 JUnit 现存结果 **76/0/0/0**，未见直接受影响面新增回归。用户确认独立 GPT-6 Luna / High 复核通过；原 V3.1 路由要求 DS-H 全受影响面主审，本次按用户明确指示记录并接受路由例外。该结论**不是 DS-H PASS**。全量 unit/integration 既有 **2,338 项：2,176 passed、70 failures、43 errors、49 skipped** 原样保留，仍未通过，作为独立历史基线问题跟踪；本次未重跑或修改测试结果。未访问真实 PhotoSearchData、真实图库或用户服务；未改 Schema/依赖。

## 下一步

[P4-STORAGE-GC-B](tasks/P4-STORAGE-GC-B.md) 前置条件已满足，可开始实施。按卡片执行四组定向测试、Ruff 和最小 CLI 检查；不扩展为全仓回归。GC-A 全量历史失败仍单独跟踪，不宣称全量通过。
