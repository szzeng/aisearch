# Development status

更新：2026-10-10。当前规则：主架构需求修订 10、执行策略修订 12 / §9.4个人待审去重。较早逐项记录归档于[本次执行前快照](development-status-archive-2026-10-10-pre-P4-STORAGE-GC-A.md)、[2026-10-10 快照](development-status-archive-2026-10-10.md)、[2026-10-09 快照](development-status-archive-2026-10-09.md)及[2026-09-27 归档](development-status-archive-2026-09-27.md)。

## P4-TEST-BASELINE-LITE-A — READY / 轻量测试基线整理（仅立卡）

[任务卡](tasks/P4-TEST-BASELINE-LITE-A.md)。针对 GC-A 全量 2,338 项中 70 failures、43 errors、49 skipped：优先核实并小范围修复地点 `PLACE_NOT_FOUND` 共享测试夹具，对其余失败按 nodeid/签名归类并区分既有、新回归、未知；只做定向验证，不强制一次修绿全仓。不修改生产代码、不放宽地点安全规则、不跳过失败；此前七阶段完整基线计划仍为 DEFERRED。本次仅提交计划，**未执行修复/测试**。本卡范围 SCOPED PASS 不能代替 GC-A 全量门禁或独立 DS-H 安全审查。

## P4-STORAGE-GC-A — IMPLEMENTED / P2 已修复；定向验证通过，全量回归仍失败

[任务卡](tasks/P4-STORAGE-GC-A.md)、[执行报告](reports/P4-STORAGE-GC-A.md)。只读存储概览与默认 dry-run 停机文件 GC 已实现。Artifact 正式/临时候选现要求目录与文件 processor ID 一致；本次当前会话修复复核确认两种错配均被拒绝，未重跑 pytest；现存相关 Artifact/媒体/索引/备份/API 定向 JUnit 为 76/0/0/0。D 级 unit/integration 共收集并运行 2,338 项：70 failures、43 errors、49 skipped；43 个 errors 是其他地点/人物搜索夹具触发 `PLACE_NOT_FOUND`，全量失败计数与此前记录的跨任务回归基线相同。Ruff、py_compile、Node、diff-check 通过。任务不标 PASS，独立 DS-H 复核仍待完成。未访问真实 PhotoSearchData、真实图库或用户服务；未改 Schema/依赖，未提交/推送。

## 下一步

可先按 [P4-TEST-BASELINE-LITE-A](tasks/P4-TEST-BASELINE-LITE-A.md) 对 D 级历史失败进行小范围夹具修复与清单分类；这不自动解除 GC-A 的 D 级门禁。随后仍须独立 DS-H 全受影响面工程复核。审核建议 DeepSeek V4.1 Flash / High，由用户在 Codex 手动切换；DS-H 若发现未决数据安全语义，再按 V3.1 决定是否 Sol high 聚焦终审。卡 B 暂不启动。
