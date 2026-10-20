# P4-LIBRARY-RETENTION-C2-E — 清理后 Artifact 与受管缓存回收

**状态**：IMPLEMENTED / ACCEPTANCE PARTIAL；父目标 `P4-LIBRARY-RETENTION-C2` 仍 IMPLEMENTED / ACCEPTANCE PARTIAL。
**基线**：承接 C2-A/B/C/D 当前工作树；不提交、不推送，不访问真实图库。

## 必读范围

- V3.0 §5.1–5.4、§6、§7、§10.2、§11.1–11.4、§12.4–12.5；V3.1 §2.4、§3.2/3.4、§4.2、§11。
- `P4-LIBRARY-RETENTION-C2-D.md` 与报告；`P4-STORAGE-GC-A.md` §2/4/5/6/7/8 和对应报告；`P4-STORAGE-GC-B.md` §2/3/5/8–9 中与物理版本边界有关的合同。
- 直接实现：`artifacts/store.py`、`storage.py`、`media/cache.py`、`media/status.py`、停机 data-root lease、storage CLI/维护流程、Artifact/Job 引用关系、C2 清理回执与其测试。

## 目标与冻结语义

1. 在 C2-D 已删除 asset/index 引用且 DELETE_VECTOR 已 CONFIRMED 后，安全释放该资产不再使用的 Artifact 元数据/文件，以及可确认资产专属的缩略图、预览和缓存；保持 ArtifactStore 现有生命周期、GC-A 的路径/身份/引用检查和停机互斥约束。
2. 从不得误删出发：只处理确切关联已完成清理的 asset、合法受控 data-root 路径和普通文件；保留期、元数据引用、未确认 outbox、活跃/可恢复 Job、其他资产引用或文件身份无法确认时必须跳过。不得按 `artifacts/pending` 目录名推断未发布。
3. 删除已清理资产遗留的 Job/Artifact 数据前，核验没有其他 asset、face、generation 或恢复路径引用。失败或中断后可重试，实际删除文件字节与逻辑删除记录分别计量。
4. 复用现有离线 `storage clean`、Artifact GC 和 data-root lease，不新增清理守护进程、不触发 Lance 全库压缩/SQLite VACUUM、不改动旧 migrations；不得删除任何原始照片文件。
5. 不让常规网页/扫描触发破坏性动作。若现有 GC API 无法安全用于 C2，补充有界的停机 CLI/服务入口或仅保留统计并说明缺口，不削弱旧保护规则。

## 性能设计

- 链路与风险：C2-D 后按资产追踪残留 Artifact 与派生缓存并清理文件；风险是全库引用集合、逐文件 N+1、与仍运行的发布者竞态。
- 做法与边界：复用 GC-A 有界候选/再校验/lease；最多 20 个资产逐批推进，路径只限受控目录，不递归遍历原图目录，不物化全库路径集合。
- 验证与停止：隔离合成 data-root 覆盖活跃引用、RECLAIMED/未确认 outbox、共享引用、路径逃逸/reparse、保留期和中断重试；身份或互斥无法证明时停止并跳过；不做大库性能认证。

## 必须验证与审查

- 合成 D→E 顺序验证：先完成 vector tombstone 确认与 asset 逻辑清理，再回收实际孤儿 Artifact；对仍有引用的 Artifact、活跃/未确认工作、RECLAIMED 记录和跨资产共享数据均不误删。
- 文件系统验收使用项目隔离目录，核对实际文件消失与 `st_size` 释放字节；不声称等于操作系统分配簇或总盘空间变化。注入文件身份变化、目录/文件 reparse、权限错误、删除中断，确认跳过与可重试。
- 复用 GC-A 停机 lease/锁/WAL 保障；没有显式 `--stopped --apply` 时必须零写。不得触及原始照片路径。
- 按风险运行受影响 pytest 并保存 JUnit XML、Ruff、Python 3.12 编译、CLI/API/Node（仅改到时）及 `git diff --check`；历史全量失败需原样记录，不冒称全量通过。
- 使用用户指定 Luna H 子代理对完整物理文件清理面只读复核；风险扩展时按 V3.1 升级审查范围。真实照片目录、GPU、模型、大库/发布验收均排除。

## 排除范围

- 不删原始照片，不做 SQLite VACUUM、Lance `cleanup_old_versions()` 或压缩，不删其他未通过 C2-D 清理的 asset；不做最终 UI、自动调度或人工记录解除授权（C2-F）。

## 实施结果与未完成验收

- schema 0040（支持版本 40）在删除 asset/face 行前持久保存媒体 SHA 和 face crop key，并分别记载媒体文件、Artifact/Job 元数据回收结果；不修改历史 migration。复用 GC-A 路径安全检查、引用复查、停机 data-root lease 和有界批次，新增 `storage clean-retained` 预览/显式停机执行入口。
- Artifact/Job 元数据清理只针对 C2 tombstone；`BLOCKED` 及其他活动工作阻断。任何未知表包含 `job_id` 或 `*_job_id` 列时保守阻断，发现其它 asset、face、generation、恢复/索引引用也跳过。Luna H 指出的未知无 FK job 引用和 BLOCKED 情况已加合成回归并跟进复核关闭。
- 媒体 unlink 先于该资产计数事务提交。若进程恰在 unlink 后、SQLite 记账前中断，重试无法复原已删除路径及其字节，故累计 `media_deleted_files/media_freed_bytes` 可能保守低估；当前只代表可提交调用所记账的 `st_size`，不满足跨崩溃精确累计。本卡不把该计量称为准确总回收量，精确持久计量仍待后续设计/验收。
- 最终受影响组合：`test-results/P4-LIBRARY-RETENTION-C2-E-final-r7-20261020.xml`，170 tests、0 failures、0 errors、16 skips；包含迁移新库/39→40/失败回滚、C2-D→E 清理、未知引用与 BLOCKED 回归。Ruff、Python 3.12 `py_compile`、`git diff --check` 均 exit 0。首次 review-fix 定向运行 r4 有 2 个失败（扫描器将 jobs 自身当成引用表、及由此影响的 composite GC），已修复后由 r6 定向组及 r7 全受影响组闭合。
- Luna H 只读跟进确认 blocker 修复，无新增 blocker；它没有运行测试。未做浏览器、真实图库、原图扫描、GPU、模型、大库或全仓验收；物理清理的具体界面、持久任务进度和 30 天自动调度留在 C2-F。
- 最终状态：本阶段实现完成，但跨崩溃精确累计计量未验收，因此为 **IMPLEMENTED / ACCEPTANCE PARTIAL**，不宣称整个 C2 已完成。
