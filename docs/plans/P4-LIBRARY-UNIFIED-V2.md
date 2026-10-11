# 图库管理统一优化任务卡（V2）

核对代码基线：`szzeng/aisearch` `master @ 3b9d472f3ed6ae70a383d3c826c0c3eb48fab94c`（2026-10-11）。开工重新核对 HEAD、并行任务、工作树。

按顺序执行：

1. **[P4-LIBRARY-OPS-A.md](../tasks/P4-LIBRARY-OPS-A.md)**（P1）：全部/单独建库与同步入口；修复 `source_ids max_length=64` 和全来源建库在运行期间不能排队的问题。推荐 GPT-6 Luna / Medium，队列变动局部用 High 复核。
2. **[P4-LIBRARY-SYNC-B.md](../tasks/P4-LIBRARY-SYNC-B.md)**（P2）：只复用已有 `RefreshStatus.sources/counters` 做变动摘要、来源失败提示；不再与 A 重复改建库入口。推荐 GPT-6 Luna / Medium。
3. **[P4-LIBRARY-RETENTION-C.md](../tasks/P4-LIBRARY-RETENTION-C.md)**（P3，C1）：只读失效照片分级与受保护记录预览；没有可靠首次失效日期的旧数据不得伪装满足30天。默认不迁移 schema、不删除业务数据。推荐 GPT-6 High。

**C2 的逻辑删除与物理回收需未来单独授权**。所有任务按个人项目最少必要合成验证，不访问真实照片或 `PhotoSearchData`，不跑全量回归、30万性能、真实模型/GPU。原图零写入。A/B 可同会话按顺序完成，C 独立开发与定点审查。

这是优化后的任务文档包，**不是已经实施/通过测试，也尚未提交 GitHub**。