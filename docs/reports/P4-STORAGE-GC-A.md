# P4-STORAGE-GC-A 执行报告

日期：2026-10-10；交接更新：2026-10-11。实现基线：`8a8a6ae`。状态：**IMPLEMENTED / GC-A 定向验证通过；全量 D 级历史失败保留；GPT-6 Luna High 独立复核按用户批准的路由例外接受（非 DS-H PASS）**。

## 实施内容

- 新增只读快速存储概览 API `GET /api/v1/storage/summary`，并在“图库管理与分析明细 → 存储空间”加入手动查询。快速查询只读 catalog/WAL/SHM 与 SQLite 页统计，不递归目录，不返回绝对路径。
- 新增 `photo-search storage inspect`：默认快速概览；`--deep` 以 `scandir` 有界遍历 vectors/artifacts/thumbnails/previews/cache/places/backups，最多检查 500,000 个条目、目录栈最多 20,000 项；只保存摘要，候选路径只保留每 scope 最多 1,000 个用于清理批次。深度扫描保存时间、分类、跳过原因及 partial 状态；同一 data root 的深度扫描和 GC 共享扫描互斥文件锁。
- 新增 `photo-search storage clean --scope ...`，默认 dry-run。执行必须同时提供 `--stopped --apply`，取得现有 data-root lease 并检查 SQLite WAL checkpoint；锁忙、数据库不可用时零删除。支持过期无引用 Artifact、24 小时以上临时文件、旧版本媒体缓存、独立预览清空和可重建人脸裁剪缓存。
- Artifact 资格要求位于精确受控目录结构、普通文件、满足保留期且没有任何 `storage_uri` 元数据引用；路径引用按大小写和斜杠形式规范化查询，包含 `RECLAIMED` 记录。执行时重新检查 metadata 引用和 `(device, inode, size, mtime_ns)`，权限失败、身份变化都记数并可重试。不会调用 `reclaim()`，不删 catalog 行、向量、原图或人工事实。
- 普通页面没有破坏性清理入口；旧媒体版本只在有效版本目录/文件结构匹配时作为候选，当前缩略图与预览默认保留。单独清空预览会重建该缓存，不连带清缩略图。
- 未改 Schema、依赖、锁协议、原图或真实 `PhotoSearchData`；未提交/推送。

## 验证

- 相关定向 JUnit：`test-results/P4-STORAGE-GC-A-related.xml`，最新 **76 tests / 0 failures / 0 errors / 0 skipped**。覆盖四组 A1–A4、新增的正式/临时 Artifact processor 目录错配反例、Artifact/媒体缓存、catalog 媒体、索引修复/提交、备份单元、API 路由及前端受保护 API 检查。
- `ruff check`（本任务 Python 文件）、`python -m py_compile`（storage/CLI/API/测试）、`node --check src/photo_search/web/app.js`、`git diff --check` 均通过。
- CLI 合成 data_root smoke：deep inspect `ok / partial=false`；artifact clean 返回 `preview`；遗漏 `--stopped` 的 `--apply` 返回 `STOPPED_CONFIRMATION_REQUIRED`、exit 1、零删除。
- 文件字节数依据普通文件 `st_size` 汇总；稀疏/压缩文件的文件系统实际分配簇可能不同，返回口径已显式注明。SQLite freelist 与 BLOB 统计均只列逻辑量，不加入目录文件字节总和。
- A4 的 Windows 账户不能创建真实 symlink；通过遍历器故障注入验证 reparse 项被跳过并让报告标记 partial。忙 lease/扫描锁、身份变化、删除权限错误和失败后重试均由合成夹具验证。
- 首次 D 级 `pytest -q tests/unit tests/integration` 收集失败，8 个文件因 `tools` import path 错误中止，另有 `tests/integration/test_p4_02y_l_identity.py` 末尾的孤立 `\\n` 语法错误。以 `PYTHONPATH=src;tests;.` 重跑解决 import path；删除该测试文件末尾孤立语法行后完整回归成功收集并执行。该小范围测试语法修复与本卡受影响代码无关。
- 一次更宽的相关集（169 tests）曾有 6 个失败：P4-05 恢复夹具的地点不满足当前 source-backed 约束，以及人物/搜索旧静态断言与当前页面不一致；本任务未改这些文件。`app.js` 的 SQLite 静态禁词检查发现并修正了本任务首版文案；其直接静态检查已在最终 74 测试中通过。

## 未完成与边界

- 状态保持 **VALIDATION PENDING**：完整 unit/integration D 级回归现已收集并运行，但仍有跨任务 failures/errors；不能写任务 PASS。
- 文件删除属于高风险数据安全面，需独立 **DS-H 全受影响面工程复核**后才能收口。当前实施会话没有切换模型，也不声称 DS-H 结论；是否需要 Sol 聚焦终审由 DS-H 按 V3.1 判断。
- 未对真实图库、真实 data root、运行中用户服务、模型/GPU、30 万规模或发行包执行任何操作。卡 B 不启动，直到 A 的验证与复核闭环完成。

## 2026-10-10 当前会话受影响面复核

- 覆盖：本任务全部改动面，包括 `src/photo_search/storage.py`、CLI、API、前端、定向测试、任务卡、执行报告与开发状态；沿 ArtifactStore 发布/回收语义、data-root lease 和相关 schema 外键核对调用链。
- 结论：**发现 P2，待修复；本次复核不构成 DS-H 独立审查或任务 PASS。** `storage.py::_artifact_candidate()` 验证了 processor 目录和文件名中 processor ID 各自格式合法，但没有验证二者相等；因此旧的无引用文件若位于 `artifacts/pending/<processor-A>/...`、文件名却带 `<processor-B>`，仍可成为候选。临时文件分支也需保证目标文件 processor 与目录一致。任务卡要求仅清理符合受控结构的文件，修复后应为正式文件和临时文件都加入该一致性约束及定向回归。
- 证据边界：本次未运行 pytest；读取现存 `test-results/P4-STORAGE-GC-A-related.xml`，确认 74/0/0/0；本次 `git diff --check` 通过。D 级收集阻塞保持原记录。未修改产品代码。
- 当前会话按用户要求完成受影响面复核，未切换模型；这不是独立 DS-H。该 P2 已在下方“P2 修复与回归重跑”中修复；D 级跨任务回归和独立 DS-H 复核仍未关闭。

## 2026-10-10 P2 修复与回归重跑

- 修复 `_artifact_candidate()`：正式文件解析出的 processor ID 必须与 `artifacts/pending/<processor>/` 一致；临时文件解析其目标 Artifact 名称，并执行相同校验。目录与文件名错配的旧正式文件或临时文件只统计，不进入清理候选。
- 新增两项合成回归，分别覆盖正式文件及临时文件 processor 不匹配；两者均验证扫描候选数为 0、停机 apply 不删除文件。
- 相关定向 JUnit：`test-results/P4-STORAGE-GC-A-related.xml`，**76/0/0/0**；Ruff、py_compile、Node 检查通过。
- 修复 D 级收集后执行 `tests/unit tests/integration`：**2,338 项，70 failures、43 errors、49 skipped**（其余 2,176 passed）。43 个 setup errors 均因其他 P2/P3 测试夹具对无有效来源目录的测试地点执行 ASSIGN，触发 `PLACE_NOT_FOUND`；failure 还包括其他模块的前端静态断言、fixture/API/迁移断言及同类地点语义错误。全量结果见 `test-results/P4-STORAGE-GC-A-full.xml`，不作为通过证据，也未为本卡修补这些跨任务测试。
- D 级仍未通过，且独立 DS-H 复核尚未完成；本卡保持未 PASS。

## 2026-10-10 P2 修复后复核

- 复核范围：仅针对上一轮 finding 的修复、对应新增回归及其直接 Artifact 候选路径；确认 `_MANAGED_ARTIFACT` 捕获文件名 processor ID，正式文件与临时文件分支都将其和 `artifacts/pending/<processor>/` 比较。错配时扫描不产生清理候选，apply 路径因重新扫描同样不会删除；新增两种错配用例均断言候选数为 0 且文件保留。
- 结论：**P2 修复复核通过，未发现该修复范围内的遗留问题。** 这只关闭当前会话复核发现的结构校验项，不构成独立 DS-H，也不代表任务 PASS。
- 证据：只读取现存 `test-results/P4-STORAGE-GC-A-related.xml`（76/0/0/0）与 `test-results/P4-STORAGE-GC-A-full.xml`（2,338 项；70 failures / 43 errors / 49 skipped）；本次未运行 pytest。`git diff --check` 通过。
- D 级结果仍有跨任务失败/错误，独立 DS-H 复核仍待完成；不扩大本次修复轮去修改无关地点/人物/搜索夹具。


## 2026-10-11 独立复核路由与 GC-B 前置条件

- 用户确认：GC-A 已由独立 GPT-6 Luna / High 完成复核并通过；本次只核对本地记录，没有重复审核。核对时本报告此前没有该复核条目，也没有找到单独的 GC-A 复核报告，因此本条记录以用户本次明确提供的结论为依据，不补写未提供的逐文件覆盖清单或 finding。
- 原任务及 V3.1 §2.4 对此高风险文件清理面要求独立 DS-H 全受影响面主审。GPT-6 Luna / High 不符合该原路由；按用户明确指示记录路由/验收例外，接受该独立复核结果作为 GC-B 的前置 review gate。此例外**不构成 DS-H PASS**，也不改写原路由规则。
- GC-A 相关定向 JUnit `test-results/P4-STORAGE-GC-A-related.xml` 现存结果为 **76 tests / 0 failures / 0 errors / 0 skipped**，本次未重跑；据此，在 GC-A 直接受影响的定向覆盖中未见新增回归。
- 全量 unit/integration 的既有结果仍为 **2,338 tests / 2,176 passed / 70 failures / 43 errors / 49 skipped**，保留在 `test-results/P4-STORAGE-GC-A-full.xml`，不修改结果、不重跑，也不把它标为通过。早期 2,152 项 JUnit 与当前失败 nodeid 并非完全相同；本记录不以计数相同推断失败集合相同，也不宣称全量零回归。GC-A 直接受影响定向集合无失败；全量历史失败继续单独跟踪，不阻止按用户批准的路由例外启动 GC-B。
- 据此，GC-A 的定向验收与 review 前置条件可供 GC-B 使用；全量失败仍单独开放。
