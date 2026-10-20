# P4-LIBRARY-RETENTION-C2-E — 执行报告

**状态**：IMPLEMENTED / ACCEPTANCE PARTIAL（本阶段；父目标仍部分验收）
**日期**：2026-10-20
**任务卡**：[P4-LIBRARY-RETENTION-C2-E](../tasks/P4-LIBRARY-RETENTION-C2-E.md)

## 结果

新增 schema 0040（支持版本 40），在 C2-D 删除 asset/face 数据前保存 SHA 与人脸 crop key，使停机回收可以按精确身份删除受管缩略图、预览、失败缓存和 face crop。新增 `photo-search storage clean-retained` 预览/显式 `--stopped --apply` 执行入口，按最多 20 个墓碑资产推进；复用 data-root lease、storage 锁、WAL checkpoint、GC-A 路径和文件身份门禁。原始照片目录不在候选范围。

Artifact/Job 元数据回收只处理 C2 完成 tombstone，遇到恢复中的 `BLOCKED` job、未知 job-like schema、其他资产/索引/恢复引用或外键不完整时 fail closed。Luna H 指出的无 FK `future_job_recovery(job_id)` 悬空风险及 `BLOCKED` 状态遗漏已修复并加回归；跟进只读复核关闭 blocker，无新增 blocker。

## 验证

- 最终受影响组合 JUnit：`test-results/P4-LIBRARY-RETENTION-C2-E-final-r7-20261020.xml`，170 tests、0 failures、0 errors、16 skips。覆盖 C2-D→E 合成回收、路径身份/共享引用保护、未知 job 引用、BLOCKED 阻断，以及 schema 39→40、新库和失败迁移回滚。
- 较早 review-fix r4 曾有 2 项失败：扫描器将 `jobs` 表本身误认作未知引用表，导致正例和组合 GC 失败。跳过 `jobs` 本体后，定向 r6 与完整受影响 r7 组合均通过。
- Ruff、Python 3.12 `py_compile`、`git diff --check` 均通过。
- 独立审查为用户指定的有界 Luna H；审查者未运行测试。未做浏览器、真实图库、原图目录、GPU/模型、大库或全仓验收。

## 未完成验收与边界

媒体文件 unlink 先于 SQLite 字节计数提交。如果进程恰在 unlink 后、记账事务提交前中断，重试已看不到该文件，累计 `media_deleted_files/media_freed_bytes` 会漏掉此窗口中的文件。因此当前指标只覆盖成功提交调用可记账的文件数与 `st_size`，可能保守低估跨重启累计值，不能称为精确历史总回收量。逐文件持久进度账本和崩溃窗口恢复仍未实现；这项计量验收保持 pending，故本阶段标 **IMPLEMENTED / ACCEPTANCE PARTIAL**。

最终确认/进度 UI、用户手动提前清理、30 天自动触发与调度状态属于 C2-F，尚未验证。整个 C2 仍为 `IMPLEMENTED / ACCEPTANCE PARTIAL`；没有提交或推送。
