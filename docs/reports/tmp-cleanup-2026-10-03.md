# tmp 清理执行记录（2026-10-03）

**完成并读回核验**：删除70个旧顶层文件夹、3个地点验证/空子目录、40个旧顶层日志/文本/lock文件，以及2份可从ZIP恢复的地点TXT，共115个明确目标，0失败。按盘点快照移除约49.69 GiB逻辑文件数据（另有少量子目录未计入该估算）。

D盘可用空间前后观测：**47.77 → 102.77 GiB，增加 55.00 GiB**。这是前后磁盘观测，不把逻辑文件大小当作实际回收空间。

删除之前已复制238份辅助材料至 `temp/tmp-cleanup-archive-2026-10-03/files`，合计1.695 MiB。源/副本SHA-256一致；清理后再次完整读回核验238份副本，全部一致。归档保留原tmp相对路径。旧数据库、索引、图片和大模型副本未归档，不能从这个小型归档恢复旧实验状态。

## 保留资源

以下9个顶层目录均存在，关键容量catalog、权重、环境解释器、测速catalog、地点manifest/spec/ZIP已检查存在；三份顶层诊断Python脚本仍在原路径。

- `p4-02y-l`
- `p4-02y-p`
- `p4-embedding-arch`
- `p4-person-ux`
- `p4-qwen-speed`
- `p402f-阶段0`
- `p402j-contention-close`
- `qwen-speed-env`
- `qwen-speed-model`

## 地点数据瘦身

删除 `raw`、`verify`、`mini-check` 子目录和 `raw-2026-09-15/{alternateNamesV2,cities500}.txt`。删除TXT之前，对应ZIP的CRC完整性和成员大小已检查。`asia-package`、spec、`mini-package`、原始ZIP、小型国家/语言表、官方对照快照和核对脚本保留。重建地名数据前先从ZIP恢复TXT。

## 操作与核验

用户明确授权“删除你建议删除的”。范围来自原盘点决策的delete/archive类别；keep/review/optional类别保持保留。操作前检查活动进程：应用数据根为外部PhotoSearchData，无活动进程引用删除目标。所有删除绝对路径均验证位于项目tmp内，拒绝目标及父路径中的reparse point；使用PowerShell/.NET在一个shell内完成删除，不串联其他shell，不修改ACL。

清理包括数百万目录；用互不重叠的4个删除工作线程继续完成，并持续保存result.json。独立核验确认115个目标全部不存在、9个保留目录存在、所有归档SHA256匹配。没有扫描外部真实照片库，没有执行归档探针，没有修改产品代码或当前任务状态；本次操作未运行产品测试。既有工作树改动继续保留。

## 文件入口

- [归档说明](../../temp/tmp-cleanup-archive-2026-10-03/README.md)
- [操作manifest](../../temp/tmp-cleanup-archive-2026-10-03/manifest.json)
- [归档SHA256](../../temp/tmp-cleanup-archive-2026-10-03/archive-hashes.json)
- [执行结果](../../temp/tmp-cleanup-archive-2026-10-03/result.json)
- [最终读回核验](../../temp/tmp-cleanup-archive-2026-10-03/verification.json)
- [清理前盘点](tmp-retention-audit-2026-10-03.md)

## 已删除的明确目标

| 目标（相对tmp） | 类型 |
|---|---|
| `__pycache__` | directory |
| `docs-personal-scope` | directory |
| `mk700` | directory |
| `p4-02n-real` | directory |
| `p4-02o-build` | directory |
| `p4-02t-upload` | directory |
| `p4-02w` | directory |
| `p4-02x-pytest` | directory |
| `p4-02x-ties` | directory |
| `p4-02y` | directory |
| `p4-02y-i-review` | directory |
| `p4-02y-j` | directory |
| `p4-02y-k` | directory |
| `p4-02y-k-review` | directory |
| `p4-02y-review` | directory |
| `p4-02y-t` | directory |
| `p402-m-review` | directory |
| `p402-review-limit` | directory |
| `p402-review-mpo` | directory |
| `p402-review-probe` | directory |
| `p402-review-t-parity` | directory |
| `p402b-dsh` | directory |
| `p402b-dsh 复核 验证` | directory |
| `p402b-dsh-run1` | directory |
| `p402c-review` | directory |
| `p402c-review2` | directory |
| `p402c-sol` | directory |
| `p402d` | directory |
| `p402d 归因` | directory |
| `p402d 归因 验证` | directory |
| `p402d-dsh` | directory |
| `p402e` | directory |
| `p402e 主审 10k` | directory |
| `p402e 领取 10k` | directory |
| `p402e 领取 回归` | directory |
| `p402e-claim-probe` | directory |
| `p402e-claim-probe-detail` | directory |
| `p402e-claim-probe-index` | directory |
| `p402e-claim-probe-index2` | directory |
| `p402e-claim-probe-noanalyze` | directory |
| `p402e-claim-probe-smoke` | directory |
| `p402e-diag` | directory |
| `p402e-diag-distinct` | directory |
| `p402e-diag-lease` | directory |
| `p402e-diag-opcodes` | directory |
| `p402e-diag-partial` | directory |
| `p402e-diag-partial-correctness` | directory |
| `p402e-diag-pinned` | directory |
| `p402e-diag-plain` | directory |
| `p402e-diag-walk` | directory |
| `p402e-review` | directory |
| `p402f` | directory |
| `p402h-recheck` | directory |
| `p402h-review` | directory |
| `p402i-isolation` | directory |
| `p402i-smoke` | directory |
| `p402j-contention` | directory |
| `p402j-review` | directory |
| `p402j-争用` | directory |
| `p402j-覆盖` | directory |
| `p402u` | directory |
| `p402v` | directory |
| `p402v-review` | directory |
| `p405-review` | directory |
| `p405-sol-disclosure-review` | directory |
| `ps-visibility-test` | directory |
| `qwen-speed-model-hf-cache` | directory |
| `review-py-mk` | directory |
| `search-reevaluation` | directory |
| `search-reevaluation-sdk` | directory |
| `greps.txt` | file |
| `p4-02m-after.log` | file |
| `p4-02m-after2.log` | file |
| `p4-02m-before.log` | file |
| `p4-02m-bounded-rounds3.log` | file |
| `p4-02m-bounded-smoke.log` | file |
| `p4-02n-nomodel.log` | file |
| `p4-02n-real-run.log` | file |
| `p4-02o-run.log` | file |
| `p4-02p-build.err` | file |
| `p4-02p-thumbs.err` | file |
| `p4-02q-build2.err` | file |
| `p4-02r.log` | file |
| `p4-02s-queries.log` | file |
| `p4-02s-thumbs.err` | file |
| `p4-02s-thumbs2.err` | file |
| `p4-02x-attribution.log` | file |
| `p4-02x-offline.log` | file |
| `p4-02x-review-commit-msg.txt` | file |
| `p4-02x-warm-text-control.log` | file |
| `p4-02x-warm-text.log` | file |
| `p401-commit-msg.txt` | file |
| `p401b-commit-msg.txt` | file |
| `p401b2-commit-msg.txt` | file |
| `p401c-closure-commit-msg.txt` | file |
| `p401c-commit-msg.txt` | file |
| `p401c-fix-commit-msg.txt` | file |
| `p401c-fix2-commit-msg.txt` | file |
| `p402a-commit-msg.txt` | file |
| `p402a-fix-commit-msg.txt` | file |
| `p402a-review-commit-msg.txt` | file |
| `p402b-dsh-rerun.txt` | file |
| `p402c-commit-msg.txt` | file |
| `p402m-blocksize-commit-msg.txt` | file |
| `p402m-flat-exact-commit-msg.txt` | file |
| `p402m-status-commit-msg.txt` | file |
| `p402n-t-commit-msg.txt` | file |
| `p402x-commit-msg.txt` | file |
| `uv-b67700ac461bb3f3.lock` | file |
| `v30-head.md` | file |
| `p4-02y-p/raw` | directory |
| `p4-02y-p/verify` | directory |
| `p4-02y-p/mini-check` | directory |
| `p4-02y-p\raw-2026-09-15\alternateNamesV2.txt` | file |
| `p4-02y-p\raw-2026-09-15\cities500.txt` | file |
