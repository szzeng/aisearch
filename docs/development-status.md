# Development status

## Current state

**P0 is complete. P1-01 is PASS (C2 final short re-review). P1-02 is PASS. P1-03 is PASS. P1-04 is PASS
(B level plus real-port/C-level packaged smokes and the C1 Windows-path/local-access review). P1-05 is PASS
(B level + C-level packaged smoke + a recorded real-browser smoke); its non-blocking follow-up P1-05B
(front-end verification base) is also PASS. P1-06 is PASS (D level: 16 new adversarial acceptance cases for
duplicate / move / replace / offline / remap / restart, with no production change). **P2-01 and P2-01A are PASS
(A-level protocol closures, no production/schema/dependency change). P2-02 is PASS (D level: a continuous
`0001 → 0002` migration, the persistent scheduler with scheduler-owned device qualification, and named
fault-injection/recovery cases). P2-03 is PASS (D level: schema 3 artifact/outbox/index confirmation and real
LanceDB replay/repair). **P2-03A is PASS** (D level: the 0004 place core, GPS facts, the place
directory, manual assignment/unassign/restore_auto with batch and compensating undo, and the pure
SQLite place filter API; 48 new cases). **P2-03B is PASS** (D level: the `0005` dataset/resolution
migration, the offline name-package lifecycle, the `non_model + CPU_PREFERRED` nearby-city resolver,
suggestion-only candidates with an independent suggestion revision, and the explicit local dataset
tool; 65 new cases). **P2-03C is PASS** (B level + a recorded real-browser smoke + the C-level
packaged smoke proving the page is inside the frozen bundle). **P2-04 is PASS** (B level + the D-level
projection fault injection + a C-level packaged smoke: the `0006` derived search layer, the versioned
tokenizer/controlled MATCH, the effective-place text projection and rename reprojection; 35 new cases,
schema_version 5 → 6). **P2-05 is PASS** (B level + the deterministic retrieval comparison + a replay
against the P2-03 crash window: the two bounded recall channels, the pure RRF fusion, the authoritative
candidate-ID restriction and the pending-projection top-up; 19 new cases, no migration, schema_version
stays 6). **P2-06A is now READY / CURRENT TASK**; the planned P2-06 was split into
**P2-06A** (search protocol, `POST /api/v1/search`, sessions/cursors, `SearchProfile` binding and atomic
activation) and **P2-06B** (explicit relevance feedback and the rerank adapter), without changing the
overall route. **P2-06A is PASS** (B level + a C-level packaged smoke + the D-level migration/activation/
expiry injections: the frozen `SearchRequest`/`SearchResult` envelopes, the process-local search sessions
with opaque bound cursors, the immutable `search_profiles` plus its atomic single-row activation and the
`catalog_filter_revision` staleness matrix; 52 new cases, **schema_version 6 → 7**). **P2-06B is PASS**
(B level + a C-level packaged smoke + the D-level migration/idempotency/gate injections: the append-only
`search_feedback` audit log with `request_id` idempotency, the optional rerank adapter boundary whose
candidate scores never enter RRF/BM25/距离, and the reranker qualification gate on profile activation;
49 new cases, **schema_version 7 → 8**). **P2-07 is PASS** (B level + a recorded real-browser smoke + the
C-level packaged smoke: the dependency-free `/search` page with frozen hit-reason rendering, the
`POST /api/v1/search/image` multipart endpoint with byte/pixel/decode-time/format hard limits and
two-path temp-file cleanup, the place filter and the four-category coverage view; 70 new cases, no
migration, `schema_version` stays 8). **P2-08 is PASS** (B level + D-level named fault injection +
a recorded real-browser closure: 11 adversarial cases covering scan/restart recovery, the pre-commit
crash window, the Lance-written/unconfirmed window, expired leases and late place results,
mid-processing content changes, incremental add/rename/move/delete, place/source filter
correctness, session staleness codes, the offline name-package lifecycle, the old-READY-generation
boundary and upload search under concurrency; **11 new cases and zero production changes**,
`schema_version` stays 8). This closes the P2 phase.
**P3-01 is PASS** (B + D + C level: the pinned PP-OCRv4 OCR processor, its bounded high-resolution
slicing, the `0009` OCR document/region layer with the fifth projection cause, the `ocr_text`
derived document and its same-transaction queue, a controlled CLI entry point and a truthful
read-only OCR status; 75 new cases, **schema_version 8 → 9**). **P3-02 is PASS** (B + D + C level: the pinned
SCRFD detector and ArcFace recogniser with the P0-04 weights pinned by size and SHA-256, the `0010` face core
(`faces`/`face_embeddings`/`persons`/`face_person_links`/`face_association_queue`), the independent face
candidate index with its own generations and outbox, and the calibrated conservative association (merge `0.56`,
suggest `0.29`, conflicts and low-quality faces only ever become suggestions); 87 new cases,
**schema_version 9 → 10**). This closes the face-detection half of the P3 phase.
**P3-03 is PASS** (B + D + C level: the versioned `nfc1:` `name_key`, the one rename / same-name / explicit
merge use case, `merged_into` canonical resolution with a bounded cycle refusal, the compensating undo with
forward-only revisions, the human-control downgrades that keep `MANUAL_UNASSIGNED`/exclusion facts intact,
the four `/api/v1` person paths with their frozen error codes, and the five read-only person counts in
`face_summary`; 74 new cases, **schema_version 10 → 11**). **P3-04A is PASS** (A-level C2 contract freeze:
ADR-0007, the machine-readable face-correction companion and 26 contract cases; no production/schema/
dependency change). **P3-04B is PASS** (B + D + C level: the `0012` operation-log rebuild that widens the
closed CHECK vocabulary without adding a table, the single-face assign/unassign/restore_auto matrix with
canonical exclusions, the 1..500 mixed batch, the global `request_id` gate ahead of every revision check,
the `FACE_CONTROL` before/after snapshots with a forward-only compensating undo, the logical
control-revision gate the automatic association now proves inside its own write transaction, the four
`/api/v1` paths with their frozen `FACE_*` errors, and the four read-only human-control counts in
`face_summary`; 50 new cases, **schema_version 11 → 12**). **P3-04C is PASS** (independent C2 review plus
five minimal correctness fixes: persisted replay revision, stable face undo replay envelope, forward-only
restored link revisions, reachable AUTO_ALLOWED canonical exclusion filtering, and no image-vector refresh
from face correction; 52 focused cases and 1116 full cases, A + D + C revalidated). **P3-05A is PASS**
(A-level C2 contract freeze: ADR-0008, the machine-readable person-split companion and 22 contract cases;
no production/schema/dependency change). **P3-05B is PASS** (B + D + C level: 44 new cases for the frozen
split — the `0013` operation-log rebuild, the eligibility/revision matrix, the unnamed created person, the
fixed `SPLIT_GROUP`+`FACE_CONTROL` items, the global-request-id gate, the compensating undo with its
conflict matrix, the closed API/error surface and the real CPU detect→merge→split→restart→undo→restart
fixture; `schema_version 12 → 13`). **P3-05C is PASS** (independent C2 review plus four minimal correctness fixes:
forward-only split/undo link revisions, strict contiguous operation-item validation, and closed split response
schemas; error details also returned to the frozen field set; 80 focused/cross-regression cases and 1185 full
cases, A + D + C revalidated). **P3-06 is PASS** (B + C level + a recorded real-browser smoke): the three
new read lists (`GET /persons`, `GET /faces`, `GET /person-operations`), the server-side bounded
face-avatar crop rendered from the authoritative box and the frozen preview
(`GET /faces/{id}/thumbnail`), and the dependency-free `/persons` page with its same-name prompt,
manual-unassigned queue, face-level selection, batch assign/unassign/restore_auto, split and the shared
undo; **45 new cases, no migration, `schema_version` stays 13**. **P3-07A is PASS** (A level: the frozen
person `all`/`any` × place AND search contract — `ADR-0009`, the machine-readable companion
`p3-person-search-contracts.v1.json` and 41 contract cases, with **zero production/schema/dependency
change**; 1271 full cases with 0 failures / 0 errors / 48 environment skips). **P3-07B is READY / CURRENT
TASK** → **P3-07B is PASS** (B + D level: `person_ids`/`person_match` on `POST /api/v1/search`, the single
`MEMBER_PREDICATE`-based authoritative person condition combined with every other condition by AND, canonical
resolution/de-duplication, the person condition in the continuation digest, `INVALID_PERSON_FILTER` (422) and
the `filters` echo; **63 new cases, no migration, `schema_version` stays 13**). The planned P3-07 was split
into **P3-07A** (the freeze above) and **P3-07B** (its implementation), without changing the overall route;
**P3-07C** (two-session staged review: independent DS-H full engineering review, then a separate C2 /
GPT-5.6 Sol / high focused review of the canonical / revision / cache-invalidation boundaries) is
**PASS（C2 final short re-review）**. DS-H closed the two canonical publication races with a read snapshot,
same-snapshot authority pre-binding, bounded re-anchor, a post-render publication gate and publish-only session
registration; the third fix preserves the actual moved counter so exhausted retries return filter stale or
suggestion stale with the established filter-first priority. DS-H full evidence is **1341 tests / 0 failures /
0 errors / 48 environment skips**; Sol independently reran the five direct error/publication regressions with
**5 / 0 / 0 / 0**, and Ruff, lock-check and diff-check are clean. See
[the final focused review §9](reports/P3-07C-sol-focused-review.md).
**P3-08A is PASS（Sol final short re-review）**: ADR-0010 is **Accepted** after DS-H contract drafting, two bounded
correction rounds and Sol focused review. The final re-review proves `undo → OPEN` restores the hold rather than
releasing it, and the 12 error codes now have strict per-code `oneOf` details shapes; all three negative probes are
rejected and both positive controls pass. Final contract evidence is **58 / 0 / 0 / 0**, with Ruff, diff-check and
lock-check clean. See [the final freeze report §10](reports/P3-08A-sol-contract-freeze.md). **P3-08B is PASS**
(B + D + recorded real-browser smoke), and its independent two-stage review **P3-08C is PASS
（C2 final short re-review）** after DS-H closed the real HTTP error envelopes and the `STALE` reappearance boundary.
Sol independently verified the per-code `details` closure (including `closure_details` for the required empty
`INVALID_CURSOR` object), hold/release/queue plus §10 undo ordering, and `STALE → SUGGESTED` only for live eligible
`OPEN` groups; focused evidence is **22 / 0 / 0 / 0**. See
[the final focused review](reports/P3-08C-sol-focused-review.md). **P3-08D is READY / CURRENT TASK**: add the frozen
person `any/all` filters to `/search` and close the still-unrun BROW-002 person+place browser path. **P3-08D is
PASS** (B level + one recorded real-browser smoke, `SOL_NOT_REQUIRED`): the `/search` page now reads the paged
`GET /api/v1/persons` list for its person choice, sends `person_ids`/`person_match` only for a real selection,
echoes the server's canonical `filters`, refuses to continue an old cursor once the conditions changed, and the
recorded in-app-browser closure completed the previously unrun **BROW-002** (person any/all × place AND → person
rename → `CATALOG_FILTER_REVISION_STALE` → re-search). 10 new static contract cases; focused **131 / 0 / 0 / 0**
and full **1461 / 0 / 0 / 48** (48 environment skips), Ruff / `git diff --check` / `uv lock --check` clean. This
closes the P3 phase. The next task is **P4-00A — 拍摄时间/时区/回退来源/新 order 契约**
([docs/tasks/P4-00A.md](tasks/P4-00A.md)), whose stage A is contract drafting and the full engineering pre-review
by an independent **DS-H / DeepSeek V4.1 Flash / high reasoning** session, followed by a mandatory stage B
**C2 / GPT-5.6 Sol / high** focused freeze. Model switching remains manual.
**P4-00A is PASS** (A level: ADR-0011 **Accepted** after DS-H drafting, two bounded correction rounds and Sol
focused freeze; companion SHA-256 `80189349bb4ea306225f9eae77ae9266c6d81c42de52c0b72bdc940a0702afd7`,
final evidence **58 / 0 / 0 / 0**). **P4-00B is PASS（独立 DS-H 全量工程主审；`SOL_NOT_REQUIRED`）**:
`0015_taken_time.sql` +
`SUPPORTED_SCHEMA_VERSION 14 → 15`, the pure-CPU `taken_time_extract` chain
(`EXIF_ORIGINAL → EXIF_DIGITIZED → EXIF_ROOT → FILE_MTIME`, civil key without offsets, one
`CAPTURE_TIME_CHANGED` per transaction), `capture_desc` + `capture_from`/`capture_to_exclusive` on the
asset listing and the search envelope (filters echo **11 + 2 = 13** keys), the conditional `taken_time`
honesty block and the closed detail object, the `/` + `/search` capture UI, the `taken_time` CLI and the
browser-smoke fixture. Focused **268 / 0 / 0 / 0**, full **1598 / 0 / 0 / 48**, D-level 8 injections,
a real onedir rebuild whose bundle carries `0001`～`0015` SHA-256-identical to source, a packaged smoke with
`status=ok`/`problems=[]` and packaged `schema_version=15`, the missing-0015 counterexample
(`409 SCHEMA_INCONSISTENT`, zero writes) and the recorded in-app-browser closure of ADR-0011 §10's eight
steps. See [the P4-00B report](reports/P4-00B-taken-time-implementation.md).
The independent DS-H full engineering review re-ran the complete suite (**1598 / 0 / 0 / 48**), a focused
**353 / 0 / 0 / 0**, rebuilt the onedir (all 15 migrations and all 27 packaged `photo_search` data resources
byte-identical to source, closing a stale packaged `web/app.js` that the implementation's C-level evidence
predated), re-ran the packaged smoke (`status=ok`, `problems=[]`, packaged `schema_version=15`, rules_version 1)
and the missing-0015 counterexample (`409 SCHEMA_INCONSISTENT`, zero writes), and re-ran key recorded-browser
steps including the half-open capture bounds; see [the DS-H review](reports/P4-00B-dsh-engineering-review.md).
**P4-00C-A is PASS（B + D level：HEIC 业务实现；`SOL_NOT_REQUIRED`）**: `pillow-heif==1.8.0` (+ bundled
libheif 1.23.4) is pinned, `HEIC`/`HEIF` join the supported set only when the plugin really loads, the EXIF
orientation pillow-heif clears is restored only when the container carries no `irot`/`imir`, broken HEIC files
are per-file `DECODE_FAILED`, and the P2-07 upload whitelist stays explicitly unchanged. Focused
**85 / 0 / 0 / 0** and full **1632 / 0 / 0 / 48**, with existing-format artifacts byte-identical before/after;
see [the P4-00C-A report](reports/P4-00C-A-heic-business-implementation.md) (with the A-layer correction of
§12). **P4-00C-B is PASS（C level：HEIC 环境与正式包兼容；`SOL_NOT_REQUIRED`）**: the onedir bundle carries
the pillow-heif extension and its libheif runtime byte-identical to the `.venv` source (plus the R1 audit of all
27 packaged `photo_search` data resources), the packaged process decodes HEIC to the same upright 320×240 frame
as its JPEG twin and serves the frozen taken-time/GPS semantics, and the three degraded variants
(libheif / extension / both removed from a disposable copy) all answer the structured
`FORMAT_UNSUPPORTED` while JPEG and the existing cache stay intact — a defect the C1 review caught and the A
layer fixed by making the plugin registration atomic. Evidence: full **1633 / 0 / 0 / 48**, healthy smoke
`status=ok`/`problems=[]`, three degraded smokes `status=ok`/`problems=[]`; see
[the P4-00C-B report](reports/P4-00C-B-heic-packaging.md). **P4-00C is PASS**（独立 DS-H A+B 全量工程主审，
`SOL_NOT_REQUIRED`，未命中 V3.1 §2.2、不升 DS-MAX），见
[the DS-H review](reports/P4-00C-dsh-engineering-review.md)：主审独立重跑全量 **1633 / 0 / 0 / 48**、聚焦
**98 / 0 / 0 / 0**、真实重建 onedir 与健康/降级四个打包 smoke、D-5 产物指纹与全部冻结面差分，并补做
「缺 libde265/libx265 仍 fail closed」探针。下一任务为 **P4-01 — 10 万/30 万 exact/ANN 对照、索引维护、
过滤分布与并发调度基准**（[docs/tasks/P4-01.md](tasks/P4-01.md)，推荐 **DS-H / DeepSeek V4.1 Flash / 高推理**）。

P1-01 history (frozen contract `docs/decisions/ADR-0002-catalog-identity-migration-scan-contract.md`):

- Implementation `92ca7566`, three C2 review rounds (`83adb65` FAIL, `8393583` FAIL) and two corrective passes
  (`ce6f56c5`, `f0b4f402`), final C2 short re-review PASS in [docs/reports/P1-01-c2-final-pass.md](reports/P1-01-c2-final-pass.md).
  Accepted closing baseline: `e39a40c`.
- Persistent positive writes are submission-qualified, retry/lease exhaustion commits job/scan_run terminal state in one
  transaction, schema compatibility cannot be bypassed, stale hashes cannot produce success, and same-scan recovery no
  longer inherits observations from an older execution.

P1-02 (streaming scan, stability waiting, hash before/after stat, missing rules) is recorded in
[docs/reports/P1-02-streaming-scan-stability-and-missing.md](reports/P1-02-streaming-scan-stability-and-missing.md).

P1-03 (EXIF orientation, thumbnails/previews, sharded rebuildable cache, bad-media isolation) is recorded in
[docs/reports/P1-03-orientation-thumbnails-cache-and-isolation.md](reports/P1-03-orientation-thumbnails-cache-and-isolation.md).
It ships `catalog thumbnail` / `catalog preview` / `catalog media-status`, keeps `schema_version = 1` (no new table, no
new dependency) and treats the atomically published artifact file itself as the READY marker.

P1-04 (sources/assets API, controlled file access, local session protection) is recorded in
[docs/reports/P1-04-api-controlled-files-and-local-session.md](reports/P1-04-api-controlled-files-and-local-session.md).
It adds `src/photo_search/api/**` (`/api/v1/{health,session,capabilities,stats,sources,assets,jobs}` plus
thumbnail/preview/original), a loopback-only binding policy with a single-use bootstrap credential, HttpOnly
session + double-submit CSRF + Host/Origin checks, four-layer controlled path resolution, and one background
scan executor that reuses the production `catalog scan` path. `schema_version` is still 1, ADR-0002 is unchanged,
and the only dependency edit is declaring the already-resolved `pydantic` range explicitly.

P1-05 (sources page, insertion-time browsing, detail view, offline hints) is recorded in
[docs/reports/P1-05-local-frontend-sources-browse-detail-offline.md](reports/P1-05-local-frontend-sources-browse-detail-offline.md).
It ships a dependency-free static front end (`src/photo_search/web/**` served from package data by
`src/photo_search/api/web.py`), an additive `order=time_desc` keyset with its own cursor space, an additive
`next=` redirect on the session exchange, and a launcher URL that lands on the page. `schema_version` is still 1,
ADR-0002 is unchanged, `pyproject.toml`/`uv.lock` are untouched, and every existing P1-04 test still passes
unmodified.

P1-05A 的服务端 Windows 路径脱敏补强为 **PASS**：完整 B 级测试（303 passed、1 skipped）、专项回归、
真实端口损坏图片场景、Ruff 与差异检查均通过；详见
[P1-05A 报告](reports/P1-05A-local-path-redaction.md)。

P1-06 (the duplicate / move / replace / offline / remap / restart acceptance closed loop) is recorded in
[docs/reports/P1-06-identity-lifecycle-acceptance.md](reports/P1-06-identity-lifecycle-acceptance.md). 它新增
一个测试文件 `tests/integration/test_catalog_identity_lifecycle.py`（16 个用例），**未改任何生产代码**：
`schema_version` 仍为 1，ADR-0002、`0001_initial.sql`、`pyproject.toml`、`uv.lock` 均未改动，既有测试
全部原样通过。该任务完成后主线依次进入 P2-01、P2-01A 与 P2-02。

Current architecture: `个人图片搜索系统_V3.0_开发主架构.md`, requirement revision 5 (2026-09-15 300k formal capacity alignment).
Current execution strategy: `个人图片搜索系统_V3.1_Codex_DeepSeek_开发执行策略.md`, revision 5 (2026-09-20 remaining-task review-routing alignment).

## Latest architecture alignment — requirement revision 5 (2026-09-15)

The product's formal supported library scale is now **300,000 image assets**. Capacity, performance, recovery,
and release PASS claims use 300k as the formal target. **350k–400k** may be used only as optional synthetic
stress observation to find performance cliffs; it is not a support promise and does not create a hard rejection
at asset 300001.

The core architecture is unchanged: SQLite/WAL/FTS5, LanceDB, the persistent scheduler/outbox/replay/repair
path, one bounded GPU worker, CUDA/GTX 1660 6 GB baseline, and existing person/OCR/place/model-upgrade
contracts remain in force. No production code, dependency, schema or migration changes are required by this
alignment, and completed P1/P2/P3 acceptance is not reopened.

P4 is correspondingly narrowed: P4-01 benchmarks exact/ANN behavior at the supported scale and only tunes ANN
when 300k evidence shows a bottleneck; P4-02 becomes 10k → 100k → 300k formal validation, with 350k–400k
optional stress observation. Do not introduce distributed scheduling, Redis/Celery, multi-GPU, another vector
backend, manual sharding or other million-scale complexity without a separate measured need and architecture
decision.

Alignment report: [2026-09-15 300k capacity alignment](reports/2026-09-15-300k-capacity-alignment.md).

## Latest review-routing alignment — strategy revision 6 (2026-09-20)

从 P3-06 起，剩余任务的独立全量工程主审优先交新会话 **DS-H**；只有 V3.1 §2.2 六类具名条件成立时，
才手动升 **DeepSeek max**。**GPT-5.6 Sol / high** 不再重复全仓主审，只接收 DS-H/DS-MAX 已收敛的
高风险不变量、ADR 差异、未决矛盾、关键 diff 和最小反例，做聚焦终审或最终冻结。C1 继续负责
Windows/ONNX/PyInstaller/HEIC/实机发行环境证据。

含 DS-H 主审与 Sol 终审的闭环现在固定拆成两个独立新会话：DS-H 运行完整工程审查并落盘压缩交接包；Sol
只读点名的高风险边界。Sol 为必选时，DS-H 阶段只能写
`ENGINEERING REVIEW PASS / SOL FOCUSED REVIEW PENDING`，不得提前 PASS 或生成下一卡。DS-H 可草拟和工程
预审契约，§11 高风险协议及任务表点名项目仍由 Sol 作最终冻结；无高风险语义变化/未决项的非必选任务记录
`SOL_NOT_REQUIRED` 后不调用 Sol。

该调整不改变任务顺序、产品架构、冻结协议、验收等级或 PASS 门槛；历史已 PASS 的任务卡/报告不追改。
详见 [2026-09-20 审查路由对齐报告](reports/2026-09-20-review-routing-alignment.md)。

## Latest planning closure — P2-00 (2026-09-13)

地点规划已完成（文档交付，**地点功能尚未实现，P2-01 尚未执行**）。范围已由用户确认：国家／城市／景点／
自定义地点，GPS 与手动归类。GPS 事实、机器建议、人工归属分离；取消后的人工锁保留；首版提供离线附近城市
建议供确认，不把最近点当准确行政区。沿用静态前端，schema_version 仍为 1。

决策：[ADR-0004](decisions/ADR-0004-place-classification-plan.md)。
全项目规划：[P2-00 路线图](plans/P2-00-location-project-roadmap.md)。
核对与验证：[P2-00 报告](reports/P2-00-location-project-planning.md)。
本次文档闭环 PASS：既有迁移测试 11/11 通过、Ruff／diff-check clean、7 份文档链接通过；
测试临时目录 ACL 限制经隔离目录和工具提权解决，完整命令在报告。未执行地点功能测试或 P2-01 契约测试。

下一任务仍为 **P2-01 / C2 / GPT-5.6 Sol / high**；增加无模型 CPU／非向量产物和权威筛选版本外壳。
随后 **P2-01A → P2-02 → P2-03 → P2-03A/B/C → P2-04～08**。地点实现不提前到本次规划。
拍摄时间、HEIC 与 remap 产品欠项分别排入 P4-00A/B、P4-00C、P4-05A/B。

## Latest contract closure — P2-01 (2026-09-13)

P2-01 已冻结 [ADR-0003](decisions/ADR-0003-p2-processing-runtime-and-search-profile-contract.md)：
JobRequest/JobResult 五字段身份映射、三种 device_policy 与禁止隐式 fallback、RuntimeCapabilities、
三类 ProcessorSpec、适配器边界、不可变 SearchProfile/原子切换、space 隔离、非向量成功结果和
CatalogFilterRevision 独立版本。机器可读伴随物为
[p2-contracts.v1.json](decisions/contracts/p2-contracts.v1.json)，报告见
[P2-01 报告](reports/P2-01-processing-runtime-search-profile-contract.md)。

本任务没有改生产代码、表/migration、schema_version、ADR-0001/0002、依赖或既有测试。
A 级验证：新契约测试 11 passed；Ruff 与 diff-check clean。环境无第三方 JSON Schema validator，
测试实际执行伴随文件所用关键字子集的实例正反例，但不声称运行时拒绝已经实现。

其后续契约任务 **P2-01A** 也已完成；当前进入 P2-02。

## Latest place contract closure — P2-01A (2026-09-13)

P2-01A 已冻结 [ADR-0005](decisions/ADR-0005-place-facts-assignment-and-search-contract.md)：GPS 原始事实、
附近城市机器建议、人工有效归属/取消锁三层分离；稳定 place_id、首版四类地点、同名与祖先层级；
批量 500、幂等/revision、操作历史与补偿撤销；地名包 manifest/原子激活/回退；增量 DDL、`/api/v1`
与结构化错误；SQLite 权威筛选、suggestion revision 和 CatalogFilterRevision 触发矩阵。

机器可读伴随物为 [p2-place-contracts.v1.json](decisions/contracts/p2-place-contracts.v1.json)，报告见
[P2-01A 报告](reports/P2-01A-place-contract.md)。本任务未改生产代码、migration、schema_version、
依赖或既有测试。A 级验证：地点契约专项 21 passed，Ruff 与完整暂存差异检查 clean；schema/样例及
显式跨项语义检查不等于数据库/API/运行时拒绝已经实现。

下一任务是 **P2-02 / DS-H / DeepSeek V4.1 Flash / 高推理**，任务卡
[docs/tasks/P2-02.md](tasks/P2-02.md)。它实现持久调度、设备资格、暂停/退避/优先级与阻塞恢复，
地点只用确定性 non-model CPU 假执行器验收，不提前实现 GPS 或地点表。

## Latest scheduler closure — P2-02 (2026-09-13)

P2-02 已实现持久调度闭环并通过 D 级验收，报告
[P2-02 报告](reports/P2-02-persistent-scheduler-and-device-qualification.md)。
起点 HEAD `bf1a652`（P2-01A），工作树干净。

新增 `src/photo_search/scheduler/**`（`p2-contracts.v1` 契约实现、不可变 runtime capability 快照、
处理器专项 evidence、唯一设备资格矩阵、持久暂停/公平窗口/指数退避、claim→dispatch→submit→
cancel/recover 闭环、只读诊断投影）和连续增量迁移
`catalog/migrations/0002_scheduler_runtime.sql`：**`schema_version` 推进到 2**，
`0001_initial.sql` 未改，五字段逻辑唯一键、七态状态机、lease/attempts/迟到提交语义不变。
`POST /api/v1/scheduler/{pause,resume}`、`GET /api/v1/jobs`（派发摘要）与
`GET /api/v1/capabilities`（scheduler/runtime 健康摘要）已接入既有 session/Origin/CSRF/Host 边界。
本任务未修改 `workers/**`、打包入口或依赖，因此未触发 C 级；未产生 artifact/outbox/LanceDB/向量行。

| Check | Result |
|---|---|
| `uv run pytest tests/unit tests/integration` | exit 0，`415 passed, 1 skipped in 131.90s` |
| `uv run pytest -q tests/unit/test_p2_scheduler.py` | exit 0，`29 passed` |
| `uv run pytest -q tests/integration/test_p2_scheduler_recovery.py` | exit 0，`10 passed` |
| `uv run pytest -q tests/integration/test_p2_scheduler_api.py` | exit 0，`8 passed` |
| `uv run ruff check .` / `git diff --check` | clean / clean |
| `uv lock --check` | exit 0，`Resolved 95 packages`（依赖未变） |
| v1→v2 迁移 | 真实 0001 库 + 中文/空格位置 + 活动 lease 行：`applied=(2,)`、备份内 `schema_version=1` 且只有 6 张 v1 表、19 列逐行不变、新列仅 `requested_device='auto'`、旧 lease 三元组原样保留 |
| 中断/缺件/过新 | 注入 0003 失败后整体回滚（版本仍 2、无 `probe_note`）；`DROP scheduler_state` 后 `SCHEMA_INCONSISTENT` 且主库字节不变；过新库 `SCHEMA_TOO_NEW` 且无 `-wal`/`-shm`（库与 API 两条路径） |
| 调度/资格 | GPU 缺失/不健康/无 evidence → `GPU_REQUIRED` 阻塞且不耗 attempts、显式 GPU 不转 CPU，同队列 `CPU_PREFERRED` non-model 假任务正常 SUCCEEDED；新健康 revision 后只恢复合格任务，`DEVICE_POLICY_CONFLICT` 不复原 |
| 重启/退避/公平 | 进程退出后过期 lease 接管 `attempts+1`、旧 worker 心跳与迟到结果被拒且零副作用；退避 `11000→13000→17000→21000`（8000 被 4000 上限截断）；pause 跨重启仍暂停而既有 RUNNING 合法完成；10 个公平窗口后旧 backlog 追平并被选中 |
| 结果资格 | `JobResult` provider/device/precision 不匹配 → `RUNTIME_RESOLUTION_MISMATCH` 且 job 行逐字节不变；`artifacts=[]` 与结构化非向量结果均正常；协议不兼容记确定性 FAILED、执行器异常记可重试且只记异常类型 |
| 诊断工具 | `frontend_smoke_fixture.py --too-new` 改为由 `SUPPORTED_SCHEMA_VERSION` 推导；顺带修掉该工具 `--help` 的历史崩溃（argparse 二次 `%` 格式化） |

## Latest artifact/index closure — P2-03 (2026-09-13)

P2-03 已完成通用 artifact/outbox/LanceDB/index_entries 可恢复闭环，报告见
[P2-03 报告](reports/P2-03-artifact-outbox-index-submission.md)。新增连续 migration
`0003_artifact_outbox_index.sql`，**schema_version 推进到 3**；0001/0002、冻结 ADR/JSON schema、
依赖、worker 与打包入口均未改。

`Scheduler.submit` 现在先核对 lease/revision/processor/device，再受控发布 structured/binary/embedding
产物，并在一个 SQLite 事务写 artifact、embedding outbox、PENDING_REPAIR index entry 与 job 终态。
LanceDB 0.38.0 使用原生 merge 幂等提交；只有 SQLite 确认后才显示 READY。删除确认、Lance 已写而
SQLite 未确认、晚到旧 projection、artifact 回收/再执行和引用保护都有具名恢复用例。
`schedule_projection_refresh` 已为 P2-03A 的 CatalogFilterRevision 同事务投影刷新提供稳定实现入口。

| Check | Result |
|---|---|
| `uv run pytest -q tests/unit tests/integration` | exit 0，433 collected，432 passed / 1 skipped |
| P2-03 专项 | exit 0，17 passed（artifact、提交/重放、migration/repair） |
| `uv run ruff check .` / `git diff --check` | clean / clean |
| `uv lock --check` | exit 0，`Resolved 95 packages` |
| C 级 | 未触发：未改 worker、正式环境、打包入口、依赖或新 package-data 目录 |

## Latest place-core closure — P2-03A (2026-09-13)

P2-03A 已实现地点核心闭环并通过 D 级验收，报告
[P2-03A 报告](reports/P2-03A-place-core-gps-assignment-api.md)。起点 HEAD `b9b5312`（P2-03），
工作树干净。

新增连续 `catalog/migrations/0004_place_core.sql`：**`schema_version` 推进到 4**，0001/0002/0003
未改；新增 `places`/`place_aliases`/`place_external_refs`/`asset_geo_facts`/
`asset_place_assignments`（+只追加 history）/`asset_place_exclusions`/`place_operations`
（+items）/`catalog_filter_revision`（+events）/`place_suggestion_revision`，并把 SQLite 能表达的
PK/FK RESTRICT/枚举/范围/模式组合全部下沉（含“禁止物理删除地点”“历史行只允许
ACTIVE→SUPERSEDED”“`SOURCE_UNAVAILABLE` 不得成为当前事实”）。

新增 `src/photo_search/places/**`（目录、层级矩阵与环校验、别名、人工 assign/unassign/restore_auto、
排除、幂等/批量 500/补偿撤销、CatalogFilterRevision 与投影刷新、纯 SQLite 筛选/覆盖）与
`src/photo_search/geo/**`（版本化 `non_model + CPU_PREFERRED` GPS 提取器、`VALID/ABSENT/INVALID/
UNSUPPORTED_CRS` 分支、受控读取 + SHA/stat 双重复核、确定性 structured GeoFact）。
`Scheduler.submit` 在既有事务内落当前事实；`/api/v1` 新增 10 条地点路径（OpenAPI 14 → 26），
`GET /assets` 支持 `place_ids/include_descendants/place_state`。本任务未改 ADR-0001～0005、
冻结 JSON schema、依赖、`workers/**` 或打包入口。

| Check | Result |
|---|---|
| `uv run --no-sync pytest -q -p no:cacheprovider tests/unit tests/integration` | exit 0；`--collect-only` 计 481 例；进度行 1 个 `s`，即 480 passed / 1 skipped |
| 本任务专项（5 个新增 + 3 个版本耦合文件） | exit 0，72 passed（新增 48 例：place_directory 14、geo_exif 12、geo_extract 12、place_api 5、place_migration 5） |
| `uv run ruff check .` / `git diff --check` / `uv lock --check` | clean / clean / exit 0（95 packages，依赖未变） |
| v3→v4 迁移 | 备份内 `schema_version=3` 且无地点表；单事务推进到 4；旧行逐列不变；注入损坏 0004 整体回滚；缺件 `SCHEMA_INCONSISTENT`；过新库零写 |
| GPS | `(0,0)` 合法；南/西半球；非法分母/NaN/Inf/越界/未知 datum 分支；离线与读中变化 → `BLOCKED` 且**不覆盖**上一条事实；迟到 lease 零事实副作用 |
| 人工控制 | assign→unassign→重启→restore_auto→再 assign 的锁/origin/排除/revision 逐项正确；无任何自动写路径；批量 500 全原子、501/0/重复被拒 |
| 撤销与版本 | 无冲突 undo 递增 revision；目标被后续编辑整批 `PLACE_OPERATION_NOT_UNDOABLE` 且历史不删；触发矩阵与同事务 outbox 投影刷新实测；注入失败全回滚 |
| 筛选与隐私 | 后代展开、同名、无地点、manual_unassigned、重复位置去重、与 source AND；`suggested` 在无解析器时确定为空；精确坐标只在会话保护 `/geo`，日志/错误/诊断/outbox/Lance 无坐标 |
| C 判定 | 按卡**不触发**（未改 workers/打包入口/依赖/新增 package-data 目录）；仍额外执行真实 onedir 构建 + `packaged_api_smoke.py`（`status=ok`、`problems=[]`、startup 1.8 s），并审计 bundle：`photo_search.places.*` 与 4 个 migration 已入包，`geo.*`/`scheduler.engine` 未入包（应用尚未接调度器，见报告 §9） |

## Latest dataset/resolution closure — P2-03B (2026-09-14)

P2-03B 已实现离线地名包与 suggestion-only 附近城市解析并通过 D 级验收，报告
[P2-03B 报告](reports/P2-03B-offline-name-dataset-and-nearby-city-suggestions.md)。
起点 HEAD `d7229be`（P2-03A），工作树干净。

新增连续 migration `catalog/migrations/0005_place_dataset_resolution.sql`：**`schema_version`
推进到 5**，0001～0004 未改。新增 `geo_datasets`/`geo_dataset_files`/`active_geo_dataset`
（单例，未安装时 `dataset_id IS NULL`）/`place_resolutions`（逻辑输入唯一）/
`place_resolution_candidates`（固定 `SUGGESTION_ONLY` + `NEARBY_CITY_POINT` + `DISTANCE_ONLY`）；
包内容不可变、`STAGED→VALIDATED→ACTIVE→RETAINED`（含 RETAINED→ACTIVE 回退）与机器产物只增不改
都由 trigger 固定。新增 `geo/dataset.py`（manifest/校验/确定性 builder/距离）、
`geo/resolver.py`（`geo-nearby-city@1`、`non_model + CPU_PREFERRED`、结构化 artifact 承载、
`persist_resolution`）、`places/datasets.py`（导入/校验/激活/回退/清理保护 + 数据集地点物化）、
`places/suggestions.py`（可见候选与独立 suggestion revision）；`Scheduler.submit` 在既有事务内落
resolution；`/places/datasets`、`/assets/{id}/place` 建议状态、`/assets` 的
`suggestion_revision` 绑定与 `origin=CONFIRMED_SUGGESTION` 已接入既有 session/Origin/CSRF 边界。
显式本地工具 `tools/places/manage_dataset.py` + 可验证的 `tools/places/examples/**`（不联网、
不改依赖、未改 `workers/**`、未改打包入口、未新增 package-data 目录）。

| Check | Result |
|---|---|
| `uv run --no-sync pytest -q -p no:cacheprovider tests/unit tests/integration` | exit 0；`--collect-only` 计 546 例；进度行 1 个 `s`，即 545 passed / 1 skipped |
| 新增专项 6 个文件 | 65 passed（manifest 23、lifecycle 13、resolver 15、place-dataset-api 7、migration 5、tool 2） |
| `uv run ruff check .` / `git diff --check` / `uv lock --check` | clean / clean / exit 0（95 packages，依赖未变） |
| v4→v5 迁移 | 备份内 `schema_version=4` 且无 5 张新表；单事务推进到 5；旧行逐列不变；注入损坏 0005 整体回滚；缺件 `SCHEMA_INCONSISTENT`；过新库零写；非法状态跳转/内容改写/产物改写全部被拒 |
| 包生命周期 | 源包→数据根 staged 目录（含 fsync）→VALIDATED→ACTIVE；提交前崩溃回滚（指针/状态/revision 不变）；被篡改的已校验包激活被拒且旧指针不变；删/改活动包 → `missing`/`invalid` 且解析 `PLACE_DATASET_MISSING`/`PLACE_DATASET_INVALID`；rollback 重新校验目标包并成功；活动/被 resolution 引用/作为回退目标的包清理返回 `PLACE_REFERENCED`；无引用 staged 包可删除 |
| 解析分支 | `VALID`→候选（`(distance,dataset_place_id)` 排序、上限 3/10、边界含等号）；`ASSUMED_WGS84`→候选但 `assumed_datum=true`/`requires_confirmation=true`；`ABSENT`/`INVALID`/`UNSUPPORTED_CRS`/海域/`(0,0)`→成功空结果；同名城市可区分且不合并；无包/缺包只 BLOCKED，人工创建/归属/筛选/操作历史继续可用 |
| 人工优先与版本 | 无任何机器路径写 `asset_place_assignments`；晚到解析在人工加锁后提交仍不改 assignment（`control_revision_seen=1`）；可见集合变化才 +1 suggestion revision（重放/空结果不变），`catalog_filter_revision` 全程不受数据包与解析影响；`origin=CONFIRMED_SUGGESTION` 的归属在重扫、切包与回退后保留 |
| 故障注入 | 解析事务中断 → resolution/候选 0 行、revision 仍 0、job 仍 RUNNING；`artifacts=[]` 的合格结果仍可提交；GPU 缺失（capability 只有 cpu）时解析/建议/人工写全部正常 |
| API 与隐私 | `/places/datasets` 无包时 `unavailable` 且不伪造 ACTIVE；`/assets/{id}/place` 只在 `/geo` 之外无坐标、无绝对路径；`place_state=suggested` 携带过期 `suggestion_revision` → 409 `PLACE_SUGGESTION_REVISION_STALE`；无 session 全部 401、跨 Origin 403 `ORIGIN_NOT_ALLOWED`、缺 CSRF 403 `CSRF_INVALID` 且零写入 |
| C 判定 | 按卡**不触发**（未改 workers/打包入口/依赖/新增 package-data 目录）；因“继承项：接入解析执行器必须复核导入图”仍执行真实 onedir 构建 + `packaged_api_smoke.py`（`status=ok`、`problems=[]`、startup 1.0 s，中文/空格路径），并审计 bundle：`geo.dataset`/`geo.exif` 与 `places.*` 已入包、0001～0005 共 5 个 `.sql` 在包内；`geo.extract`/`geo.resolver`/`scheduler.engine` 仍未入包（无常驻调度线程，见报告 §9） |
| 环境事件 | 打包 smoke 会把约 3.4 GB bundle 复制到 `%TEMP%`，C 盘被填满导致第二次复核以 `WinError 112` 失败；随后只清理本任务创建的 3 个 `%TEMP%\P2-03B 打包*` 目录（约 3.9 GB）并确认 C 盘恢复 3.76 GB 可用，C 级证据取自第一次成功运行 |

## Latest place-page closure — P2-03C (2026-09-14)

P2-03C 已实现静态地点整理页并通过 B 级、真实浏览器 smoke 与 C 级打包验收，报告
[P2-03C 报告](reports/P2-03C-place-page-and-manual-curation.md)。起点 HEAD `f4959a2`（P2-03B），
工作树干净。

新增依赖-free 页面 `GET /places`（`src/photo_search/web/{places.html,places.js,places.css}`，
由 `api/web.py` 从 package data 提供，不遮蔽 `/api/v1/places`），外壳导航新增入口；页面覆盖目录
keyset 分页与 `CATALOG_FILTER_REVISION_STALE` 提示、同名地点的完整祖先链 + 稳定 `place_id`、
创建/改名/别名（`expected_revision` 与 `PLACE_ASSIGNMENT_CONFLICT` 的 `current_revision` 提示）、
建议列表绑定 `suggestion_revision`、`SUGGESTION_ONLY` 候选的单条确认
（`origin=CONFIRMED_SUGGESTION`）与 ≤500 分批的原子批量动作（逐批结果、冻结 `asset_id` 快照）、
操作日志与撤销（含 `PLACE_OPERATION_NOT_UNDOABLE`/`PLACE_IDEMPOTENCY_CONFLICT` 提示）、
数据包状态面板与缺包降级。未改 ADR、migration、`schema_version`（仍为 5）、触发矩阵、错误码、
依赖或 `workers/**`；本轮未新增后端能力。

| Check | Result |
|---|---|
| `uv run --no-sync pytest -q -p no:cacheprovider tests/unit tests/integration` | exit 0，148.6 s；`--collect-only` 计 564 例，进度行 1 个 `s`，即 563 passed / 1 skipped |
| 专项（前端静态契约 + 页面路由 + 页面请求形状） | exit 0，68 passed（P2-03C 新增 18 例） |
| `uv run ruff check .` / `git diff --check` / `uv lock --check` | clean / clean / exit 0（95 packages，依赖未变） |
| 真实浏览器 smoke（内置浏览器 + 127.0.0.1:8732 + `%TEMP%` 合成夹具 14 张照片/14 GPS/14 resolution/12 建议） | 10 步逐条记录：落地导航、创建/改名/过期 revision、同名消歧与后代过滤、单条确认（`origin=CONFIRMED_SUGGESTION` 留存于结果区、列表 11→10）、批量确认（1 批 10 项原子提交、空目标前端拒绝且不发请求）、跨标签页 `PLACE_SUGGESTION_REVISION_STALE`、撤销成功 + `PLACE_OPERATION_NOT_UNDOABLE`、缺包 `missing`/`PLACE_DATASET_MISSING` 且人工路径可用、Tab/Enter 可达、页面文本无路径无坐标、访问日志 145 条全在 `/api/v1/*` 或 `/web/*` 且 `geo-hits = 0` |
| smoke 期间自修并复核 | 改名后编辑标题未刷新、后代过滤保留名称条件、单条确认结果随列表刷新消失（3 处，均在最终代码上重新核对） |
| C 级（页面是发行资源） | `build_onedir.py` exit 0（67.6 s，bundle 3,541,244,472 B，21 个 NVIDIA DLL）；bundle 内 `photo_search/web/` 含 `places.html`(12,329) `places.js`(61,297) `places.css`(2,558) 与既有三个资产、migrations 仍为 0001～0005；`packaged_api_smoke.py` exit 0 `status=ok`/`problems=[]`：`/places` 200 `text/html` 无外链无本机路径、`/web/places.js` 200、`/web/places.css` 200、外壳链接 `/places`、startup 1.0 s |
| 磁盘与清理 | 只删除本任务创建的 `%TEMP%\P2-03C 打包页面验收`（3,541,874,021 B），C 盘 2.56 GB → 5.87 GB；其中应用用 `icacls` 收紧过 DACL 的 `runtime\session.json`（约 1 KB）在沙箱内无法删除而残留 |
| C 判定 | 依赖/`workers/**`/打包入口未变、未新增 package-data 目录（按卡不触发）；因页面是发行资源仍执行真实 onedir 构建 + 打包 smoke + bundle 审计 |

未验证项（报告 §9）：本次内置浏览器的 `viewport` 覆盖没有改变实际窗口，640×900 像素级证据用
「0 个元素内部横向溢出 + `min(100%, …)` 网格」代理；批量接口没有 `origin` 字段，批量确认记录
`MANUAL`（要保留 `CONFIRMED_SUGGESTION` 需先更新契约）；`PLACE_OPERATION_NOT_UNDOABLE` 的
「目标被后续编辑」分支在页面上不可达（由 API 测试覆盖）；页面不触发后台解析；无 DOM 自动化；
`GET /assets` 与建议列表仍非快照隔离读。

## Latest search/fts closure — P2-04 (2026-09-14)

P2-04 已实现文件名／有效地点 FTS 与改名重投影并通过 B 级、D 级故障注入与 C 级打包验收，报告
[P2-04 报告](reports/P2-04-fts-and-rename-reprojection.md)。起点 HEAD `6cc75a9`（P2-03C），工作树干净。

新增连续 `catalog/migrations/0006_search_fts.sql`：**`schema_version` 推进到 6**，0001～0005 未改。
新增 `src/photo_search/search/**`（`tokens.py` 版本化分词与受控 MATCH、`documents.py` 派生文档与
受影响集队列、`query.py` 绑定谓词与滞后摘要）。`search_documents` 以 `asset_id` 为主键，六列文本
按主架构 §4.3 分开，本任务只写 `filename_text`（PRESENT 位置的 basename）与 `place_text`
（`MANUAL_ASSIGNED` 的有效地点 + 完整祖先链 + ACTIVE 别名，含数据包别名）；FTS5 是外部内容表、
`rowid = asset_id`，由触发器在同一事务维护（级联删除也清理索引）。受影响集由触发器在事实变更的
同一短事务写入 `search_projection_queue`（文件名、归属、地点改名/退休、别名增改，含递归子树展开），
重投影按批派生，`GET /assets?text=` 先做有界 catch-up 再 MATCH 并返回
`search={state SYNCED|LAGGING, queued_assets, processed_assets, documents, tokenizer_version,
fts_generation_id, terms, match_expression}`；`GET /capabilities` 新增只读 `search` 投影摘要。
地点条件仍完全由 SQLite 权威过滤，文本命中不证明拍摄地。

| Check | Result |
|---|---|
| `uv run --no-sync pytest -q -p no:cacheprovider tests/unit tests/integration` | exit 0；`--collect-only` 计 **599** 例，进度行 1 个 `s`，即 **598 passed / 1 skipped** |
| 新增 5 个测试文件 | **35 passed**（tokenizer 11、migration 7、document 8、projection 5、api 4） |
| `uv run ruff check .` / `git diff --check` / `uv lock --check` | clean / clean / exit 0（95 packages，依赖未变） |
| v5→v6 迁移 | 备份内 `schema_version=5` 且无 7 张新表；单事务 `applied=(6,)`；旧行逐列不变；注入损坏 0006 整体回滚；缺 `search_projection_queue` → `SCHEMA_INCONSISTENT`；过新库 `SCHEMA_TOO_NEW` 零写 |
| FTS 一致性 | 插入/更新/删除后无孤儿行；改名后旧词条 0 命中、新词条 1 命中；删除资产级联清空索引；`integrity-check` 通过；重扫改名只留新名称 |
| 重投影与恢复 | 改名在同一事务持久记录子树受影响集、`catalog_filter_revision` 恰好 +1、`suggestion_revision` 不变；批次中途注入异常 → 整批回滚、重跑无重复无遗漏；`LAGGING/SYNCED` 信号与权威 SQLite 过滤同时正确 |
| 候选隔离与 MATCH 边界 | 只有候选的资产 `place_text` 为空且候选地名 0 命中；空/特殊字符/超长查询 → 422 `VALIDATION_ERROR`；中英数字混排、全半角、显式短语、停用词按报告 §3 策略逐条断言 |
| C 级（改了应用导入图 + 新 migration） | `build_onedir.py` exit 0（约 59 s，bundle 3,541,272,714 B）；bundle 内 migrations 含 0001～0006，归档含 `photo_search.search{,.documents,.query,.tokens}`；`packaged_api_smoke.py` exit 0 `status=ok`/`problems=[]`，新增 `GET /assets?text=照片` → 200、命中 `[1]`、`state=SYNCED`、`fts_generation_id=fts-v1`、startup 1.0 s |
| 磁盘与清理 | smoke 工作目录放在 D 盘（`dist\p2-04-smoke`）以避开 C 盘压力；结束后只删除本任务创建的该目录（约 3.5 GB），其中 `runtime\session.json`（约 1 KB，应用收紧过 DACL）在沙箱内无法删除而残留 |

未验证项（报告 §10）：单次请求最多追赶 2000 个资产（更大规模改名需后续请求或 P2-05 后台追赶）；
`rebuild_all` 尚未接 CLI/调度；`filename_text` 只取 PRESENT 位置（整库离线资产按名称检索不到）；
同一资产多 PRESENT 名称之间存在短语跨界理论命中；停用词表为固定小表且「只含停用词」按 422 拒绝；
`search` 是加性响应字段（不带 `text=` 时只有显式 `"search": null`）；候选链路为直接写行（解析器由
P2-03B 验收）；前端尚未接 `text=`（无需浏览器 smoke）；`GET /assets` 仍非快照隔离读。

## Latest retrieval closure — P2-05 (2026-09-14)

P2-05 已实现双通道并行召回、RRF 融合、权威候选预过滤与投影滞后补查，并通过 B 级完整测试与
确定性检索对照验收，报告
[P2-05 报告](reports/P2-05-vector-fts-fusion-and-authoritative-topup.md)。起点 HEAD `fdfd13b`
（P2-04），工作树干净。

新增 `src/photo_search/search/**`（`fusion.py` 纯函数 RRF、`embedding.py` 查询侧
`EmbeddingAdapter` 切片、`retrieval.py` 权威谓词/两通道/补查、`service.py` 服务层编排）。
文本通道复用 P2-04 的 `build_match` + `search_documents_fts`（`bm25, asset_id`），向量通道复用
P2-03 的 `LanceVectorIndex`（新增加性 `search`/`search_candidates`，`asset_id IN` 下推预过滤）；
每通道有界 `DEFAULT_TOP_K=200`，向量候选按 CONFIRMED `index_entries` ∩ 权威条件每块 1000 个
keyset 扫描后精确归并。融合公式 `Σ weight/(60+rank)`、权重相等、去重、排序
「score 降序 → 命中通道数降序 → asset_id 升序」。查询必须显式绑定 `space_id`+`generation_id`+
维度（否则结构化拒绝），文本只编码一次；`catalog_filter_revision` 变化在返回前复核并标注
`FILTER_REVISION_CHANGED`。补查路径把「绑定 generation 下没有已确认投射」的权威资产显式列进
`pending_semantic`，因此改名/新归类后第一次查询即命中，向量未确认时 `matched_by` 只记 `fts` 且
`semantic_ready=false`。未改 migration（`schema_version` 仍 6）、ADR/冻结 JSON schema、依赖、
`places/**`、`ingestion/**`、`workers/**`、打包入口、前端；未接 API，故按卡不触发 C 级。

| Check | Result |
|---|---|
| `uv run --no-sync pytest -q -p no:cacheprovider tests/unit tests/integration` | exit 0；`--collect-only` 计 **618** 例，进度行 1 个 `s`，即 **617 passed / 1 skipped** |
| 新增 4 个测试文件（+ 共享夹具） | **19 passed**（fusion 6、channels 5、authority 5、topup 3） |
| `uv run ruff check .` / `git diff --check` / `uv lock --check` | clean / clean / exit 0（95 packages，依赖未变） |
| 确定性通道与融合 | 假 `EmbeddingAdapter` + 真 LanceDB generation：FTS `[sunset]`、向量 `[sunset, IMG_0001, 西湖]`（距离 `0.0/0.02/2.0`）、融合 `[sunset, IMG_0001, 西湖]`；同一查询两次 `json.dumps` 字节一致；查询文本只编码一次 |
| 过滤正确性 | 后代展开/`include_descendants=false`/`place_ids` OR/`place_state` 与原语义一致；`place_ids=[空地点]` 时两通道均 0 行（全库最近邻不可达，证明非「Top-K 后过滤」）；非法 `where()` 列 → 结构化 `FILTER_UNSUPPORTED` 且不回退未过滤检索；空候选集不打开引擎 |
| 补查与绑定 | 文本命中但向量未确认的资产仍出现并被 `pending_semantic` 指名（`PENDING_REPAIR`/`pending_events=1`/`in_ranked_hits`）；未知 generation/foreign space/错误维度/半绑定/非法 `top_k` 全部结构化拒绝；旧 READY generation 仍可查，新 PENDING generation 向量通道为空且不宣称新照片已入语义检索 |
| P2-03 崩溃窗口联测 | `after_lance` 注入后引擎有行、outbox 零 CONFIRMED、向量通道为空；重启重放同一逻辑事件成功且不产生第二条逻辑向量，之后才进入向量通道 |
| 既有不变量与隐私 | 两次查询前后 `schema_version=6`、两个 revision、`outbox`/`entries`/`documents` 行数与每个 `assets.created_at` 逐项不变；响应无本机路径、无坐标、无 `request_id`；既有测试原样通过 |

未验证项（报告 §11）：候选分块扫描的规模/延迟只在 ≤4 资产夹具上实测 1 个块（`MAX_VECTOR_BLOCKS=1000`
只是安全阀，触发时如实报 `vector_scan.truncated`）；无真实模型与人工标注，不声称召回质量；查询向量
归一化由 processor 负责；混入外来 space 的拒绝路径未构造真实注入；中查询并发变更只标注状态、不自动
补新命中；纯筛选查询给有界权威列表（≤1000 个 id，`top_k` 只约束通道）；`pending_projection_summary`
尚未接入 `/capabilities`。`catalog/errors.py` 新增了一个 extra 码常量 `FILTER_UNSUPPORTED`（本文件
外唯一改动，见报告 §11.1），暂无 HTTP 映射，P2-06A 接入 API 时补齐。

## Latest search-protocol closure — P2-06A (2026-09-14)

P2-06A 已实现搜索协议、搜索会话/游标与 profile 原子激活并通过 B + C + D 级验收，报告
[P2-06A 报告](reports/P2-06A-search-api-session-cursor-and-profile-binding.md)。起点 HEAD `27cb006`
（P2-05），工作树干净；实现提交即承载本行与报告的这次提交（未 push；哈希用
`git log -1 --format=%H -- docs/reports/P2-06A-search-api-session-cursor-and-profile-binding.md` 读取）。

新增 `src/photo_search/search/{contracts,sessions,profile}.py` 与连续 migration
`catalog/migrations/0007_search_profiles.sql`：**`schema_version` 推进到 7**，`0001`～`0006` 未改。
`SearchRequest`/`SearchResult` 是 `p2-contracts.v1` 的不可变信封（拒绝未知字段、只做形状校验、`text`
原样交给 P2-04 的 `build_match`）；`POST /api/v1/search` 首屏跑一次 P2-05 检索并把 ≤1000 条**有序候选及其
名次来源**冻结进**进程内**会话，续页只按 `base64url("s1:<session>:<offset>")` 的不透明游标切片，并在每页
返回前按当前 SQLite 权威条件复核该页 id（被排除的行不出现在页里，offset 照常前进）。过期矩阵：
他会话/他查询/他筛选/篡改/越界 → 422 `INVALID_CURSOR`；TTL 过期或进程重启 → 409
`SEARCH_SESSION_EXPIRED`；绑定变化 → 409 `PROFILE_GENERATION_MISMATCH`；`catalog_filter_revision`
变化 → 409 `CATALOG_FILTER_REVISION_STALE`（`place_state=suggested` 另绑 `suggestion_revision`）。
`search_profiles` 创建后不可变（UPDATE 触发器拒绝），单行 `active_search_profile` 指针与
`index_generations.is_active` 在**一个**事务内核对并联动；激活要求引用的 generation `READY`、
`space_id`/`processor_id` 与 profile 一致、FTS 世代与冻结 RRF 配置可服务、P2-02 的 runtime 健康且存在
该 processor 的专项 evidence。`GET /api/v1/search-profile` 与
`POST /api/v1/search-profile/activate` 已接入既有 session/Origin/CSRF 边界；`/capabilities` 的
`search` 块加性补充 `active_profile_id`/`active_image_generation_id`/`active_fts_generation_id`/
`pending_projection`/`query_encoder_available`。本卡未改 ADR-0001～0005、冻结 JSON schema、依赖、
`places/**`、`geo/**`、`ingestion/**`、`workers/**`、打包入口与前端；除卡内清单外仅加性改了
`api/context.py` 与 `app.py`（进程内会话宿主 + `encoder_factory` 注入缝，见报告 §1）。

| Check | Result |
|---|---|
| `uv run --no-sync pytest -q -p no:cacheprovider tests/unit tests/integration` | exit 0；`--collect-only` 计 **670** 例，进度行 1 个 `s` → **669 passed / 1 skipped** |
| 本任务 4 个新文件 | **52 passed**（contracts 23、sessions 5、profile 迁移 6、API/协议 18） |
| `uv run ruff check .` / `git diff --check` / `uv lock --check` | clean / clean / exit 0（95 packages，依赖未变） |
| v6→v7 迁移 | 备份内 `schema_version=6` 且无两张新表；单事务 `applied=(7,)`；旧 8 组行逐项不变；注入损坏 `0007` 整体回滚且无半成品；缺件 `SCHEMA_INCONSISTENT`；过新库零写；不可变触发器/FK RESTRICT/`state_id` CHECK/非法 `fusion_config` 形状逐条拒绝 |
| 激活事务 | 8 类拒绝（`PROFILE_NOT_FOUND`/`GENERATION_NOT_READY`/`CAPABILITY_MISSING`/`RUNTIME_UNHEALTHY`/`QUALIFICATION_MISSING`/`FTS_GENERATION_UNSUPPORTED`/`FUSION_CONFIG_UNSUPPORTED`/`GENERATION_SPACE_MISMATCH`）全部 409 且指针与 `is_active` 零变化；`activation_hook` 注入异常后两者全回滚且旧 profile 仍可查询；重复激活 `changed=false` |
| 会话/游标对照 | 同一查询两次不翻页逐项一致；page_size=2 走完 3 个候选无重复无遗漏、`rank` 连续、与单页同一查询顺序一致；TTL/重启 → `SEARCH_SESSION_EXPIRED` 后重搜得到完整第一页；翻页期间地点归属 +1 → 续页过期且重搜补回新命中（`matched_text=["place_text"]`） |
| C 级 | 真实 onedir 构建（exe 35,537,192 B）+ `packaged_api_smoke.py`（D 盘工作目录）：`status=ok`、`problems=[]`、startup 1.01 s；PYZ 内确认 `photo_search.search.{contracts,sessions,profile,service,retrieval}` 入包、`_internal` 内 `0007_search_profiles.sql` 存在；smoke 新增「未激活 profile + 显式绑定 `POST /search` → 200 `state=OK`」「带 cursor 续页同 session 无重复」「无 encoder 的文本请求 → 409 `QUERY_ENCODER_UNAVAILABLE`」「无 session → 401」，结束后删除本次 3.54 GB 工作目录（D 盘 160.98 → 164.29 GB 可用） |

未验证项（报告 §11）：真实查询编码器未接入应用进程（打包应用里文本查询以 409 拒绝，不降级为纯文本页，
`/capabilities.search.query_encoder_available=false`）；`search_profiles` 没有产品侧创建入口（测试/smoke
按 DDL 直写）；`fusion_config` 只按冻结 RRF 校验回显、`reranker_processor_id` 只存储；
会话仅进程内（多 worker 未测）；`page_size` 上界与 1000 候选上界只在小夹具上验证公式；`GET /assets`
与搜索页内复核都不是快照隔离读。

**C 级顺带发现的既有缺陷（未修复，需独立任务）**：`api/session.py::restrict_credential_file` 用
`icacls ... /grant:r "<user>:(R,W)"` 后凭据文件 DACL 只剩 `(R,W)`（实测 `icacls` 输出
`session.json TCC_SZ\28288:(R,W)`），**没有 DELETE 权限**；同一 `data_root` 第二次启动时
`os.replace(tmp, session.json)` 抛 `PermissionError: [WinError 5]`，打包应用直接启动失败（本次由连续两次
smoke 复现）。属 P1-04 既有行为，不在 P2-06A 允许修改面内，建议独立 **C1** 任务修复
（候选：授予 `(R,W,D)` 或先删后写），并加一条「同一 data root 连续两次启动」的打包 smoke 断言。

## Latest feedback/rerank closure — P2-06B (2026-09-14)

P2-06B 已实现显式相关性反馈与精排适配器接入并通过 B + C + D 级验收，报告
[P2-06B 报告](reports/P2-06B-search-feedback-and-rerank-adapter.md)。起点 HEAD `75d467b`（P2-06A），
工作树干净；实现提交即承载本行与报告的这次提交（未 push；哈希用
`git log -1 --format=%H -- docs/reports/P2-06B-search-feedback-and-rerank-adapter.md` 读取）。

新增连续 migration `catalog/migrations/0008_search_feedback.sql`：**`schema_version` 推进到 8**，
`0001`～`0007` 未改。`search_feedback` 只追加（`UPDATE`/`DELETE` 由触发器拒绝）：`request_id` 唯一
（幂等键）、`search_session_id`、`asset_id` FK RESTRICT、`rank ≥ 1`、`relevance IN
('RELEVANT','NOT_RELEVANT')`、`note ≤ 500`、`created_at ≥ 0`。`POST /api/v1/search/feedback` 新键
`201`、同载荷重放 `200`（同一 `feedback_id`、零写）、不同载荷 `409 FEEDBACK_IDEMPOTENCY_CONFLICT`
（零写）；未知 asset/非法枚举/越界/超长/缺字段 `422`；无 session `401`、缺 CSRF 或外来 Origin `403`。
`GET /api/v1/search/feedback` 是只读 keyset 分页（游标 `fb1`），只返回反馈自身字段。反馈**不改变**
`catalog_filter_revision`、`suggestion_revision`、`search_documents`、`assets.created_at`、向量/outbox
行数或可见性，被标 `NOT_RELEVANT` 的照片仍出现在下一次查询。

新增 `src/photo_search/search/rerank.py`（ADR-0003 §7 的 `spec()`/`score()` 边界，业务层不 import
ONNX/PyTorch）：`SearchRequest` 加性新增 `rerank: bool = false`，`SearchResult` 加性新增
`rerank_state`（`NOT_REQUESTED`/`OK`/`UNAVAILABLE`/`FAILED`）与 `reranked` 块（`state`/`reason`/
`processor_id`/`candidates`/`limit`/`timeout_ms`/`elapsed_ms`/`rows`/`error`）。`score()` 在 daemon
线程里最多等待 `timeout_ms`（默认 2000 ms，`Cancellation` 同步 deadline）；超时/异常/结果违约/spec
违约都返回**零行**并如实标注，基础 `hits` 与不请求精排时逐字段相同。`rows` 只含
`asset_id`/`rank`/`reranker_score`/`reranker_processor_id`，**没有** `score`，绝不与 RRF/BM25/距离
相加；候选头 `DEFAULT_RERANK_CANDIDATES=50`、硬上限 100，续页回显同一块。`reranker_processor_id`
非空的 profile 只有在健康 capability + 该 processor 的专项 evidence 齐备时才允许激活，否则 409
`RUNTIME_CAPABILITY_UNAVAILABLE`（`reason=RERANKER_QUALIFICATION_MISSING`）且零写（指针与 `is_active`
不动），注入中途异常整体回滚。本卡未改 ADR-0001～0005、冻结 JSON schema、`0001`～`0007`、依赖、
资格矩阵语义、`places/**`、`geo/**`、`ingestion/**`、`workers/**`、打包入口与前端；除卡内清单外仅
加性改了 `api/context.py` 与 `app.py`（`reranker_factory` 注入缝，见报告 §1）。

| Check | Result |
|---|---|
| `uv run --no-sync pytest -q -p no:cacheprovider tests/unit tests/integration` | exit 0；`--collect-only` 计 **719** 例，进度行 1 个 `s` → **718 passed / 1 skipped** |
| 本任务 5 个新文件 | **49 passed**（契约 19、精排边界 15、反馈迁移 6、反馈 API 3、精排/门禁 API 6） |
| `uv run ruff check .` / `git diff --check` / `uv lock --check` | clean / clean / exit 0（95 packages，依赖未变） |
| v7→v8 迁移 | 备份内 `schema_version=7` 且无 `search_feedback`；单事务 `applied=(8,)`；7 组冻结行（含已激活 profile 指针）逐项不变；损坏 `0008` 整体回滚且无半成品；缺件 `SCHEMA_INCONSISTENT`；过新库零写；唯一键/FK RESTRICT/枚举/`rank`/`note`/时间 CHECK 与两个只追加触发器逐条拒绝 |
| 反馈幂等与权威 | 新键 201 → 同键同载荷 200 同一行、行数 1 → 同键不同载荷 409 且原值不变；未知 asset 422（`field=asset_id`）；写入前后 revision/`search_documents`/`assets.created_at`/向量与 outbox 行数逐项相同，下一次同查询 `hits` 逐字段一致 |
| 精排门禁与失败面 | 无 evidence 的 reranker profile 激活 409 且指针/`is_active` 零变化；`activation_hook` 注入异常后两者全回滚；无适配器/抛异常/超预算三例 `rerank_state` 分别 `UNAVAILABLE`/`FAILED`/`FAILED`，`hits`/`channels`/`total_candidates`/`state` 与不请求精排时完全相同且 `rows=[]` |
| 分数分离与确定性 | `reranked.rows` 只含 `asset_id`/`rank`/`reranker_score`/`reranker_processor_id`；重复请求 `rows` 逐位相同；假适配器分数 `0.9/0.5/0.1` 复算一致而基础名次为 `3/2/1`（两套顺序可区分）；续页块与首页块完全相同 |
| 继承回归 | P2-06A 的会话过期/游标隔离/切换后过期/profile 激活用例**未改一行**并原样通过；唯一机械改动是 `test_p2_search_profile_migration.py` 的 `applied` 期望改为 `range(7, SUPPORTED+1)` |
| C 级 | 真实 onedir 构建（exe 35,557,186 B）+ `packaged_api_smoke.py`（D 盘工作目录）：`status=ok`、`problems=[]`、startup 1.0 s；PYZ 内确认 `photo_search.search.rerank` 入包、`_internal` 内 `0008_search_feedback.sql` 存在；smoke 新增「反馈 201 → 重放 200 同一 id → 不同载荷 409」「反馈列表 200」「无 session 401」「`rerank: true` → 200 `UNAVAILABLE`/`PROFILE_HAS_NO_RERANKER` 且基础 `hits` 不变」 |

未验证项（报告 §11）：真实精排/VLM 模型与 GPU 精排未接入，未测质量与延迟；在线学习/反馈驱动排序
未实现（反馈只记录）；超时是有界等待而非抢占式强杀；reranker 的 capability-missing/unhealthy 分支
因与嵌入处理器共用 runtime package 而不可达；反馈表规模/并发写入未测；精排候选上界只在 ≤4 候选夹具
上验证。凭据文件 DACL 缺陷新增一个可观察症状：同一缺陷让凭据文件对普通用户**不可删除**（smoke 后
残留两个 112 B 的 `session.json`，见报告 §9.1）。

## Latest search-page closure — P2-07 (2026-09-14)

P2-07 已实现搜索页、上传搜图、地点筛选与分项覆盖率并通过 B + 真实浏览器 smoke + C 级验收，报告
[P2-07 报告](reports/P2-07-search-page-upload-and-coverage.md)。起点 HEAD `e281925`（P1-07），
工作树干净；实现提交即承载本行与报告的这次提交（未 push；哈希用
`git log -1 --format=%H -- docs/reports/P2-07-search-page-upload-and-coverage.md` 读取）。

新增 `src/photo_search/web/{search.html,search.js,search.css}`（零依赖、无外链、无绝对本地路径、
窄视口可重排、键盘可达）与 `api/web.py` 的 `GET /search` 文档路由；`index.html` 与 `places.html`
互相加链，三个文档互通。页面只呈现后端字段：`hit_reason` 四个冻结取值逐行显示、
`matched_text`/`semantic_ready` 如实呈现、`matched_by` 只显示通道名次；**从不读取**
`hit.score`/`reranker_score`，没有百分比或「准确率」换算。必须显式呈现的非成功状态都各自成句：
缺查询编码器（`RUNTIME_CAPABILITY_UNAVAILABLE`/`QUERY_ENCODER_UNAVAILABLE`，文案写明「语义通道未运行」
且「这不是「没有结果」」）、缺图像编码器（`IMAGE_ENCODER_UNAVAILABLE`，声明没有用文本通道或「零命中」
冒充）、五类会话/游标/版本过期码、`search_projection=LAGGING`、`pending_semantic` 计数、
`candidates_truncated`/`channels.*.truncated` 与 `state=FILTER_REVISION_CHANGED`。

新增 `src/photo_search/search/upload.py` 与 `POST /api/v1/search/image`（multipart，无新增依赖：
自有的严格 RFC 7578 读取器）。上传图与 `SearchRequest` 同一套绑定语义（激活 profile 或显式
`space_id`/`image_generation_id`/`dimension`），复用同一条 `search_assets(..., query_vector=)`
通道/RRF/权威复核路径，没有第二套排序；向量通道无命中时**不**退化成权威列表。硬限制逐项服务端执行：
16 MiB 字节上限（`413 UPLOAD_TOO_LARGE`）、60 MP 像素上限（解码前按文件头判定，`422
UPLOAD_TOO_MANY_PIXELS`，两个新码的映射在 `api/errors.py`）、20 s 解码预算
（`409 DECODE_FAILED`/`reason=DECODE_TIME_BUDGET_EXCEEDED`）、与 P1-03 一致的格式白名单
（`409 FORMAT_UNSUPPORTED`）与解码失败分类（`409 DECODE_FAILED`），另加 JPEG DCT 解码长边 1600 与
EXIF Orientation 归一化。上传落盘在私有 `%TEMP%\photo-search-upload-*` 目录，成功与失败**两条路径**
都删除（用例对六条路径做调用前后目录集合相等的断言）；整条路径不新增 `assets`/位置/artifact/索引/
文档/反馈行，不改两个 revision 与 `assets.created_at`；会话摘要绑定上传字节的 SHA-256，因此不同图片
不能续用同一游标（`422 INVALID_CURSOR`）。缺图像编码器时输入限制仍然先执行，然后返回结构化 409。

分项覆盖率按卡的「优先复用」**不新增端点**：页面用 `GET /api/v1/capabilities`、
`/api/v1/places/coverage`、`/api/v1/stats` 与 `/api/v1/sources` 组装四项，
（1）文本投影覆盖（`search_documents`/`search_projection_queue`）、（2）语义索引覆盖
（CONFIRMED `index_entries`/`pending_projection`）、（3）地点覆盖（GPS 事实/已归类/未归类/待确认建议）、
（4）来源可用性，各自列出并写明「口径」，末行声明不合成「整体 AI 准确率」。本卡**未新增 migration**
（`schema_version` 仍为 8，`0001`～`0008` 未改），也未改 ADR-0001～0005、冻结 JSON schema、依赖、
`places/**`、`geo/**`、`ingestion/**`、`workers/**`、打包入口与 `api/context.py`；除卡内清单外新增
一个生产模块 `search/upload.py`（见报告 §1 的理由）。

| Check | Result |
|---|---|
| `uv run --no-sync pytest -q -p no:cacheprovider tests/unit tests/integration` | exit 0；`--collect-only` 计 **791** 例，进度行 1 个 `s` → **790 passed / 1 skipped** |
| 本任务新文件 | **57 passed**（上传边界 45、上传端点 12）+ 前端静态契约新增 12 例 + 路由新增 1 例 |
| `uv run ruff check .` / `git diff --check` / `uv lock --check` | clean / clean / exit 0（95 packages，依赖未变） |
| 真实浏览器 smoke（内置浏览器 + `%TEMP%` 夹具 + 真实端口） | 10 步逐步观察：303 落到 `/` 且三文档互链；无编码器 409 提示与「按当前条件重搜」；纯筛选 6 行（含缩略图、命中原因「仅筛选」、「语义就绪：否」、通道名次）；「文本投影仍在追赶」提示；每页 2 行 + 续页无重复；`place_state=assigned` 收窄到人工归属的 1 个资产；上传 → 409 `IMAGE_ENCODER_UNAVAILABLE`；四项覆盖率与口径；640×900 无横向滚动（网格 3 列）且 Tab 焦点顺序正确 |
| 浏览器 smoke 发现并修复的缺陷 | 上传失败时页面曾显示「上传图查询成功」（失败分支未标记失败）；已改为按结果布尔分支并加静态回归断言，新端口复测显示「上传图查询没有成功」 |
| C 级 | 真实 onedir 构建（exe **35,576,110 B**）+ `packaged_api_smoke.py`（D 盘工作目录）：`status=ok`、`problems=[]`、startup **1.0 s**；`/search`、`/web/search.js`（37,953 B）、`/web/search.css` 均在冻结包内可取，shell/places 均链接到 `/search`；上传端点 409 + 零新增资产 + 无 session 401 + 无路径；三项覆盖率只读接口 200 且不含路径/坐标；PYZ 内确认 `photo_search.search.upload` 入包 |

未验证项（报告 §11）：真实文本/图像编码器仍未接入，因此浏览器里看不到 `SEMANTIC`/`TEXT+SEMANTIC`
行与「语义索引仍在追赶」提示（其数据路径由集成用例覆盖）；覆盖率视图是客户端组装的三次只读请求，
没有服务端一次性视图或快照隔离；上传限制只在 32×24 与注入的小上限夹具上验证，未测真实 16 MiB/60 MP；
解码时间预算是测量后拒绝而非抢占式强杀；自有 multipart 读取器未与第三方客户端怪癖互操作验证；
分页/上界只在小夹具验证公式；页面无 DOM 自动化；搜索页仍非快照隔离读。

## Latest accepted verification

P1-06 的完整 D 级验收（`docs/tasks/P1-06.md` 要求的基础等级 + 6 类故障注入/恢复场景）：

| Check | Result |
|---|---|
| `uv run pytest -q tests/integration/test_catalog_identity_lifecycle.py` | exit 0, `16 passed`（本次新增用例） |
| `uv run pytest -q tests/unit tests/integration` | exit 0, `tests=337 failures=0 errors=0 skipped=1`（336 passed / 1 skipped，127.111 s） |
| `uv run ruff check .` / `git diff --check` | clean / clean |
| `uv lock --check` | exit 0, `Resolved 95 packages`（无依赖变更） |
| 场景：重复内容（同来源 3 份含中文/空格、>220 字符长路径、只读原文件 + 跨来源 1 份） | 1 个 `asset_id`、4 个位置、1 个内容寻址缩略图；删除同来源 1 份后其余 `PRESENT`、来源内 `assets` 仍为 1、产物字节/时间戳不变；删除另一来源唯一位置后资产行仍保留 |
| 场景：移动（`a.jpg` → `2024 年/照 片 一.jpg`） | 新位置 `PRESENT` + 旧位置 `MISSING`、`asset_id` 不变、位置数 +1、资产数不变、缩略图路径与 `(size, mtime_ns)` 完全不变（未重算） |
| 场景：替换（4 种） | 不同长度覆盖 → 新内容身份、旧资产仍 `ACTIVE`；覆盖成既有位置的内容 → 重绑该既有资产、不新增；同尺寸 + 还原 mtime → 仍重新 hash 识别；两位置互换 → 各自跟随内容身份；覆盖后中断 → 位置 `PRESENT` 但 `asset_id IS NULL`（待确认）、`RUNNING`、无缺失、`last_successful_scan` 不变 |
| 场景：断盘/离线（成功后拔盘 + 扫描中掉盘 + 枚举↔确认窗口） | 一律 `BLOCKED`、`locations_missing == 0`、`last_successful_scan` 不变；2 个已发布缩略图字节不变且 `media-status` 仍 `published == 2`；未生成的 preview 报 `source_missing`/`SOURCE_FILE_MISSING`；原文件仅被夹具移动、字节未变 |
| 场景：重映射（真实改名挂载点 + 单写事务更新 `root_path`） | 位置行零改写（`location_id`/`path_key`/`asset_id`/`size`/`mtime_ns`/`availability` 投影逐项不变）、重扫 0 缺失、资产数与产物不变；产品 remap 入口实测不存在（OpenAPI 14 条路径，无 `remap`） |
| 场景：重启/中断恢复 | 关闭服务 → 停机期间替换 1 个已绑定文件 + 删除 1 个未确认文件 → 新进程接管**同一** `scan_run`（`scans == 1`、`jobs == 1`）；`attempts == 1`、无重复位置、被替换位置绑定新内容、旧内容资产仍 `ACTIVE`、被删位置 `MISSING` 且 `asset_id IS NULL`；多次接管 `attempts` 0→1→2 单调 |
| 边界 | 0 文件/空子目录、1 文件、1001 文件（1001 位置 / 1001 资产 / `path_key` 全不重复 / 0 缺失）、只读原文件、中文/空格/长路径 |

Produced by DS-L on the P1-05 implementation and its P1-05B follow-up (see the P1-05 and P1-05B reports for
per-scenario evidence).

### P1-05B (front-end verification base, A level)

| Check | Result |
|---|---|
| `uv run pytest -q tests/unit/test_api_frontend.py` | exit 0, `46 passed` (42 existing + 4 new contract cases) |
| `uv run ruff check .` / `git diff --check` | clean / clean |
| `uv run python tools/diagnostics/frontend_smoke_fixture.py --photos 60 --bulk 2000 --corrupt --reset` | exit 0, 2061 synthetic files under `%TEMP%`, prints the source root, the real-port start command and the 10 expected observations |
| `--too-new` / `--offline` branches (separate `%TEMP%` dir) | verified: deletes the named original, reports the missing ones, builds a `schema_version = 2` catalog |
| Production code | untouched (`src/photo_search/**` unchanged); the only test edit is 4 added cases in `tests/unit/test_api_frontend.py` |
| New artifacts | `tools/diagnostics/frontend_smoke_fixture.py`, `docs/diagnostics/frontend-smoke-checklist.md` |

| Check | Result |
|---|---|
| `uv run pytest -q tests/unit tests/integration` | exit 0, 304 collected, `303 passed, 1 skipped` |
| focused P1-05 suites | exit 0, `50 passed` (`test_api_frontend.py` 42, `test_api_frontend_routes.py` 4, `test_api_time_order.py` 4) |
| `uv run ruff check .` / `git diff --check` | clean / clean |
| `uv lock --check` | exit 0, `Resolved 95 packages` (no dependency change) |
| P1-05 API/page scenarios 1-9 (public shell + static assets with traversal refusals, session `next` 303 + unsafe-`next` 422 without burning the credential, `time_desc` 26-asset paging with tie-break stability and cross-cursor `INVALID_CURSOR`, sources/scan flow, detail/offline with `SOURCE_FILE_MISSING`, bad-media codes, empty/single/multi-page/unicode/read-only boundaries, higher `schema_version`) | all executed on `%TEMP%` fixtures; mapped to named tests in report §5 |
| Recorded real-browser smoke (Codex in-app browser, real port, 2061-file `%TEMP%` fixture) | 10 steps observed: 303 landing, source registration without rendering absolute paths, live `RUNNING 14% → 65% → SUCCEEDED 100%`, 50 → 100 grid paging with lazy thumbnails, detail with 7 locations, `原文件不可用` badge with the thumbnail still shown, real `409 SOURCE_FILE_MISSING` from `/original`, `DECODE_FAILED` with the path withheld, narrow 640×900 viewport + Tab/Enter, and the `SCHEMA_TOO_NEW` notice (no empty list, no `-wal`/`-shm` written) |
| C-level packaged smoke (`tools/packaging/build_onedir.py` + `tools/diagnostics/packaged_api_smoke.py`) | build exit 0 (~62 s, 34,020,895 B); bundle copied to `%TEMP%\P1-05 打包页面验收\PhotoSearch 中文 空格`; `status=ok`/`problems=[]`: `GET /` 200 `text/html` `no-cache` with no external reference or local path, `/web/app.js` 200 (33,516 B) and `/web/styles.css` 200 inside the frozen bundle, unknown asset 404, printed link → 303 `Location: /`, plus the P1-04 session/scan/thumbnail/offline scenarios; startup 1.0 s |
| New finding (carried forward) | the media failure records embed a **repr-escaped** path (`C:\\Users\\…`) that `errors.Redactor` does not match, so `GET /assets/{id}` messages can still contain an absolute path; the page now withholds such messages (unit-asserted) and a C1/C2-reviewed server-side fix is recommended (report §8.1) |

P1-03 shipped contract: thumbnail `catalog-thumbnail@1` (long edge 320, JPEG q85, LANCZOS, never enlarged) and preview
`catalog-preview@1` (long edge 1600, JPEG q88, lazy). Layout
`<data_root>/{thumbnails,previews}/<processor_id>/<sha[:2]>/<sha[2:4]>/<sha>.jpg`, failure records under
`<data_root>/cache/media/...`, atomic same-directory write + fsync + `os.replace`. No schema change (still
`schema_version = 1`), no new dependency.

## Carried-forward limits / follow-ups

- **P2-06B**: 真实精排/VLM 权重未接入（`search/rerank.py` 只有 ADR-0003 §7 的边界与确定性假适配器，
  打包应用里 `rerank: true` 固定答 `UNAVAILABLE`/`PROFILE_HAS_NO_RERANKER`），因此没有 GPU 精排延迟或
  排序质量结论；反馈只被记录，**不**影响权威筛选、可见性或后续排序（在线学习属新的产品决策）；超时是
  有界等待（daemon 线程 + `Cancellation` deadline），**不是**抢占式强杀；reranker 的
  `CAPABILITY_MISSING`/`RUNTIME_UNHEALTHY` 分支因与嵌入处理器共用 `runtime_package_id` 而结构上不可达
  （可达且已实测的是 `RERANKER_QUALIFICATION_MISSING`）；`search_feedback` 的规模/并发写入未测，
  精排候选上界 50/100 只在 ≤4 候选夹具上验证。凭据文件 DACL 缺陷新增症状：普通用户无法删除该文件，
  因此 smoke 临时目录会残留 112 B 的 `session.json`（见 P2-06B 报告 §9.1）。
- **P1-06**: 对抗性验收**未发现**与 ADR-0002 冲突的真实缺陷，故无生产代码改动。三项残留边界已实测记录：
  (a) 若内容被替换但字节数不变且 `mtime` 被还原，扫描层的变化提示不会触发，此时若扫描被中断，位置会
  在**下一次完整扫描**前保留上一次观测（旧 `asset_id`/旧 size/mtime）；ADR-0002 §5.1 明确 size/mtime
  只是快速提示，且每次完整扫描都重算完整 SHA-256（`test_same_size_replacement_window_*` 已实测纠正），
  该窗口内 `last_successful_scan` 不前进、不会静默成功；若要缩短到「中断窗口内立即解除绑定」，属于改变
  缺失/身份判定边界，必须先交 C2 更新 ADR-0002。
  (b) 「断盘」用真实重命名/移走来源根目录模拟，**不是**真实外接盘/网络盘的卷级故障
  （`ERROR_NOT_READY`、USB 意外拔出、SMB 断链仍未在真实硬件上验证）。
  (c) 重映射只验证到夹具层（受控 `source_roots.root_path` 更新 + 重扫的身份/位置/产物不变）；
  `POST /sources/{id}/remap` 的**产品能力**（新根校验、来源重叠复查、映射审计、API 契约）仍缺，
  该项产品层入口标记为 `VALIDATION PENDING — 缺产品能力，不缺验收能力`。
  跨进程写者约束沿用 P1-02 记录，本任务未新增承诺。
- **P2-03B**: 真实 GeoNames/公开地名包**未使用**，只用极小合成子集验证格式、生命周期与数值分支，
  因此不声称任何真实城市覆盖/命中率；最近点解析是城市点线性扫描（无 RTree/网格索引），
  冷缓存会整包重算 SHA-256，大包规模与吞吐未测；切包/回退不自动重算既有 resolution
  （旧包产物仍作历史机器产物保留，`place_state=suggested` 只匹配活动数据集），自动刷新流程留给后续任务；
  坏包时既有建议仍可读、只有新解析阻塞；回退链只依赖包声明的 `previous_dataset_id`，工具导入不自动
  串联；数据集别名只新增不覆盖。`photo_search.geo.extract`/`geo.resolver`/`scheduler.engine`
  仍不在冻结包内（无常驻调度线程），接入后台解析的任务必须重跑 C 并复核导入图。
  C 级 smoke 会把约 3.4 GB bundle 复制到 `%TEMP%`，执行前需确认 C 盘余量（见 P2-03B 报告 §9）。
- **P1-04**: the local session lives in memory for the process lifetime (no TTL, no logout, no LAN auth); the
  bootstrap credential file's Windows DACL could not be verified here (`icacls` was denied in this sandbox, so
  the tightening stays best-effort); the API and the scan worker use two SQLite connections in one process and only
  a sequential-overlap window was stress-tested; media generation still runs on the event loop thread; `GET /stats`
  is O(assets). A C1 review of the packaging/migration-check fix is recommended; see report §6 and §8.
- No file-id move acceleration: a move is only proven by full SHA-256, so it leaves "new location PRESENT + old position
  MISSING" with the same `asset_id`. Explicitly allowed by the P1-02 card; revisit only with a sound identity argument.
- A file that disappears between enumeration and content confirmation still classifies as `FILE_READ_ERROR` → `BLOCKED`
  (conservative: never converted into a deletion conclusion). A re-run of the same `scan_run` completes it.
- Files whose `mtime` is later than the wall clock (clock skew, wrong camera date) skip the quiet-period check and rely on
  the stat pair around the hash only.
- Throughput is bounded by per-file short write transactions (2 observe/bind writes + throttled progress): ~375 files/s on
  4 KB synthetic files in this environment. Batching commits is a future tuning item that must not break ADR-0002 §8.
- Single-writer coordinator is in-process; cross-process writers use WAL + `BEGIN IMMEDIATE` + busy timeout but have not
  been stress-tested. Recovery always re-enumerates the whole source (no directory cursor), as ADR-0002 §5.3 allows.
- `assets.width/height/format` are still NULL: P1-03 delivers the orientation-normalised size and source format in the
  batch summary and the derived facts can be read from the artifact; persisting them needs a documented additive
  migration and a C2 decision (P1-03 report §2/§8).
- P1-03 refuses TIFF/RAW/HEIF/AVIF as `FORMAT_UNSUPPORTED` even though Pillow can read TIFF; multi-page, RAW and colour
  semantics are unverified, so no support may be claimed before a dedicated task verifies it.
- No colour management in the media path: ICC is not applied, CMYK/16-bit inputs are converted naively to RGB and
  transparency is composited on white.
- Media generation decodes one asset at a time on one thread and uses JPEG DCT-scaled decode (intermediate always at
  least the target long edge). A bounded decode pool per architecture §6.4 is still a P2 item.
- `catalog media-status` is an O(assets) diagnostic (one reachability probe per pending asset plus a walk of the current
  cache version); no incremental status index yet. Media batches are untested under cross-process concurrency (the
  catalog is never written by the media path; identical content-addressed artifacts rely on atomic replace).
- GTX 1660 6 GB remains **unverified on real hardware** (P4-07/P4-08 release gate). Model GPU execution is verified on the
  current RTX 4060 Laptop GPU; earlier P0 limits for InsightFace/RapidOCR, cold CUDA start and human-labelled semantic
  quality remain carried forward.
- Two historical P0 record inconsistencies remain recorded but unedited; see the P0-07 report §3.1.
- **P1-05 historical redaction gap is closed by P1-05A** (`b708847`); its earlier report remains historical evidence.
  The front end has no automated DOM tests (no Node/npm chain introduced); its behaviour
  rests on the static contract assertions consolidated by P1-05B in `tests/unit/test_api_frontend.py` plus the
  repeatable manual checklist `docs/diagnostics/frontend-smoke-checklist.md`. Narrow-viewport/200% zoom was
  proxied by a 640×900 viewport (no horizontal overflow,
  3-column reflow), not by real OS zoom. `time_desc` sorts by insertion time only (`taken_at` would need an
  ADR-0002 §7 additive migration + C2). A fully offline asset shows no relative path in the grid
  (`current_location` is null once every position is MISSING); the detail view still lists every position.

  两个历史非阻塞后续项均已完成，保留任务卡／报告供追溯（不再作为下一任务）：

  - `docs/tasks/P1-05B.md` — 前端静态契约断言集中化 + `%TEMP%` 夹具脚本 + 浏览器 smoke 核对清单
    （不引入 Node/npm），验收等级 A。**状态：PASS**，见
    [docs/reports/P1-05B-frontend-verification-base.md](reports/P1-05B-frontend-verification-base.md)。
  - `docs/tasks/P1-05A.md` — 本机路径脱敏补强（repr 转义与多拼写 + 未登记路径兜底），推荐
    **C1 / GPT-5.6 Terra / medium**，验收等级 **B**；唯一允许改动的生产文件是
    `src/photo_search/api/errors.py` 的 `Redactor`，页面守卫保留并加回归断言。**状态：PASS**；见
    [P1-05A 报告](reports/P1-05A-local-path-redaction.md)。
  - `taken_at`（EXIF 拍摄时间）排入 **P4-00A（C2 契约）→ P4-00B（DS-H 实现）**；
    通过 ADR-0002 §7 的增量迁移路径，不改变现有加入时间排序。

## Latest face closure — P3-02 (2026-09-14)

P3-02 已实现人脸检测/对齐/特征/独立候选索引/保守自动关联并通过 **B + D + C** 级验收，报告
[P3-02 报告](reports/P3-02-face-detect-align-index-association.md)。起点 HEAD `34db5e3`（P3-01 报告
提交），工作树干净。

新增 `src/photo_search/face/**`（P0-04 固定权重校验、SCRFD 640 画布检测 + 锚点/NMS、ArcFace 5 点相似对齐
112×112、512 维 L2 特征与三项质量口径、确定性 `face_id`、`face-document.v1` 结构化产物、同事务事实持久化、
独立人脸候选索引、按真实夹具校准的保守关联、受控 CLI）与连续增量迁移 `0010_face_core.sql`：
**`schema_version` 推进到 10**，`0001`～`0009` 未改；新增 `faces`（当前检测事实）、`face_embeddings`
（规范 512 维向量）、`persons`（未命名分组）、`face_person_links`（每脸至多一个有效归属 + 任意条建议）、
`face_association_queue`，以及**独立**的 `face_index_generations/_entries/_outbox`/`face_candidates`
（自己的世代与主键，绝不共用图片索引的资产级身份）。`/api/v1/capabilities` 新增只读 `faces` 段；
`photo_search.face.cli` 提供 weights/status/enqueue/run/index/group/detect。本任务未改 `workers/**`、
打包入口、依赖、前端资源、`geo/**`、`places/**`、`ingestion/**`，也未改动 P3-01 OCR 契约。

| Check | Result |
|---|---|
| `uv run --no-sync pytest -q -p no:cacheprovider tests/unit tests/integration` | exit 0；JUnit `tests=964 failures=0 errors=0 skipped=1`，297.6 s |
| 本任务新增用例 | 87 例（contract 27、geometry 17、association 12、migration 6、cli 9、lifecycle 16） |
| `uv run --no-sync ruff check .` / `git diff --check` / `uv run --no-sync uv lock --check` | clean / clean / exit 0（95 packages，依赖未变） |
| C 级 | 真实 onedir 重建（3.30 GB）+ `packaged_api_smoke.py --work-dir D:\PhotoSearchSmoke\P3-02-final`：`status=ok`、`problems=[]`、`startup_seconds=1`、`/capabilities` 200、`coverage_has_no_local_path=true`；bundle 内含 `0001`～`0010`（`0010` 与源码 SHA-256 一致），`faces` 段返回真值（`state=NOT_RUN`、`thresholds.merge=0.56`） |
| 真实夹具 | `tools/diagnostics/face_smoke_fixture.py --device cpu` exit 0：6 张资产/12 张脸/8 个未命名分组；宇航员与增亮变体同组（0.9723）、42% 缩放脸 `LOW/SMALL` 只出建议（0.7826）、同图两张同人为 1 成员 + 1 条 `SAME_IMAGE_CONFLICT`、六人合影 6 个不同分组、112 px 紧裁人像独立成组（验证"小于画布不放大"规则） |
| 阈值校准 | 用产品引擎复测并断言与 `contract.CALIBRATION` 一致：异人 28 对 max 0.2061；同人整图变化 40 对 min 0.8527；同人裁剪几何变化 32 对 min 0.6433 ⇒ `MERGE=0.56`、`SUGGEST=0.29`（推导公式写入契约，明确不是通用常数 0.45） |
| v9→v10 迁移 | 备份内 `schema_version=9` 且无 face 表；单事务推进；旧行（含 P3-01 OCR 行与队列行）逐列不变；注入损坏 0010 整体回滚；缺件 `SCHEMA_INCONSISTENT`；过新库零写 |
| D 级注入 | 权重缺件/哈希不符、租约到期接管与迟到结果、内容变化前后旧结果、来源不可用、重跑同内容不产生第二套脸/第二个分组、候选索引写而未确认窗口、人工优先、显式 GPU 不隐式转 CPU——全部零副作用或按设计阻塞 |

未验证项：真实 GPU 人脸推理（本机 venv 的 CUDA 会话因缺 `cublasLt64_13.dll` 搜索路径无法创建；打包
`PhotoSearch.spec` 已把 NVIDIA DLL 复制成 `nvidia_dlls/`，P0-06 亦在打包产物上验收过 CUDA，但本任务未在
GPU 上跑人脸）；真实年龄变化/遮挡/眼镜/强侧脸/低照度；真实家庭照片库规模、聚类纯度与代表上限调参；
候选索引为 SQLite 派生表而非 ANN；世代切换后的新旧脸映射。若要现在就补 GPU 证据，建议手动切
**C1 / GPT-5.6 Terra / medium** 做一次 GPU/DLL bring-up（不阻塞 P3-03）。

## Latest person-knowledge closure — P3-03 (2026-09-15)

P3-03 已实现人物命名、同名合并、旧 ID 重定向与补偿撤销并通过 **B + D + C** 级验收，报告
[P3-03 报告](reports/P3-03-person-naming-merge-redirect.md)。起点 HEAD `d4eae44`（P3-03 规则冻结），工作树干净。

新增 `src/photo_search/persons/**`（`nfc1:` 版本化 name_key、唯一 canonical 解析器、`PERSON_BUNDLE`
前值/后值快照、唯一合并用例与补偿、薄 facade）与连续增量迁移
`catalog/migrations/0011_person_knowledge.sql`：**`schema_version` 推进到 11**，`0001`～`0010` 未改；
新增 `person_operations`（request_id 幂等唯一、五类 type、单调且唯一的 operation_revision、UNDONE 必须带
`undone_by`）、`person_operation_items`（逐实体 before/after 快照与操作后 revision）、`person_aliases`
（人物内唯一、跨人物可同名，永不触发合并）、`face_assignment_controls` 与 `face_exclusions`（架构必需列
一字不多，保证 P3-02 的人工优先用例继续可用），并按 0009 的已验收范式重建 `search_projection_queue`
只加第六个 cause `PERSON_CHANGED`（0006/0009 触发器一字未改）。`rename_person` 与 `merge_persons`
调用**同一个** `_apply_merge`；语义 no-op 仍写 `COMMITTED` 操作但不推进任何 revision/outbox；
撤销是新 `UNDO` 操作，所有恢复的 revision 从当前值向前递增，目标被后续编辑时整次拒绝并列出
`entity_type/entity_id/reason`。`/api/v1` 新增 4 条人物路径（OpenAPI 31 → 35），`face_summary` 新增
`named_persons/unnamed_persons/aliases/pending_confirmation/recent_operations` 五个只读计数。
本任务未改 ADR-0001～0006、冻结 JSON schema、依赖、`workers/**`、`ingestion/**`、`geo/**`、
`places/**`、前端资源或打包入口。

| Check | Result |
|---|---|
| `uv run --no-sync pytest -q -p no:cacheprovider tests/unit tests/integration` | exit 0；JUnit `tests=1038 failures=0 errors=0 skipped=1`（提交后在已提交的树上复跑一次，327.7 s） |
| 本任务新增用例 | 74 例（contract 31、merge 9、operations 12、undo 10、api 5、migration 6、lifecycle 1） |
| `uv run --no-sync ruff check .` / `git diff --check` / `uv run --no-sync uv lock --check` | clean / clean / exit 0（95 packages，依赖未变） |
| 真实夹具 | `tools/diagnostics/person_smoke_fixture.py --device cpu` exit 0：4 资产/9 脸/8 个未命名分组；RENAME → MERGE_BY_NAME → UNDO → MERGE → UNDO 全部 `replayed=false`；人工未归类脸降为 `SUGGESTED/MANUAL_UNASSIGNED`、撤销后回到 `CONFIRMED/AUTO` 且人工模式行保留；`named=1 / unnamed=7 / aliases=0 / pending_confirmation=0 / recent_operations=5`；撤销后 3 张候选恢复，最终 `entries={CONFIRMED: 9}` |
| v10→v11 迁移 | 备份内 `schema_version=10` 且无 person 表；单事务推进；旧行（含 P3-01/P3-02 行与队列行）逐列不变；队列既有 `OCR_TEXT_CHANGED` 行保留且新 cause 可用；未知 cause 仍被拒；注入损坏 0011 整体回滚；缺件 `SCHEMA_INCONSISTENT`；过新库零写 |
| C 级 | 真实 onedir 重建 + `packaged_api_smoke.py --work-dir D:\PhotoSearchSmoke\P3-03-final`：`status=ok`、`problems=[]`、`startup_seconds=1.2`、`coverage_capabilities_status=200`、`coverage_has_no_local_path=true`；bundle 内含 `0001`～`0011`（`0011` 与源码 SHA-256 `0CD3DC42…A064325` 一致），打包后的 data root 实测 `schema_version=11` 且含 4 张新表 |
| D 级注入 | 幂等重放/载荷冲突、过期 revision、批量中途失败整体回滚、重定向环、`MANUAL_UNASSIGNED` 与活动排除（含指向源的排除）降级、合并后新增成员导致撤销整体拒绝、undo an UNDO、未知操作、no-op 零副作用——全部零写入或按设计拒绝 |

## GPU status

Model GPU execution is verified on this machine (RTX 4060 Laptop GPU / 8188 MiB / driver 616.56 / CUDA UMD 13.4):
Chinese-CLIP image and text towers run on `CUDAExecutionProvider` with CPU fallback disabled, the worker defaults to CUDA,
ViT-L/14 also ran fully on CUDA, and the packaged app reproduced the CUDA path from a Chinese/space path offline.

Still open for later phases: real GTX 1660 6 GB validation; bounded decode pool and thumbnail/subsample prep (P1-03);
scheduler handling of cold GPU start; explicit OCR device decision; InsightFace CPU-operator re-check; and a
human-labelled semantic-search holdout.

## Current task / next execution step

**P1-06 — 重复、移动、替换、断盘、重映射、重启验收：PASS.** 见
[P1-06 报告](reports/P1-06-identity-lifecycle-acceptance.md)。

**P2-01 — 通用处理任务、运行时与搜索配置契约：PASS.** 见
[P2-01 报告](reports/P2-01-processing-runtime-search-profile-contract.md)。

**P2-01A — 地点事实、人工控制、数据包与搜索契约：PASS.** 见
[P2-01A 报告](reports/P2-01A-place-contract.md)。

**P2-02 — 持久调度、设备资格与阻塞恢复实现：PASS.** 见
[P2-02 报告](reports/P2-02-persistent-scheduler-and-device-qualification.md)。

**P2-03 — 通用产物、outbox、幂等索引提交与修复：PASS.** 见
[P2-03 报告](reports/P2-03-artifact-outbox-index-submission.md)。

**P2-03A — 地点增量迁移、GPS 事实、目录、人工归属/撤销与纯筛选 API：PASS.** 见
[P2-03A 报告](reports/P2-03A-place-core-gps-assignment-api.md)（D 级，48 个新增用例）。

**P2-03B — 离线地名包、GPS 附近城市建议、版本/缺包/升级恢复：PASS.** 见
[P2-03B 报告](reports/P2-03B-offline-name-dataset-and-nearby-city-suggestions.md)
（D 级，65 个新增用例）。`schema_version = 5`，migration 连续 0001～0005；
依赖/`workers/**`/打包入口未改，`geo.extract`/`geo.resolver`/`scheduler.engine` 仍不在冻结包内。

**P2-03C — 静态地点页与人工整理交互：PASS.** 见
[P2-03C 报告](reports/P2-03C-place-page-and-manual-curation.md)（B 级 563 passed / 1 skipped + 真实
浏览器 smoke 10 步 + C 级打包 smoke）。`schema_version` 仍为 5，migration/ADR/依赖未改。

**P2-04 — 文件名/有效地点 FTS、受控 MATCH 与改名重投影：PASS.** 见
[P2-04 报告](reports/P2-04-fts-and-rename-reprojection.md)（B 级 598 passed / 1 skipped + D 级
投影故障注入 + C 级打包 smoke，35 个新增用例）。`schema_version` 推进到 6，migration 连续
0001～0006；ADR/冻结 JSON schema/依赖/`places/**`/`ingestion/**`/`workers/**`/打包入口未改。

**P2-05 — 图文 + FTS 并行召回、RRF 融合、地点候选预过滤与权威补查：PASS.** 见
[P2-05 报告](reports/P2-05-vector-fts-fusion-and-authoritative-topup.md)（B 级 617 passed / 1 skipped +
确定性检索对照 + P2-03 崩溃窗口联测，19 个新增用例）。`schema_version` 仍为 6（无新 migration）；
ADR/冻结 JSON schema/依赖/`places/**`/`ingestion/**`/`workers/**`/打包入口/前端未改，未接 API（按卡不触发 C）。

**P2-06A — 搜索协议、`POST /api/v1/search`、搜索会话/游标与 Profile 绑定：PASS.** 见
[P2-06A 报告](reports/P2-06A-search-api-session-cursor-and-profile-binding.md)（B 级 669 passed / 1 skipped +
C 级打包 smoke `problems=[]` + D 级迁移/激活/过期故障注入，52 个新增用例）。`schema_version = 7`，
migration 连续 0001～0007；ADR/冻结 JSON schema/依赖/`places/**`/`geo/**`/`ingestion/**`/`workers/**`/
打包入口/前端未改（仅加性改了 `api/context.py` 与 `app.py` 两处注入缝，见报告 §1）。

**P2-06B — 显式相关性反馈与精排适配器接入：PASS.** 见
[P2-06B 报告](reports/P2-06B-search-feedback-and-rerank-adapter.md)（B 级 718 passed / 1 skipped +
C 级打包 smoke `problems=[]` + D 级迁移/幂等/门禁故障注入，49 个新增用例）。`schema_version = 8`，
migration 连续 0001～0008；ADR/冻结 JSON schema/依赖/资格矩阵语义/`places/**`/`geo/**`/`ingestion/**`/
`workers/**`/打包入口/前端未改（仅加性改了 `api/context.py` 与 `app.py` 两处注入缝，见报告 §1）。

**P2-07 — 搜索页、上传搜图、地点筛选、命中原因与分项覆盖率.**

- Status: **PASS**（B 级 790 passed / 1 skipped + 真实浏览器 smoke 10 步 + C 级打包 smoke
  `problems=[]`，70 个新增用例）。见
  [P2-07 报告](reports/P2-07-search-page-upload-and-coverage.md)。交付 `/search` 页面、
  `POST /api/v1/search/image`（硬限制 + 临时文件双路径回收 + 同一条通道/RRF/权威路径）、
  地点筛选与四项分项覆盖率视图；`schema_version` 仍为 8，无新 migration。
- Task card: `docs/tasks/P2-07.md`（历史任务卡，保留供追溯）。

**P2-08 — 恢复、过滤、增量、后台竞争与地点人工锁/投影/缺包综合验收.**

- Status: **PASS**（B 级 801 passed / 1 skipped + D 级 11 条具名故障注入/恢复用例 + 真实浏览器闭环
  10 步；**未改任何生产代码**）。见
  [P2-08 报告](reports/P2-08-acceptance-matrix.md)。三条新测试文件：
  `tests/integration/test_p2_acceptance_{recovery,search,datasets}.py`（5 + 5 + 1 例）与共享夹具
  `tests/p2_acceptance_fixtures.py`。`schema_version` 仍为 8，migration `0001`～`0008`、ADR、
  冻结 JSON schema、依赖、`src/photo_search/**`、前端与打包入口均零改动。
  报告 §5 记录两处行为事实（地名包进程内 warm cache 边界；人工地点编辑先把向量条目降级为
  `PENDING_REPAIR` 的投影追赶语义），均不构成最小修复触发条件。
- Task card: `docs/tasks/P2-08.md`（历史任务卡，保留供追溯）。
- P2 阶段收口：报告 §6 明确区分「P2 已实现并验收」/「P2 未实现（真实编码器、产品侧 profile/
  generation 入口、模型升级状态机等）」/「排入 P3/P4 的欠项」。

P1-05A 已关闭脱敏缺口；P1-04 打包/迁移检查的建议复核保留到发行检查。P1-06 §5 product remap
排入 P4-05A/B，拍摄时间排入 P4-00A/B，HEIC 排入 P4-00C；均不是 P2-03C 的前置依赖。
P2-02 未验证项（无常驻调度线程、真实模型设备对接、跨进程并发、evidence 撤销、真实端口 smoke）
记录在 P2-02 报告 §10；P2-03A 未验证项（`GET /assets` 页面非快照隔离读、改名未接 undo）
记录在 P2-03A 报告 §9/§10；P2-03B 未验证项（真实 GeoNames 包未使用、最近点线性扫描、
冷缓存整包重校验、切包不自动重算、坏包时旧建议仍可读、`geo.resolver` 尚未进入冻结包）
记录在 P2-03B 报告 §10；P2-03C 未验证项（内置浏览器 `viewport` 覆盖未生效致 640×900 像素级证据
只到代理、批量接口无 `origin` 致批量确认记 `MANUAL`、`PLACE_OPERATION_NOT_UNDOABLE` 的「目标被后续
编辑」分支页面上不可达、页面不触发后台解析、无 DOM 自动化、非快照隔离读）记录在 P2-03C 报告 §9；
P2-04 未验证项（单次请求最多追赶 2000 资产的滞后、`rebuild_all` 未接 CLI/调度、
`filename_text` 只取 PRESENT 位置、多 PRESENT 名称的短语跨界、停用词固定小表、`search` 为加性字段、
候选链路直接写行、前端未接 `text=`、非快照隔离读）记录在 P2-04 报告 §10；P2-05 未验证项（候选分块
扫描的规模/延迟未测、`MAX_VECTOR_BLOCKS` 安全阀未在大库触发、查询向量归一化由 processor 负责、
混入外来 space 的拒绝路径未真实注入、中查询并发变更只标注不补命中、纯筛选查询的权威列表上界 1000、
`pending_projection_summary` 未接 `/capabilities`、`FILTER_UNSUPPORTED` 暂无 HTTP 映射）记录在
P2-05 报告 §11；P2-06A 未验证项（真实查询编码器未接入应用进程致打包应用的文本查询以 409
`QUERY_ENCODER_UNAVAILABLE` 拒绝而不降级、`search_profiles` 无产品侧创建入口、`fusion_config` 只按
冻结 RRF 校验回显、`reranker_processor_id` 只存储、会话仅进程内/多 worker 未测、`page_size` 与 1000
候选上界只在小夹具验证公式、搜索页内复核非快照隔离读）记录在 P2-06A 报告 §11；P2-06B 未验证项
（真实精排/VLM 模型与 GPU 精排未接入、在线学习与反馈驱动排序未实现、超时是有界等待而非抢占式强杀、
reranker 的 capability-missing/unhealthy 分支因共用 runtime package 不可达、反馈表规模/并发写入未测、
精排候选上界只在 ≤4 候选夹具验证）记录在 P2-06B 报告 §11；P2-07 未验证项（真实文本/图像编码器
仍未接入致浏览器里看不到 `SEMANTIC`/`TEXT+SEMANTIC` 行与「语义索引仍在追赶」提示、覆盖率视图为
客户端组装的三次只读请求且无快照隔离、上传限制只在 32×24 与小上限夹具验证、解码预算为测量后拒绝而
非抢占式强杀、自有 multipart 读取器未做第三方客户端互操作验证、分页/上界只在小夹具验证公式、
页面无 DOM 自动化）记录在 P2-07 报告 §11。

P2-08 未验证项（真实编码器/语义质量、浏览器侧 `SEMANTIC`/`TEXT+SEMANTIC` 行仍不可达、无 DOM 自动化、
会话仅进程内并以 `fresh_app`/`clear_content_cache` 在进程内模拟「新进程」、非快照隔离读、规模与
16 MiB/60 MP 上传上限未测、真实 GeoNames 包未使用、断盘仍以 `os.replace` 模拟而非真实 USB/SMB 卷级
故障、地名包进程内 warm cache 边界、产品 remap/profile/generation 入口与模型升级状态机缺口）记录在
[P2-08 报告](reports/P2-08-acceptance-matrix.md) §5/§6/§7。

P3-01 未验证项（真实复杂版面/手写/竖排/多语言与真实截图库规模、引擎去空格对数字段检索的保真度、
tokenizer 世代切换无法重新分词既有 OCR 文本、产品侧自动排队与常驻调度尚未接入、区域元数据尚无服务入口、
跨进程并发与大库吞吐未压测、真实 USB/SMB 断盘未验证）记录在
[P3-01 报告](reports/P3-01-ocr-text-regions-confidence-fts.md) §8/§10。

P3-02 未验证项（真实 GPU 人脸推理：本机 venv 的 CUDA 会话因 `cublasLt64_13.dll` 不在搜索路径而无法创建，
打包产物已带 `nvidia_dlls/` 但本次 smoke 的数据根没有固定权重因此未触发；真实年龄变化/遮挡/口罩/眼镜/强侧脸/
低照度；真实家庭照片库规模、聚类纯度与代表上限调参；候选索引为 SQLite 派生表而非 ANN；`MAX_FACES` 只在小夹具
验证；世代切换后的新旧脸映射未实现；质量阈值仅由 8 个真实脸的下界推出安全余量；跨进程并发与取消的抢占语义
未测）记录在 [P3-02 报告](reports/P3-02-face-detect-align-index-association.md) §14。

**P1-07 Windows 凭据 ACL 轮换修复为 PASS**：`restrict_credential_file` 仍移除继承并仅授予当前用户，
但由 `(R,W)` 改为 `(R,W,D)`，使原子 `os.replace` 能替换旧 `session.json`，临时 data root 也可由普通
用户回收。专项 31 passed、完整单元/集成套件 100% 无失败、Ruff/diff/lock clean；真实新 onedir 的同一
data root 第二启动 smoke 为 `status=ok`（新 token、health 200）且工作目录可完整删除。详见
[P1-07 报告](reports/P1-07-windows-credential-acl-rotation.md)。旧版本遗留的 `(R,W)` 文件不会被本任务
擅自接管或删除，仍可能需要所有者/管理员人工清理。

**P2-08 已 PASS**（B + D + 真实浏览器闭环，零生产改动），见
[P2-08 报告](reports/P2-08-acceptance-matrix.md)；P2 阶段到此收口。

## Latest OCR closure — P3-01 (2026-09-14)

P3-01 已实现 OCR 闭环并通过 **B + D + C** 级验收，报告
[P3-01 报告](reports/P3-01-ocr-text-regions-confidence-fts.md)。起点 HEAD `3ea0cf1`（P2-08 报告
提交 `446dbf7`），工作树干净。

新增 `src/photo_search/ocr/**`（固定 PP-OCRv4 权重校验、有界长边 2000 px + 1280/160 切片与 IoU
去重、确定性阅读顺序、低置信度区域保留、取消/预算检查、结构化 `ocr-document.v1` 产物、同事务
持久化、`python -m photo_search.ocr.cli` 受控入口）与连续增量迁移
`catalog/migrations/0009_ocr_documents.sql`：**`schema_version` 推进到 9**，`0001`～`0008` 未改；
新增 `asset_ocr_text`（规范化检索副本 + revision/权重指纹/区域统计）与 `asset_ocr_regions`
（顺序/box/置信度），并把 `search_projection_queue` 的 cause 扩到五值（新增 `OCR_TEXT_CHANGED`，
0006 的 8 个触发器 SQL 一字未改）。`search_documents.ocr_text` 经既有队列/`sync_document` 投影，
且只在 `input_revision = assets.sha256` 时生效；`/capabilities` 新增只读 `ocr` 段
（`documents/stale_documents/regions/low_confidence_regions/jobs/state ∈ {NOT_RUN, LAGGING,
BLOCKED, SYNCED}`，无原文、无坐标、无路径）。本任务未改 ADR/冻结 JSON schema、依赖、前端资源、
`ingestion/**`、`places/**`、`geo/**` 或打包入口。

| Check | Result |
|---|---|
| `uv run --no-sync pytest -q -p no:cacheprovider tests/unit tests/integration` | exit 0；JUnit `tests=877 failures=0 errors=0 skipped=1`，208.9 s |
| 本任务新增用例 | 75 例（contract 24、geometry 20、cli 8、end-to-end 4、lifecycle 13、migration 6） |
| `uv run --no-sync ruff check .` / `git diff --check` / `uv run --no-sync uv lock --check` | clean / clean / exit 0（95 packages，依赖未变） |
| C 级 | 真实 onedir 重建 + `packaged_api_smoke.py --work-dir D:\PhotoSearchSmoke\P3-01`：`status=ok`、`problems=[]`、`coverage_ocr_block={processor_id: ocr-text@1, state: NOT_RUN, weights_present: false}`；bundle 内含 `0001`～`0009`（0009 与源码 SHA-256 一致） |
| 真实夹具 | `tools/diagnostics/ocr_smoke_fixture.py` exit 0（12.22 s）：发票图 5 区域/置信度 0.9719–0.9999、高密度页 20 区域/2 切片、空白页 0 区域（诚实空文档）；发票图 FTS 命中 `发票`/`1280`/`20260914` |
| v8→v9 迁移 | 备份内 `schema_version=8` 且无 OCR 表；单事务推进；旧队列行逐列保留；未知 cause 被拒；注入损坏 0009 整体回滚；缺件 `SCHEMA_INCONSISTENT`；过新库零写 |
| D 级注入 | 权重缺件/同尺寸哈希不符、提交前崩溃、内容变化后旧结果、旧 revision 文档被投影忽略、到期租约接管、用户取消、预算耗尽、强制重识别幂等——全部零副作用 |

**P3-02 — 人脸检测对齐、特征、候选索引、保守自动关联：PASS.** 见
[P3-02 报告](reports/P3-02-face-detect-align-index-association.md)（B + D + C 级：964 例 0 失败 / 1 跳过，
87 个新增用例；真实夹具 6 资产/12 脸/8 个未命名分组；真实 onedir 重建 + 打包 smoke `problems=[]`；
`schema_version 9 → 10`）。任务卡 `docs/tasks/P3-02.md`（历史任务卡，保留供追溯）。

**P3-03 — 人物名称、NFC `name_key`、同名合并、旧 ID 重定向：PASS.** 见
[P3-03 报告](reports/P3-03-person-naming-merge-redirect.md)（B + D + C 级：1038 例 0 失败 / 1 跳过，
74 个新增用例；真实夹具 4 资产/9 脸/8 个未命名分组；真实 onedir 重建 + 打包 smoke `problems=[]`；
`schema_version 10 → 11`）。任务卡 `docs/tasks/P3-03.md`（历史任务卡，保留供追溯）。
冻结决策 `docs/decisions/ADR-0006-person-naming-merge-operation-contract.md` 在实现期**一字未改**；
按任务卡要求，实现后仍需交 **C2 / GPT-5.6 Sol / high** 独立审查。

## Latest face-correction contract closure — P3-04A (2026-09-15)

**P3-04A — 单脸纠错、合照与人工锁定：C2 规则冻结已 PASS。** 见
[P3-04A 报告](reports/P3-04A-face-correction-contract.md)与
[ADR-0007](decisions/ADR-0007-face-correction-lock-exclusion-contract.md)。本任务冻结共享
`person_operations` 操作权威的 `FACE_ASSIGN/FACE_UNASSIGN/FACE_RESTORE_AUTO/BATCH_FACE_EDIT`、
`FACE_CONTROL` 快照、无控制行 logical revision 0、逐脸模式/排除矩阵、1～500 mixed batch、全局
request_id 幂等、向前补偿撤销、API/错误/隐私、四个只读计数，以及复用 `PERSON_CHANGED` 的权威→派生
顺序。机器可读伴随物为
[p3-face-correction-contracts.v1.json](decisions/contracts/p3-face-correction-contracts.v1.json)。A 级验证为
26 个新契约用例 + 完整 unit/integration + Ruff/diff/lock（详见报告）；未改生产代码、migration、
schema_version、依赖、既有 ADR/JSON、打包或前端。

## Latest face-correction implementation closure — P3-04B (2026-09-16)

**P3-04B — 单脸纠错、合照与人工锁定：DS-H 实现已 PASS（B + D + C）。** 见
[P3-04B 报告](reports/P3-04B-face-correction-lock-and-undo.md)。起点 `5a17418`（P3-04A），
本任务提交 `980f41d`，工作树干净。

新增连续 `catalog/migrations/0012_face_correction_operations.sql`：**`schema_version` 推进到 12**，
`0001`～`0011` 逐字节未改；0012 **不新增任何表**，只重建 `person_operations`/`person_operation_items`
两张操作表，把闭合 CHECK 扩到 ADR-0007 的四个脸级 type 与互斥的 `entity_id`/`entity_key`
（`FACE_CONTROL` 用 `entity_key = face_id`），并逐列保留既有 operation、item、UNDO 与
`person_aliases.last_operation_id` 引用。扩展 `search_projection_queue` 与新增 cause **都不需要**：
`PERSON_CHANGED` 已准确覆盖。

新增 `src/photo_search/persons/face_correction.py`（模式/排除矩阵、canonical 排除、`FACE_CONTROL`
before/after 快照、全局 `request_id` 幂等门、1～500 mixed batch、向前补偿撤销），加性修改
`persons/{contracts,operations,service}.py`（`publish_derived` 与 `operation_family`）、`face/index.py`
（写事务内的 logical control revision 门禁 `_control_revision_gate` + canonical 排除过滤）、
`catalog/{schema,errors}.py`、`scheduler/projection.py`（四个只读计数，canonical pair 去重复用同一个
redirect 解析器）、`api/{models,routes,errors}.py`（3 条新路径 + 扩展 undo，OpenAPI 35 → 37）。
未改 ADR-0001～0007/冻结 JSON、依赖（95 包）、`PhotoSearch.spec`、前端、`places/**`、`geo/**`、
`ingestion/**`、`workers/**`。

| Check | Result |
|---|---|
| `uv run --no-sync python -m pytest -q -p no:cacheprovider tests/unit tests/integration` | exit 0；JUnit `tests=1114 failures=0 errors=0 skipped=1`（326.5 s）；`test-results/p3-04b-full.xml` |
| 本任务新增用例 | 50 例（lifecycle 21、undo 11、migration 6、api 5、unit contract 7） |
| `uv run --no-sync ruff check .` / `git diff --check` / `uv run --no-sync uv lock --check` | clean / clean / exit 0（95 packages，依赖未变） |
| 真实夹具 | `tools/diagnostics/face_correction_smoke_fixture.py --device cpu` exit 0：4 资产/9 脸/8 个未命名分组；取消→指定→恢复→撤销全部 `replayed=false`；`siblings_unchanged=true`；重新关联 `human_controlled=1` 且被排除人物未回归；重启后 mode/revision/归属/排除与撤销后一致；候选 entry `CONFIRMED` |
| v11→v12 迁移 | 真实 v11 库（3 operation/4 item/1 UNDO/3 alias 引用）逐列保留；备份内 `schema_version=11` 且无 `entity_key`；注入损坏 0012 整体回滚（无残留 `*_backup` 表）；缺件 `SCHEMA_INCONSISTENT`；过新库零写 |
| C 级 | 真实 onedir 重建 + `packaged_api_smoke.py --work-dir D:\PhotoSearchSmoke\P3-04B-final`：`status=ok`、`problems=[]`、`startup_seconds=2.41`、`coverage_capabilities_status=200`、`coverage_has_no_local_path=true`；bundle 内含 `0001`～`0012`（`0012` 与源码 SHA-256 `6E4E56F0…1CF12` 一致），打包后的 data root 实测 `schema_version=12`、`entity_key` 列存在、type CHECK 含 `FACE_UNASSIGN` |
| D 级注入 | 过期 revision 零写、幂等门早于 revision、晚到自动结果 `LATE_CONTROL_CONFLICT` 零写、批量中途失败全回滚、批量重复/501 拒绝、撤销冲突整批拒绝且 revision 不倒退、undo an UNDO 拒绝、no-op 撤销零副作用、P3-03 合并交叉冲突、快照指向的脸消失具名冲突 |
| 非生产机械更新 | `SUPPORTED_SCHEMA_VERSION` 12；`tests/unit/test_catalog_migrations.py` 注释；`tests/integration/test_p3_person_migration.py` 钉回 0011；`tools/places/examples/mini-cities.{spec,manifest}.json` 的 `compatible_catalog_schema.maximum` 11 → 12 |

## Latest independent face-correction review — P3-04C (2026-09-20)

**P3-04C — P3-04 独立 C2 审查：PASS（修复后 A + D + C）。** 见
[P3-04C 报告](reports/P3-04C-c2-review.md)。审查修复 5 类已冻结语义的实现偏差：operation 持久化实际
filter revision、脸级 undo replay 保持 contract envelope、控制 bundle 恢复的 link revision 严格向前、
AUTO_ALLOWED 活动排除只过滤 canonical 候选而不锁死整脸、脸级纠错不刷新图片向量 outbox。

`schema_version` 仍为 12；ADR-0007、companion、`0001`～`0012`、依赖、`PhotoSearch.spec` 和前端均未改。
52 个 P3-04 专项、6 个独立关键复现及完整 unit/integration 全部通过；完整 JUnit 为
`tests=1116 failures=0 errors=0 skipped=1`（419.408 s）。Ruff、diff-check、lock-check、真实 CPU 夹具、
真实 onedir 重建、新 D 盘目录打包 API smoke 和 bundle migration/hash 核对全部通过。

## Latest person-split contract closure — P3-05A (2026-09-20)

**P3-05A — 人物拆分、通用撤销与 revision 冲突契约冻结：PASS（A 级，零生产变化）。** 见
[P3-05A 报告](reports/P3-05A-person-split-contract.md)、[ADR-0008](decisions/ADR-0008-person-split-operation-contract.md)
与 [companion](decisions/contracts/p3-person-split-contracts.v1.json)。冻结唯一
`POST /api/v1/persons/{source_person_id}/split`、source/control/assignment 三 revision、全局 request_id
幂等、未命名新人物、所选脸 MANUAL_ASSIGNED + source exclusion、`SPLIT_GROUP + FACE_CONTROL` 混合快照、
created ID 撤销后 redirect、后续 rename/merge/face edit/second split/effective-member 冲突，以及一次
`PERSON_CHANGED` + face `PENDING_REPAIR` 且不写图片向量的派生边界。

新增契约用例 22/22 通过；既有 P3-03/P3-04 contract 回归 64/64 通过；Ruff、diff-check、lock-check clean。
`schema_version` 仍为 12，`0001`～`0012`、`src/photo_search/**`、依赖、打包与前端均未改。

## Latest person-split implementation closure — P3-05B (2026-09-20)

**P3-05B — 人物拆分与通用撤销实现：PASS（B + D + C）。** 见
[P3-05B 报告](reports/P3-05B-person-split-and-undo.md)。起点 `7ba4c12`（P3-05A 文档冻结），
实现提交 `12a14a1`（另有 2 个仅改文档/测试的收尾提交），交付时工作树干净。

新增 `src/photo_search/persons/split.py` 与连续 `catalog/migrations/0013_person_split_operations.sql`：
**`schema_version` 推进到 13**，`0001`～`0012` 逐字节未改；0013 与 0012 同构，只重建
`person_operations`（加 `SPLIT`）与 `person_operation_items`（加 `SPLIT_GROUP`，形状改为「只有
`FACE_CONTROL` 带 `entity_key`」），不新增事实表、不新增 queue cause，`PERSON_CHANGED` 继续覆盖。
实现 `POST /api/v1/persons/{source_person_id}/split` 与 split family 的共享撤销：source/control/assignment
三 revision 预检、全局 request_id 幂等门先于一切实体/revision 检查、未命名无 alias 新人物、所选脸
`MANUAL_ASSIGNED` + source exclusion + 严格向前的 `MANUAL/CONFIRMED` link、固定
`source SPLIT_GROUP → created SPLIT_GROUP → 按 face_id 排序 FACE_CONTROL` 顺序、七类具名撤销冲突、
created 撤销后 redirect 到 source。派生顺序复用 `publish_derived`（一次 `PERSON_CHANGED` + 队列 +
`PENDING_REPAIR`）且**不调用**图片 projection refresh。

| Check | Result |
|---|---|
| `uv run --no-sync python -m pytest -q -p no:cacheprovider tests/unit tests/integration --basetemp=.pytest-tmp/P3-05B-full4 --junitxml=test-results/p3-05b-full.xml` | exit 0；JUnit `tests=1184 failures=0 errors=0 skipped=1`（373.8 s）；4 次完整套件运行中有 2 次命中既有 P2-06B 计时断言偶发（`elapsed_ms` 0 vs 15，该文件与精排代码自 `dfb4c3b` 起未改、不在允许范围），最终树上的同一命令 exit 0 |
| 本任务新增用例 | 46 例（unit 7、lifecycle 12、undo 15、api 4、migration 8），JUnit `test-results/p3-05b-focused.xml`；另加 P3-03/P3-04/P2-03B/P3-05 回归批 231 例 0 失败 |
| `uv run --no-sync ruff check .` / `git diff --check` / `uv run --no-sync uv lock --check` | clean / clean / exit 0（95 packages，依赖未变） |
| 真实夹具 | `tools/diagnostics/person_split_smoke_fixture.py --device cpu` exit 0：真实检测→合并→命名→拆分→重启→撤销→重启，`status=ok`、`problems=[]`，`filter_delta=1`、`image_outbox_delta=0`、`siblings_unchanged=true` |
| v12→v13 迁移 | 真实 v12 库（RENAME/UNDO/MERGE_BY_NAME/BATCH_FACE_EDIT + PERSON_BUNDLE/FACE_CONTROL + alias 引用）逐列保留；备份拒绝 `SPLIT`；损坏 0013 整体回滚；缺件 `SCHEMA_INCONSISTENT`；过新库零写；重复迁移 `applied=()` |
| C 级 | 真实 onedir 重建 + `packaged_api_smoke.py --work-dir "D:\PhotoSearchSmoke\P3-05B 中文 空格"`：`status=ok`、`problems=[]`、`startup_seconds=1.8`；bundle 内含 `0001`～`0013`（`0013` 与源码 SHA-256 `9DEA2D67…CCBDE4` 一致），打包 data root 实测 `schema_version=13` 且 CHECK 含 `SPLIT`/`SPLIT_GROUP`/`FACE_ASSIGN`/`FACE_CONTROL` |
| 环境事实 | `.pytest-tmp` 对受限令牌不可读，pytest 的 `mode=0o700` basetemp 在该令牌下 `WinError 5`；按任务卡未改任何 ACL，全部 pytest 改由已授权的正常 Windows 权限路由执行，basetemp 仍为 `.pytest-tmp/P3-05B-*` |
| 非生产机械更新 | `SUPPORTED_SCHEMA_VERSION` 13；`tools/places/examples/mini-cities.{spec,manifest}.json` 兼容上限 12 → 13；`tests/integration/test_p3_face_correction_migration.py` 两处钉回 0012；`tests/integration/test_p3_person_migration.py` 的未知类型样例 `SPLIT` → `MOVE_FACE`；`tools/diagnostics/packaged_api_smoke.py` 新增 split route/schema 13 检查 |

P3-05B 未验证项（真实大规模拆分与 500 face 批量延迟、多进程并发、产品侧入口与新人物再拆分、
模型重跑后的新旧 `face_id` 映射属 P3-08、`.pytest-tmp` ACL 环境事实、P2-06B 精排计时断言偶发）记录在
[P3-05B 报告](reports/P3-05B-person-split-and-undo.md) §12。

## Latest independent person-split review — P3-05C (2026-09-20)

**P3-05C — P3-05 人物拆分与通用撤销独立 C2 审查：PASS（修复后 A + D + C）。** 见
[P3-05C 报告](reports/P3-05C-c2-review.md)。审查发现并最小修复 4 类已冻结语义的实现偏差：拆分覆盖
人工 link 前先保留其 revision floor；撤销恢复 link 时把当前有效 assignment 与全部幸存 link 纳入 floor；
拆分 operation item 必须使用连续 index，不能用 gap 绕过 family/order 校验；split 响应及嵌套 schema
统一闭合 `additionalProperties: false`。另删除一个不在 companion 中的 undo error `status` 详情字段。

`schema_version` 仍为 13；ADR-0008、companion、`0001`～`0013`、依赖、`PhotoSearch.spec` 和前端均未改。
80 个定向/交叉回归全部通过；完整 unit/integration 收集 1185 项，JUnit 为
`tests=1185 failures=0 errors=0 skipped=48`（185.861 s），48 项均为缺模型权重/artifact 或未启用 GPU 的
显式环境 skip，P3-05C 定向批为 0 skip。Ruff、diff-check、lock-check、真实 onedir 重建、新 D 盘中文/
空格目录打包 API smoke 和 bundle migration/hash 核对全部通过。

本机当前没有 buffalo_l/PP-OCRv4 权重，本次未下载模型、未重跑 P3-05B 的真实 CPU 检测夹具；这项沿用
P3-05B 已有证据并在报告列为本次未验证，不影响本次针对拆分权威语义的 80 项独立回归。

## Latest person-page closure — P3-06 (2026-09-20)

**P3-06 — 人物页、同名提示、待归类、脸框、最近操作和撤销 UI：PASS（B + C + 真实浏览器 smoke）。**
见 [P3-06 报告](reports/P3-06-persons-ui.md)。起点 `84876b8`（P3-05C 审查收尾），工作树干净。

新增 `src/photo_search/persons/read.py`（三个 keyset 列表：三套互不通用的 cursor token 空间、canonical/
redirect 解析、有效成员计数）与 `src/photo_search/persons/members.py`（「有效成员」谓词的唯一定义，
`service.py` 改用它、行为逐字不变），加性公开 `persons/operations.item_projection()`；新增
`src/photo_search/media/face_crop.py`（bbox 校验、方向归一尺寸探测、单因子帧映射、有界不放大裁剪、
内容+几何+face 的缓存摘要）以及 `media/contract.py`/`cache.py` 的加性常量与 `face_crop_path()`；
新增 `api/routes.py` 的四个只读端点与 `GET|HEAD /api/v1/faces/{face_id}/thumbnail`；新增前端
`web/persons.html|js|css` 与 `web.py` 的 `/persons` 文档路由，三个既有页面增加**默认隐藏**、由
`/capabilities`（`schema_version >= 13 && schema_status == "ok"`）揭示的导航入口。
**未新增/修改任何 migration**，`schema_version` 仍为 13，`0001`～`0013` 逐字节未改；ADR-0001～0008、
companion、人物写接口闭集、依赖（95 包）、`PhotoSearch.spec` 的输入集合、`places/**`、`geo/**`、
`ingestion/**`、`workers/**`、`face/**`、`ocr/**`、`search/**` 均未改动。

| Check | Result |
|---|---|
| `uv run --no-sync python -m pytest -q -p no:cacheprovider tests/unit tests/integration --basetemp=.pytest-tmp/P3-06-full --junitxml=test-results/p3-06-full.xml` | exit 0；JUnit `tests=1230 failures=0 errors=0 skipped=48`（216.8 s；48 项为缺模型权重/未启用 GPU 的显式环境 skip）；基线 1185 → 1230 |
| 本任务新增用例 | 45 例（读模型/裁剪几何 unit 16、只读 API 8、头像端点 8、页面写入闭环 5、前端静态契约 8） |
| `uv run --no-sync ruff check .` / `git diff --check` / `uv run --no-sync uv lock --check` | clean / clean / exit 0（95 packages，依赖未变） |
| 只读面 | 三个列表排序/分页无重无漏；跨列表 cursor 一律 `INVALID_CURSOR`；同名合并后旧 id 不再列出而 `person_id=<旧 id>` 仍解析到 canonical；`MANUAL_UNASSIGNED` 队列只含人工取消归类的脸；活动排除从列表与详情计数同时消失 |
| 头像端点 | 双色合成图上左右脸的裁剪颜色各自正确（证明取的是该脸的框）、两脸字节不同；ETag/304/HEAD；120×90 图上的 20 px 脸裁成 34 px（不放大到 256）；EXIF orientation 6 的图按 `exif_transpose` 参考帧取色；源离线 `SOURCE_FILE_MISSING`、坏媒体 `DECODE_FAILED`、`FACE_NOT_FOUND`、坏几何 `FACE_STATE_CONFLICT(bbox_arity)` 四种拒绝都不留裁剪文件、不泄露路径 |
| OpenAPI/隐私 | 四个新响应模型 `additionalProperties: false`；查询参数集合闭合；响应负向扫描覆盖 `name_key`/alias/bbox/landmark/embedding/vector/similarity/confidence/快照/`payload_sha256`/`request_id`/本地路径 |
| D 级写回归 | 过期 revision 拒绝且状态逐字未变；同 `request_id` 逐字重放 `replayed=true` 不写入、同 key 不同 payload `FACE_IDEMPOTENCY_CONFLICT`；501 项 `FACE_BATCH_TOO_LARGE`；被后续人工编辑改变的操作 `FACE_OPERATION_NOT_UNDOABLE`、撤销重放返回已记录 UNDO；401/CSRF/Origin/Host 四种拒绝零副作用 |
| 重启恢复 | 同一 data root 重建应用后人物列表、操作日志（同一 `operation_id`）、成员脸与头像裁剪都从权威事实重新得到 |
| 真实浏览器 smoke | 内置浏览器逐条人工核对 14 步（三视图、10+3 分页无重复、脸图失败占位、同名合并展示 canonical、撤销、481/640/1280 px 三宽度 `scrollWidth <= clientWidth`、键盘可达、正文无路径、无外链、`img` 只指向受控 id、访问日志 `token=<redacted>` 且 `/geo` 命中 0）；截图 `test-results/p3-06-persons-wide.png`、`test-results/p3-06-persons-narrow.png`；清单见 `docs/diagnostics/frontend-smoke-checklist.md` §7 |
| C 级 | 最终源码修订后真实 onedir 重建 + `packaged_api_smoke.py --work-dir "D:\PhotoSearchSmoke\P3-06 中文 空格"`：`status=ok`、`problems=[]`；`/persons` 与 `persons.js/css` 在 bundle 内且 `nav_offers_persons=true`；四个新只读面与 401 边界就位；打包 data root `schema_version=13`；bundle `0001`～`0013` 连续且与源码逐个 SHA-256 一致（`0013 = 9DEA2D67…CCBDE4`） |
| 开发期真实缺陷（已修） | 浏览器里发现 `AUTO_ALLOWED` 人脸标签与「有效归属」矛盾、撤销成功后操作日志不刷新、`#name-result` 的 64 位 operation id 在 481 px 下撑出横向滚动；三处分别改为「自动（没有人工锁）」、撤销后重载日志、给长 token 容器加 `overflow-wrap: anywhere` |

P3-06 未验证项（真实库规模与延迟、真实 buffalo_l 检测→分组链路、旧 schema 隐藏入口、无 DOM 自动化、
运行时 `localStorage` 读数、多进程并发、接近 500 face 的拆分/批量延迟、头像主观质量、`.pytest-tmp`
ACL 环境事实）记录在 [P3-06 报告](reports/P3-06-persons-ui.md) §7。

## Latest combined-search contract closure — P3-07A (2026-09-20)

**P3-07A — 人物 all/any 与地点 AND 搜索契约冻结：PASS（A 级，零生产变化）。** 见
[P3-07A 报告](reports/P3-07A-person-search-contract.md)、
[ADR-0009](decisions/ADR-0009-person-place-combined-search-contract.md) 与
[companion](decisions/contracts/p3-person-search-contracts.v1.json)。起点 `87ad5c7`（P3-06 收尾），
开始时工作树干净；本任务提交 `e6b1f7e`。

冻结 `POST /api/v1/search` 的两个**加性可选**字段 `person_ids`（1..100 个、每个 `>= 1`）与
`person_match`（缺省 `any`）：空/缺省**不产生**人物条件；原始重复/非整数/非正数/超界与非法枚举各有固定
判定（422 `VALIDATION_ERROR`，或新增的 422 `INVALID_PERSON_FILTER`）；canonical 解析复用
`persons/identity.py`，合并后的重复条件静默去重，旧 id 仍可用于重搜；`all`/`any` 的判定对象是
`persons/members.py` 的 `MEMBER_PREDICATE` 有效成员（`CONFIRMED`、非 `MANUAL_UNASSIGNED`、无活动排除），
与其他条件一律 AND，人物只作筛选、不作命中原因；人物条件是权威 SQLite 谓词，**不新增 migration**
（`schema_version` 仍为 13）；人物与地点共享**一个** `catalog_filter_revision`，续页过期复用
`CATALOG_FILTER_REVISION_STALE`，游标摘要必须含 canonical 人物条件；`search_projection` 与
`pending_semantic` 的字段集合与上界（1000 / 200）被字段级冻结，并写明 `LAGGING` 只代表派生文本索引在
追赶。

新增契约用例 41 例；完整 unit/integration JUnit 为 `tests=1271 failures=0 errors=0 skipped=48`
（173.5 s；48 项为缺模型权重/未启用 GPU 的显式环境 skip）；`uv run --no-sync ruff check .`、
`git diff --check`、`uv run --no-sync uv lock --check`（95 packages）全部 clean / exit 0。
`src/photo_search/**`、`catalog/migrations/**`、`schema_version`、依赖、`PhotoSearch.spec`、前端与既有测试
**均未改动**（`git diff --stat` 为空，只有 3 个新增未跟踪文件）。`.pytest-tmp` 的受限令牌 ACL 环境事实
沿用 P3-05B/P3-06，本次未改任何 ACL。

未验证项（检索实现是否按冻结谓词组合、`INVALID_PERSON_FILTER` 是否已生效、30 万容量下人物条件延迟、
解析器 `person_mode` 与协议字段的映射、脸级口径、时间条件）记录在
[P3-07A 报告](reports/P3-07A-person-search-contract.md) §6。

下一任务是 **P3-07B — 人物 all/any × 地点 AND 组合搜索实现**，任务卡
[docs/tasks/P3-07B.md](tasks/P3-07B.md)，推荐 **DS-H / DeepSeek V4.1 Flash / 高推理**；实现完成后由独立
新会话 DS-H 做全量工程主审（仅按 V3.1 §2.2 六类条件升 DeepSeek max），canonical/revision/缓存失效三条
边界交 **C2 / GPT-5.6 Sol / high 聚焦终审**；请手动切换模型/推理档后在新对话继续。Windows/PyInstaller/
浏览器环境问题升级 C1。

## Latest person-search implementation closure — P3-07B (2026-09-20)

**P3-07B — 人物 all/any × 地点 AND 组合搜索实现：PASS（B + D；未触发 C）。** 见
[P3-07B 报告](reports/P3-07B-person-search-implementation.md)。起点 `6fbe68e`（P3-07A 收尾，工作树干净），
本任务提交 `733f1a3`。

`SearchRequest`/`SearchRequestModel` 加性新增两个可选字段（`person_ids`、`person_match`），
`p2-contracts.v1` 的既有字段、默认值、上下界与版本号一字未改；判定表逐行落地（缺省/空不产生条件；
原始重复、非整数、`<1` 与非数组为 422 `VALIDATION_ERROR`；`all` 无 id 与 101 项为 422
`INVALID_PERSON_FILTER`，后者带 `max_person_ids=100`）。传输层用 `StrictInt` 拒绝 `true`/浮点/字符串/
`null`，但**故意不设** `maxItems`，以保证超界答的是冻结码而不是通用形状错误。

新增 `search/retrieval.py` 的 `person_condition()`：内联 `persons/members.MEMBER_PREDICATE`（**只有一份**
有效成员口径）、`any` 一个 `EXISTS … IN (…)`、`all` 每人一个 `EXISTS … = ?` AND，并作为权威谓词进入
`scope_condition`，因此 FTS/向量通道、提交前复核与补查页同时受约束；人物条件不进入命中原因或排序通道。
`sessions.query_hash` 纳入 canonical、去重、升序的人物条件；canonical 解析复用
`persons/identity.resolve_canonical_person_id`（404 `PERSON_NOT_FOUND` / 409 `PERSON_REDIRECT_CYCLE`，
整请求拒绝）；`scope_view` 始终回显 `person_ids`（canonical 升序，无条件时 `[]`）与 `person_match`
（无条件时 `"any"`），`/search/image` 固定为空回显且不新增 multipart 字段。

一处**刻意的判断顺序收敛**：`_continue_session` 现先比 `catalog_filter_revision`（与 suggestion
revision）再比 `query_hash`。理由是合并会把 link 改写到目标人物、使旧 id 的 canonical 身份改变，
旧顺序会把「合并后的旧会话续页」答成 `INVALID_CURSOR`，而 ADR-0005 §10 与 ADR-0009 §4.4/§6.2 要求
`CATALOG_FILTER_REVISION_STALE`；revision 未变时的不同条件续页仍答 `INVALID_CURSOR`（P2-06A 既有断言
原样通过）。**该条列为 P3-07C 主审的重点问题。**

| Check | Result |
|---|---|
| `uv run --no-sync python -m pytest -q -p no:cacheprovider tests/unit tests/integration --basetemp=.pytest-tmp/P3-07B-full --junitxml=test-results/p3-07b-full.xml` | exit 0；JUnit `tests=1334 failures=0 errors=0 skipped=48`（237.4 s）；基线 1271 → 1334（+63） |
| 本任务专项（scope 21 + contracts 46 + integration 37） | exit 0；JUnit `tests=104 failures=0 errors=0 skipped=0`（19.3 s） |
| `uv run --no-sync ruff check .` / `git diff --check` / `uv run --no-sync uv lock --check` | clean / clean / exit 0（95 packages，依赖未变） |
| 真实夹具冒烟 `tools/diagnostics/person_search_smoke_fixture.py` | exit 0；`status=ok`、`problems=[]`：`person_ids=[p]` 命中数逐人等于 `GET /persons/{p}` 的 `asset_count`（2/2/0）、`any`={3 张}、`all`={1 张}、merge 后旧会话 `CATALOG_FILTER_REVISION_STALE`、旧 id 重搜回显 canonical、undo 恢复、重启后旧游标 `SEARCH_SESSION_EXPIRED` |
| D 级注入 | 判定表全表 422 且零写零会话；`any`/`all` × 地点/来源/可用性/语义矩阵；旧 id/折叠/不存在/成环/悬挂；`SUGGESTED`/`MANUAL_UNASSIGNED`/活动排除都不命中且同图 sibling 不受影响；改名/合并/拆分/纠错/撤销后旧会话 stale 且新请求正确；不同人物条件续页 `INVALID_CURSOR`；TTL 到期 `SEARCH_SESSION_EXPIRED`；投影滞后不复活已移除命中；隐私负向扫描；四个既有 companion SHA-256 与 `0001`～`0013`/`schema_version` 13 未改 |

未改动：`ADR-0009` 与 companion、四个既有 companion、`catalog/migrations/**`、`schema_version`（13）、
依赖、`PhotoSearch.spec`、前端、`places/**`、`geo/**`、`ocr/**`、`ingestion/**`、`workers/**`、
`face/**`；`persons/**` 只读复用（`identity`/`members`）。既有测试只**新增**用例，唯一编辑是
`tests/unit/test_p3_person_search_contracts.py` 的文档字符串与 5 条实现↔冻结件漂移检查（原 41 例断言逐字保留）。

开发期发现（**超范围、未修**）：当合并目标的 `person_id` 大于源人物时，撤销该合并会因
`face_person_links` 的部分唯一索引触发 `IntegrityError`（反向顺序正常）。最小复现与成因见报告 §6；
该缺陷属 `persons/operations.py` 的人物撤销语义，不在本卡允许范围，建议 P3-08 或单开卡处理。

未验证项（30 万容量下人物条件延迟与索引使用、多进程并发、脸级口径、解析器 `person_mode` 映射、
时间条件、`GET /api/v1/assets` 的人物筛选、C 级打包）记录在
[P3-07B 报告](reports/P3-07B-person-search-implementation.md) §7。`.pytest-tmp` 受限令牌 ACL 环境事实
沿用 P3-05B/P3-06/P3-07A，本次未改任何 ACL。

下一任务是 **P3-07C — P3-07 两会话分阶段审查（DS-H 全量主审 → Sol 高风险聚焦终审）**，
任务卡 [docs/tasks/P3-07C.md](tasks/P3-07C.md)。

## Latest staged review, stage A — P3-07C DS-H engineering review (2026-09-20)

**P3-07C 阶段 A（DS-H 对 P3-07B 的全量工程主审）：`ENGINEERING REVIEW PASS / SOL FOCUSED REVIEW PENDING`。**
见 [P3-07C 阶段 A 报告](reports/P3-07C-dsh-engineering-review.md)。起点 `6fbe68e`（P3-07A 收尾），审查对象
`733f1a3`（+ `033e3ca` 报告收尾）；阶段 A **未做任何最小修复**，未改任何生产代码、测试、冻结件或迁移。

11 项审查范围逐条给出结论：diff 走查逐文件落到 ADR-0009 的具体条款；判定表逐行三方对照（**原始重复 =
形状错误**与 **canonical 折叠 = 合法去重**确为两条独立路径；传输层**故意不设** `maxItems`，101 项答冻结码
`INVALID_PERSON_FILTER` 并公开 `max_person_ids=100`）；检索层只内联 `persons/members.MEMBER_PREDICATE`
（全仓唯一定义），`any`/`all` 的 SQL 与 ADR §3.2 等价（`|P|=1` 时等价）；人物条件进入 scope 后同时约束
FTS、向量、提交前复核与补查页，且不成为命中原因/排序通道/RRF 输入；canonical 解析、回显、摘要同用
`Catalog.connection`，任一 ID 失败整请求拒绝；三条高风险边界（canonical / revision / 缓存失效）均通过；
`search_documents` 六个文本列无人物列，`cache/face-crops` 键不含 `person_id`；隐私闭集与 `/search/image`
空回显通过；ADR-0009 与 companion、四个既有 companion、`0001`～`0013`、依赖 95 包、`PhotoSearch.spec`、
前端均逐字节未改。

**顺序收敛（`_continue_session`：revision → suggestion → digest → binding）判定：一致。** ADR-0005 §10、
ADR-0003 §9、ADR-0009 §4.4/§6.2 都要求续页在 revision 差异时答 `CATALOG_FILTER_REVISION_STALE`，而合并会
改写旧 id 的 canonical 身份，旧顺序会把这种续页误答成 `INVALID_CURSOR`。审查期独立探针（真实扫描夹具 +
合成库，运行后删除）实测：revision 未变 + 条件不同仍 422 `INVALID_CURSOR`；revision 未变 + 仅 profile 切换仍
409 `PROFILE_GENERATION_MISMATCH`；merge 后旧会话 409 `STALE`、旧 id 重搜 200 且 `filters.person_ids` 回显
canonical。唯一外延是「revision 已变且 profile 同时切换」现在答 `STALE`（旧顺序答 `PROFILE_GENERATION
MISMATCH`），两者都是要求重搜的 409，已按 ADR 判定为符合冻结件并列入 Sol 必审边界。

实测：完整 JUnit `tests=1334 failures=0 errors=0 skipped=48`（207.6 s，`test-results/p3-07c-full.xml`）；
专项 104 例 0 skip（`test-results/p3-07c-focused.xml`）；Ruff、diff-check、lock-check 全 clean。本卡**不触发
DeepSeek max**（V3.1 §2.2 六类条件逐条未命中，理由写在报告文首）。越界发现核实：P3-07B 报告 §6 的
「合并目标 id 大于源人物时 undo 触发 `face_person_links` 唯一索引冲突」独立复现成功，确认属
`persons/operations.py` 的既有撤销顺序缺陷、与本任务改动无关，**本卡不修**，建议随 P3-08 处理。

下一阶段当时是 **P3-07C 阶段 B — C2 / GPT-5.6 Sol / high 聚焦终审**（已执行，结果见下）。阶段 A 的
「同连接 = 同快照」推论已被阶段 B 更正，并在阶段 A 报告内保留了更正注；请以阶段 B 与
[阶段 A 补轮报告](reports/P3-07C-dsh-canonical-fix.md) 为准。Windows/PyInstaller/浏览器环境问题升级 C1。

## Latest focused review, stage B — P3-07C Sol boundary review (2026-09-20)

**P3-07C 阶段 B：`SOL FOCUSED REVIEW FAIL / DS-H FIX REQUIRED`。** 见
[阶段 B 报告](reports/P3-07C-sol-focused-review.md)。revision 与缓存失效两条边界通过；canonical 边界不通过。

失败反例使用第二个正常 `Catalog` 连接，在 `[source,target]` 的第一个 id 解析后提交合法 merge。实现随后回显
合并前集合 `[source,target]`，却用合并后的 link 执行 `all` 谓词，得到空命中；这既不是合并前应有的共现照片，
也不是合并后应有的 canonical 目标并集。根因是 `_canonical_person_ids()` 与后续检索只是共用 autocommit
连接，没有覆盖整个请求的一致只读快照；`search_assets()` 又在 canonical 解析之后才记录 revision 起点，无法
检测该窗口。阶段 B 现有最小受影响批 12 例全部通过，但新增并发快照探针按预期暴露缺陷（1 test / 1 failure）；
临时测试代码已删除，失败 JUnit 保存在 `test-results/p3-07c-sol-probe.xml`。

当前仍是 **P3-07C**：按报告 §6 退回 **DS-H / DeepSeek V4.1 Flash / 高推理**，只修 canonical
snapshot/revision 一致性并补持久回归；不得改 `persons/**` 语义、ADR/companion、migration/schema、依赖或
前端。修复后再交独立 C2 短审失败边界及 revision/cache 直接回归面。**P3-08 未生成，也不得提前开始。**
请在 Codex 中手动切换模型/推理档后继续。

## Latest canonical fix, stage A follow-up — P3-07C (2026-09-20)

**P3-07C 阶段 A 补轮（DS-H canonical 一致性最小修复）：已应用，等待 Sol 短审。** 见
[阶段 A 补轮报告](reports/P3-07C-dsh-canonical-fix.md)。起点 `033e3ca`，仍属 P3-07C，未生成 P3-08 卡。

改动面只有 4 个文件：`src/photo_search/catalog/db.py` 新增 `Catalog.read_snapshot()`（`BEGIN DEFERRED`
固定快照、块内只读，并让同进程 `write()` 与嵌套快照直接报错，从而把搜索期唯一写者
`search_documents.catch_up()` 留在快照外）；`src/photo_search/search/service.py` 把解析改为
`_person_authority_base()`（在**同一个**快照里读 `catalog_filter_revision` 并用唯一解析器解析全部 id），
新搜索分支改为 `SEARCH_PUBLISH_ATTEMPTS = 3` 的有界重锚：前置绑定与检索末尾 revision 不等即丢弃本次尝试
（不建会话、不渲染），重解析后重跑，三次都用尽则 409 `CATALOG_FILTER_REVISION_STALE`
（`reason=CATALOG_FILTER_REVISION_CHANGED`，details 只用冻结键）。**未新增人物 revision，未改
`search_assets()` 自身语义（其直接调用者仍按 P2-05 如实报告 `state=FILTER_REVISION_CHANGED`）。**

反例转持久回归 3 例（unit 1 + integration 2）：`test_a_read_snapshot_holds_one_state_across_a_concurrent_commit`、
`test_a_merge_during_the_canonical_resolution_is_never_a_mixed_answer`（第二连接在第一次解析后提交合法 merge，
结果必须一致，排除 `([A,B], ∅)`）、`test_a_catalog_that_keeps_moving_the_revision_refuses_instead_of_mixing`
（每次解析都提交真实 rename → 结构化拒绝且零会话）。对照实测：模拟修复前锚点语义时，丢弃的那次尝试返回的
`([A,B], []) @rev3` 与旧实现发布的混合页逐字相同；修复后该尝试被丢弃、重锚尝试返回 `([B], {1,2,3})`。

实测：完整 JUnit `tests=1337 failures=0 errors=0 skipped=48`（229.1 s，`test-results/p3-07c-fix-full.xml`，
阶段 A 基线 1334 → +3）；修复专项批 134 例 0 skip（`test-results/p3-07c-fix-focused.xml`）；Ruff、diff-check、
lock-check 全 clean。ADR-0009/companion、四个既有 companion、`0001`～`0013`、`schema_version` 13、依赖 95 包、
`PhotoSearch.spec`、前端、`places/**`、`geo/**`、`ocr/**`、`ingestion/**`、`workers/**`、`face/**` 与
`persons/**` 语义均未改（P3-07B 报告 §6 的 merge/undo 顺序缺陷仍未修，仍建议随 P3-08 处理）。

下一步：**P3-07C 阶段 B 短审**——独立新会话 **C2 / GPT-5.6 Sol / high** 只审修复后的 canonical 边界及
revision/cache 直接回归面（输入为阶段 A 补轮报告 §9 交接包）；通过后才标 P3-07C PASS 并生成 **P3-08**。
未验证项（多进程压测、30 万下重试成本、`place_state=suggested` 的同类窗口、`search_assets()` 直接调用者、
C 级打包、UI）记录在补轮报告 §7。请手动切换模型/推理档后在新对话继续。

## Latest Sol short re-review — P3-07C canonical publication window (2026-09-20)

**P3-07C Sol 短审补轮：`FAIL / DS-H SECOND FIX REQUIRED`。** 更新结论见
[阶段 B 报告 §7](reports/P3-07C-sol-focused-review.md)。第一轮修复的 `read_snapshot()`、canonical + revision
同快照 pre-binding 与检索期三次有界重锚均成立，也关闭了原始反例窗口；但一致性门在
`search_assets()` 返回时结束，未覆盖后续 `_entries_of`、rerank、`sessions.create()` 与 `_render_page()`。

短审用第二个正常 `Catalog` 连接，在最后一次 revision 比较后、唯一 `_entries_of(payload)` 调用内提交合法
merge；实测仍为 HTTP 200、`filters.person_ids=[1,2]`、`hits=[]`，不是任何单一快照的结果。失败 JUnit：
`test-results/p3-07c-sol-rereview-probe.xml`（1 test / 1 failure / 0 errors / 0 skipped）；临时测试代码已删除。

当前仍为 **P3-07C**。下一步建议 **DS-H / DeepSeek V4.1 Flash / 高推理**只把一致性边界延长到首页最后一次
权威读取与安全会话发布，并把这个 late-window merge 变成持久回归；不得改 ADR/companion、migration/schema、
依赖、`persons/**` 语义或前端。修复后再交 C2 短审。**P3-08 未生成。**请手动切换模型/推理档后继续。

## Latest second fix, publication gate — P3-07C (2026-09-20)

**P3-07C 第二轮修复（DS-H 发布闸门）：已应用，等待 Sol 短审补轮。** 见
[修复报告 §10](reports/P3-07C-dsh-canonical-fix.md)。起点为第一轮修复后的工作树，仍属 P3-07C，
未生成 P3-08、未标 PASS。

改动面 3 个文件：`src/photo_search/search/service.py` 让解析锚点同时带上独立
`place_suggestion_revision`（`_AuthorityBase`），新增 `_authority_holds()` 并在**首页渲染完成之后**重读
冻结计数器作为发布闸门（filter revision 对每个请求；suggestion revision 仅当 `place_state=suggested`，
与 `_continue_session` 的绑定口径一致），把首页构建抽成 `publish_once()`：
`run_retrieval → _entries_of → rerank → build_session → _render_page → 闸门 → register + 返回`；闸门不通过则
本次尝试不注册任何会话（因此不泄漏、也不会挤掉 LRU 里的既有会话）并重锚重跑，三次用尽才 409
`CATALOG_FILTER_REVISION_STALE`。`src/photo_search/search/sessions.py` 把 `create()` 拆成
`build_session()`（构造、不注册）+ `register()`（注册并维持上界），`create()` 保留为二者组合，LRU/TTL 行为不变。

反例转持久回归 2 例：`test_a_merge_after_the_last_retrieval_read_is_never_a_mixed_page`（第二连接在
`_entries_of()` 内提交合法 merge，即 Sol §7.2 的窗口；断言结果一致、尝试数 ≥ 2、会话数恰好 +1）与
`test_a_suggested_candidate_change_after_the_last_read_is_never_a_mixed_page`（同一注入点 +1
`place_suggestion_revision`；断言回显 revision 等于变更后的数据库值）。对照实测（只把 `_authority_holds`
替换为恒真以复现修复前发布行为）：merge 注入 → `[1,2]`/`[]`（混合页）；suggestion 注入 → 回显 0 而数据库 1；
开启闸门后分别得到 `[2]`/`[1,2,3]` 与回显 1，两条新断言在最左列必然失败。

实测：完整 JUnit `tests=1339 failures=0 errors=0 skipped=48`（221.8 s，`test-results/p3-07c-fix2-full.xml`，
第一轮 1337 → +2）；第二轮专项批 64 例 0 skip（`test-results/p3-07c-fix2-focused.xml`）；Ruff、diff-check、
lock-check 全 clean。第一轮的 `read_snapshot()`、同快照解析/pre-binding、三次有界重锚、continuation 顺序与
stale 四键、缓存键与 SQLite 同源读面全部保留；未改 ADR/companion、migration、`schema_version` 13、
依赖 95 包、`PhotoSearch.spec`、前端、`places/**`、`geo/**`、`ocr/**`、`ingestion/**`、`workers/**`、
`face/**` 与 `persons/**` 语义。

下一步：**Sol 短审补轮**——独立新会话 **C2 / GPT-5.6 Sol / high** 只审发布窗口与第一轮成果是否被削弱
（输入为修复报告 §10.6 交接包）；通过后才标 P3-07C PASS 并生成 **P3-08**。未验证项（多进程压测、30 万下
重试成本、`search_assets()` 直接调用者仍如实报告 `state=FILTER_REVISION_CHANGED`、C 级打包、UI）见报告 §7。
请手动切换模型/推理档后在新对话继续。

## Latest second Sol short re-review — P3-07C retry error surface (2026-09-20)

**P3-07C 第二次 Sol 短审补轮：`FAIL / DS-H ERROR-SURFACE FIX REQUIRED`。** 详见
[阶段 B 报告 §8](reports/P3-07C-sol-focused-review.md)。第二轮发布闸门本身通过：late merge 与单次
suggestion bump 两条持久回归独立复跑为 2 tests / 0 failures / 0 errors / 0 skipped，未注册 session 后校验、
通过后才发布的顺序成立，第一轮同快照 canonical 锚点未被削弱。

新增的临时反例让每次 bounded attempt 只递增 `place_suggestion_revision`，目录 filter revision 保持不变。
三次耗尽后接口虽返回 409，却固定报 `CATALOG_FILTER_REVISION_STALE` /
`CATALOG_FILTER_REVISION_CHANGED`，而 details 中 session/current filter revision 都是 `2`。冻结 ADR-0005 与既有
continuation 路径要求这种 suggestion-only 过期返回 `PLACE_SUGGESTION_REVISION_STALE` /
`SUGGESTION_REVISION_CHANGED`。失败 JUnit：`test-results/p3-07c-sol-rereview2-probe.xml`
（1 test / 1 failure / 0 errors / 0 skipped）；临时测试代码已删除。

当前仍是 **P3-07C**，P3-08 未生成。下一步由 **DS-H / DeepSeek V4.1 Flash / 高推理**只修
`src/photo_search/search/service.py` 的重试耗尽分类并补“连续 suggestion-only 漂移、正确错误码/details、
filter revision 未变、零 session”持久回归；保留连续人物 rename 的 catalog-stale 回归和 filter-first 优先级。
修复后 C2 仅短审该错误面及四条直接回归。

## Latest third fix, retry-exhaustion error surface — P3-07C (2026-09-21)

**P3-07C 第三轮修复（DS-H 重试耗尽错误面分类）：已应用，等待 Sol 短审补轮。** 见
[修复报告 §11](reports/P3-07C-dsh-canonical-fix.md)。仍属 P3-07C，未生成 P3-08、未标 PASS。

改动面**只有 1 个生产文件**：`src/photo_search/search/service.py` 把 `_authority_holds()`（只返回布尔）改为
`_authority_drift() -> "filter" | "suggestion" | None`（先 filter，再仅对 `place_state=suggested` 看
suggestion，与 `_continue_session` 同序），并新增 `_publication_refusal()` 作为**重试耗尽**的唯一出口：
重新读取两个冻结计数器后按既有优先级分类——filter revision 不同（或请求不是 suggested）→
`CATALOG_FILTER_REVISION_STALE` / `CATALOG_FILTER_REVISION_CHANGED` + 既有两 revision details；否则 →
`PLACE_SUGGESTION_REVISION_STALE` / `SUGGESTION_REVISION_CHANGED` + `session_suggestion_revision` /
`current_suggestion_revision`（与续页路径既有形态一致，新搜索不带 `search_session_id`）。未新增错误码，
快照、`build_session()`/`register()`、三次有界重锚与发布闸门位置全部未改。

反例转持久回归 **+2 例（参数化）**：`test_an_exhausted_retry_names_the_counter_that_moved[suggestion|both]`——
每次 `_entries_of()` 后提交真实 suggestion bump（`both` 再叠一次真实人物 rename）使每次尝试都撞上变化；
`suggestion` 断言 409 `PLACE_SUGGESTION_REVISION_STALE`、details 恰为三键、`current > session`、
**filter revision 未变**、`moves == SEARCH_PUBLISH_ATTEMPTS`、**零 session**；`both` 断言
`CATALOG_FILTER_REVISION_STALE` 且 filter revision 确实增加，锁定 filter-first 优先级。既有的「每次人物
rename」用例保留为 catalog 类的对照。对照实测（只把 `_publication_refusal` 换回旧出口）：旧出口返回
`CATALOG_FILTER_REVISION_STALE` 且 `session/current filter revision = 2/2`（自相矛盾），修复后返回
`PLACE_SUGGESTION_REVISION_STALE` 且 `2 → 3`；两次都恰好 3 次有界尝试。

实测：完整 JUnit `tests=1341 failures=0 errors=0 skipped=48`（220.7 s，`test-results/p3-07c-fix3-full.xml`，
第二轮 1339 → +2）；第三轮专项批 71 例 0 skip（`test-results/p3-07c-fix3-focused.xml`）；Ruff、diff-check、
lock-check 全 clean。未改 ADR/companion、migration、`schema_version` 13、依赖 95 包、`PhotoSearch.spec`、
前端、`places/**`、`geo/**`、`ocr/**`、`ingestion/**`、`workers/**`、`face/**` 与 `persons/**` 语义。

下一步：**Sol 短审补轮（第三次）**——独立新会话 **C2 / GPT-5.6 Sol / high** 只审该错误面分类与四条直接
回归（输入为修复报告 §11.5 交接包）；通过后才标 P3-07C PASS 并生成 **P3-08**。未验证项（多进程压测、
30 万下重试成本、`search_assets()` 直接调用者、C 级打包、UI）见报告 §7。请手动切换模型/推理档后继续。

## Latest final Sol short re-review — P3-07C closure (2026-09-21)

**P3-07C：PASS（C2 final short re-review）。** 最终结论见
[阶段 B 报告 §9](reports/P3-07C-sol-focused-review.md)。第三轮修复的 `_authority_drift()` 与
`_publication_refusal()` 正确保留 filter/suggestion 的独立错误面与 filter-first 优先级；没有新增错误码、
revision、migration、依赖或人物缓存。

Sol 独立复跑 5 个直接案例（suggestion-only、both、连续 rename、late merge、单次 suggestion bump），JUnit
`test-results/p3-07c-sol-final-rereview.xml` 为 5 tests / 0 failures / 0 errors / 0 skipped；DS-H 完整 JUnit
`test-results/p3-07c-fix3-full.xml` 为 1341 / 0 / 0 / 48 environment skips；Ruff、lock-check、diff-check clean。
canonical、revision、缓存失效三条必审边界全部通过。

下一任务为 **P3-08A — 新旧 `face_id` 可审查映射契约与 P3 故障验收矩阵冻结**，任务卡
[docs/tasks/P3-08A.md](tasks/P3-08A.md)。由于 mapping authority 尚未冻结，而 P3-08 还包含实现、D 级故障全集、
真实浏览器与独立两阶段终审，按粒度拆为 P3-08A/B/C；P3-07B 报告 §6 的 merge target 高 id undo 缺陷进入
P3-08B 必修范围。P3-08A 阶段 A 推荐 **DS-H / DeepSeek V4.1 Flash / 高推理**，请手动切换后继续。

Model switching remains manual; the repository only records the recommended model per stage.

## Latest contract review, stage A — P3-08A DS-H contract draft (2026-09-22)

**P3-08A 阶段 A：`ENGINEERING CONTRACT REVIEW PASS / SOL FREEZE PENDING`。** 见
[P3-08A 阶段 A 报告](reports/P3-08A-dsh-contract-review.md)。起点 `7dd392d`（工作树干净），本阶段只新增
四个文件并更新状态：ADR-0010（**Proposed**）、
[p3-face-redetection-contracts.v1.json](decisions/contracts/p3-face-redetection-contracts.v1.json)、
[p3-acceptance-matrix.v1.json](decisions/contracts/p3-acceptance-matrix.v1.json)（56 条稳定 ID 场景）与
[契约测试](../tests/unit/test_p3_face_redetection_contracts.py)（33 例）。

**冻结要点。** 映射身份是 `mapping_id = sha256(canonical JSON{asset_id, old_face_id, new_face_id,
old_spec_revision, new_spec_revision})`，跨资产候选永不生成，ordinal/cluster/`person_id` 不得冒充身份；
候选由方向修正后的 bbox + 5 landmarks 几何计算（合格 `iou>=0.30` 且 `landmark_ratio<=0.25`，高强度
`iou>=0.50` 且 `landmark_ratio<=0.12`，低质量脸永不 `HIGH`），**服务端永不自动确认**；携带人工知识的
旧脸在 `OPEN` 期间对其候选新脸施加**临时保持**，实现方式是扩展 `human_controlled_face_ids()` 的来源，
不新增第四种 control mode。确认按“每新脸至多一个继承者、其余保守 `MANUAL_UNASSIGNED`、exclusion 取并集、
肯定/否定冲突整批拒绝、已被人工编辑的目标拒绝覆盖”的矩阵转移人工知识；审核状态写进既有
`person_operations`（新增 `FACE_MAPPING_CONFIRM`/`FACE_MAPPING_REJECT`/`FACE_MAPPING_GROUP`），全局
`request_id` 幂等门先于 revision 检查，撤销取 `max(before, live)+1` 且不倒退；只有人工事实实际变化时才
推进一次 `catalog_filter_revision`（cause 仍为 `PERSON_CHANGED`），不写图片向量 `index_outbox`。
API 冻结 11 个错误码与只读审核面，响应/错误/日志不含 bbox、landmark、IoU、向量、confidence、路径、坐标、
内部快照。P3-07B §6 的 merge/undo 顺序缺陷被冻结为 P3-08B 必修不变量（两点式恢复、影响集合严格来自快照、
五条回归）。

矩阵覆盖主架构 §12.4 的人物第 **8、14～22** 项与地点交叉第 **29～33** 项，并含映射生命周期（生成、
确认、拒绝、撤销、重启、重扫、升级失败、回退、晚到 worker、备份恢复）、人物/地点交错顺序、
merge/undo 缺陷族与真实浏览器闭环计划。证据状态为 `existing 16` / `must_rerun 10` /
`not_implemented 30`，契约测试禁止把未跑项写成 PASS。

实测：契约专项 `test-results/p3-08a-contract.xml` **33 tests / 0 failures / 0 errors / 0 skipped**；完整
`tests/unit` `test-results/p3-08a-unit.xml` **792 / 0 / 0 / 0**；Ruff、`git diff --check`、`uv lock --check`
（95 packages）全 clean。`.pytest-tmp` 对受限令牌仍为 `WinError 5`（既有环境事实），完整单测经已授权的
正常 Windows 权限路由执行，basetemp 仍在项目内，**未改 ACL**。未验证项（30 万容量下候选成本、`0014`
迁移耗时、多进程并发、真实权重的升级/回退、C 级打包、真实浏览器闭环）记录在阶段 A 报告 §7。

下一阶段为 **P3-08A 阶段 B — C2 / GPT-5.6 Sol / high 聚焦终审并冻结**（输入为阶段 A 报告 §9 的压缩交接包，
只审未确认映射不转移人工 authority、事务/revision/幂等/撤销边界、身份/歧义/升级回退/备份恢复、API 与故障
矩阵是否足以直接生成 P3-08B，以及 §10 缺陷是否被正确纳入）。通过后才把 ADR-0010 转 Accepted、输出
`docs/reports/P3-08A-sol-contract-freeze.md`、标 P3-08A PASS 并生成 `docs/tasks/P3-08B.md`。**P3-08B 未生成。**
请手动切换模型/推理档后在新会话继续。

## Latest stage B review and DS-H correction — P3-08A (2026-09-22)

**P3-08A 阶段 B 初次终审：`SOL FOCUSED REVIEW FAIL / DS-H CONTRACT CORRECTION REQUIRED`。** 见
[阶段 B 报告](reports/P3-08A-sol-contract-freeze.md)。Sol high 指出四条普通且可界定的契约缺陷：①`1 → N`
允许把人工人物 authority 复制到多个新脸（契约测试还断言了与 ADR §12 相反的分支）；②临时保持没接到真实
自动关联写门（`face/index.py::_reconcile_asset()` 事务内读的是 `_manually_locked_face_ids()`），且
`face_association_queue` 在关联遍末尾被无条件删除，解除保持后没有恢复路径；③`MAP-001` 的“同内容同 spec
未变化重跑会产生新脸”与稳定 `face_id` 冲突，且未拒自映射；④error `details`、`candidate_state` 与候选
数组、`limit`/`fm1` 游标都不是闭集。ADR-0010 未转 Accepted，P3-08A 未 PASS，P3-08B 未生成；不升级
DeepSeek max（四条均可由最小反例界定）。

**阶段 B 修复（DS-H）：`CORRECTIONS APPLIED / SOL SHORT RE-REVIEW PENDING`。** 见
[修复报告](reports/P3-08A-dsh-contract-fix.md)。改动面严格限于 Sol 允许集合：ADR-0010、两份 companion、
契约测试与报告/任务卡/状态元数据；未改 `src/photo_search/**`、migration、依赖、`PhotoSearch.spec`、前端与
既有测试语义。修复要点：

- **双向继承基数**（ADR §4.1）：每个旧脸至多一个 `inherits_person=true`
  （`multiple_targets_without_single_inheritor`），每个新脸跨 group 至多一个继承来源；允许零继承者的保守
  分支（目标按 §4.3 处理）。
- **保持与队列**（ADR §3.4、§5.3、§7、§9）：显式区分**检测行保留集合**（`persist_face_document()` 的
  删除门）与**自动关联保持集合**（`_reconcile_asset()` 写事务内与 `_manually_locked_face_ids()` 同点读）；
  冻结判定顺序“写/退休 → 候选 → 失效 `STALE` → 保持”；冻结 `face_association_queue` 在保持期内保留、
  在 confirm/reject/undo 释放时**重新排队**且决策事务不替用户选人物。
- **身份边界**（ADR §2.1、§3.1、§7）：自映射永不生成（提交答 `FACE_MAPPING_NOT_FOUND`）；v1 的唯一
  生成面是跨世代（`old_spec_revision != new_spec_revision`）；同内容同 spec 未变化重跑**零新脸、零映射行、
  零新 group**；v1 不定义同 spec 的新旧映射。
- **公开闭集**（ADR §8.1、§8.2）：逐码冻结 `details` 允许/必填键、`conflicts` 形状、`candidate_state` ↔
  候选数组/计数约束、`limit` 1..200（`maximum` 进入 schema）与 `fm1` 游标（越界 limit 答
  `VALIDATION_ERROR`，外来游标答既有 `INVALID_CURSOR`）。
- 矩阵扩到 **65 条**（新增/更新 `MAP-001` 跨世代夹具与 `MAP-027`～`MAP-035`），契约测试扩到 **43 例**；
  四条反例探针修复后分别被拒（`at most one inheritor per old face`、`$.details: unexpected bbox`、
  `group: UNIQUE_HIGH needs exactly one HIGH candidate`、`$.limit: maximum` +
  `page: next_cursor must decode to fm1:<old_face_id>`）。阶段 A 报告 59→56 的计数笔误同轮更正。

实测：契约专项 `test-results/p3-08a-contract-fix.xml` **43 tests / 0 failures / 0 errors / 0 skipped**；
完整 `tests/unit` `test-results/p3-08a-unit.xml` **802 / 0 / 0 / 0**；Ruff（首轮 I001 已修）、
`git diff --check`、`uv lock --check`（95 packages）全 clean。`.pytest-tmp` 的 `WinError 5` 环境事实沿用，
未改 ACL，未触碰真实照片库。

下一阶段仍为 **P3-08A 阶段 B 短审 — C2 / GPT-5.6 Sol / high**：只审四条失败边界及其直接回归（输入为修复
报告 §9）。通过后才把 ADR-0010 转 Accepted、写出阶段 B 补轮结论、标 P3-08A PASS 并生成
`docs/tasks/P3-08B.md`。**P3-08B 未生成。** 请手动切换模型/推理档后在新会话继续。

## Latest focused review, stage B — P3-08A Sol contract freeze (2026-09-22)

**P3-08A 阶段 B：`SOL FOCUSED REVIEW FAIL / DS-H CONTRACT CORRECTION REQUIRED`。** 详见
[阶段 B 报告](reports/P3-08A-sol-contract-freeze.md)。ADR-0010 保持 Proposed，P3-08A 未 PASS，P3-08B 未生成。

四个最小失败边界：① ADR §12/MAP-007 要求一个旧脸在 `1→N` 中至多一个继承者，但契约测试明确接受两个不同
新脸都 `inherits_person=true`；② §3.4 把临时保持只接到 `human_controlled_face_ids()`，实际自动关联写门使用
`_manually_locked_face_ids()` 且处理后删除 association queue，解除保持后的重试与候选失效顺序也未冻结；
③ MAP-001 要求同内容同 spec 重跑写入“新脸”，与稳定 `face_id`/既有幂等重跑语义冲突且未拒绝 self-mapping；
④ companion 允许隐私字段进入 error details、允许 UNIQUE_HIGH 携带两个 WEAK 候选，并接受 limit 999 与非
`fm1` 游标。阶段 A 报告末尾的“59 条”也是计数笔误，实际矩阵为 56 条。

现有契约套件独立复跑仍为 `test-results/p3-08a-sol-review.xml` **33 / 0 / 0 / 0**，说明缺陷在冻结规则与
断言覆盖本身，不是已有测试红灯。下一步回到 **DS-H / DeepSeek V4.1 Flash / 高推理**，仅修 ADR-0010、两份
companion、contract tests 与交接元数据；不得改生产代码/migration/依赖/前端，不得生成 P3-08B。本轮问题均已
有最小反例，未命中 V3.1 §2.2 的 max 条件。修复后 C2 / Sol high 只短审这四条失败边界及直接回归面。

## Latest first Sol short re-review after DS-H correction — P3-08A (2026-09-22)

**P3-08A：`SOL SHORT RE-REVIEW FAIL / SECOND DS-H CONTRACT CORRECTION REQUIRED`。** 详见
[阶段 B 报告 §8～§9](reports/P3-08A-sol-contract-freeze.md)。第一轮四条缺陷中的双向继承基数、
unchanged/self-mapping 身份边界，以及 candidate-state / limit / `fm1` cursor 已闭合；剩余两个普通且可界定的
阻塞点：

- ADR §3.4 把 `undo → OPEN` 写成释放保持，而 §5.3、§12 与 `MAP-032` 要求重新施加保持并禁止自动归类；
- companion 只关闭了错误 `details` 的全局键并集，没有逐 code 关闭：跨 code 的安全键、空
  `FACE_NOT_FOUND` details、带 `old_face_id` 的 `INVALID_CURSOR` details 仍通过。

独立复跑 `test-results/p3-08a-sol-rereview.xml` 为 **43 / 0 / 0 / 0**，Ruff、diff-check、lock-check clean；
全绿说明现有用例缺少上述两个负向断言。ADR-0010 仍为 Proposed，P3-08A 未 PASS，P3-08B 未生成；不升级
DeepSeek max。下一步由 DS-H 只修这两个契约点、直接回归与交接元数据，修复后 Sol 只复核两个反例。

## Latest second correction — P3-08A (2026-09-22, DS-H)

**第二轮契约修正：`CORRECTIONS APPLIED / SOL FINAL SHORT RE-REVIEW PENDING`。** 见
[修复报告 §11](reports/P3-08A-dsh-contract-fix.md)。改动仍严格限于 Sol 允许面（ADR-0010、两份 companion、
契约测试与交接元数据），未改 `src/photo_search/**`、migration、依赖、`PhotoSearch.spec`、前端或既有测试语义。

- **`undo → OPEN` 语义**：ADR §3.4 把释放集合显式收窄为“只有 `confirm` / `reject`（以及 `confirm` 未选
  候选）”，并新增独立条款“`undo` 回到 `OPEN` 不是释放”——它恢复 `OPEN` 保持（候选新脸重新被关联遍跳过），
  只保留/重新排队队列行等待下一次决策。新增静态契约断言 `test_undo_is_never_part_of_the_release_set`
  （释放条款切片内不得出现 `undo`/“撤销”），避免回归。
- **错误 `details` 逐码闭集**：删除“所有安全键的并集 + 逐码追加必填键”的弱形状，`MappingApiError` 改为
  **12 个逐码分支的严格 `oneOf`**，每个分支同时冻结 `code`、允许键与必填键（`additionalProperties: false`）；
  `FACE_NOT_FOUND` 用 `anyOf` 要求 `old_face_id` / `new_face_id` 至少一个；`INVALID_CURSOR` 的 `details`
  必须是空对象。ADR §8.2 同步写明禁止回退到键并集写法。
- Sol 的三个反例探针现在分别返回 `['$: oneOf matched 0']`（`FACE_MAPPING_NOT_FOUND + person_id`、
  `FACE_NOT_FOUND {}`、`INVALID_CURSOR + old_face_id`），正向对照仍返回 `[]`。

实测：契约专项 `test-results/p3-08a-contract-fix2.xml` **58 tests / 0 failures / 0 errors / 0 skipped**；
完整 `tests/unit` `test-results/p3-08a-unit.xml` **817 / 0 / 0 / 0**；Ruff、`git diff --check`、
`uv lock --check`（95 packages）全 clean。矩阵仍为 65 条（本轮未增删场景）。未升级 DeepSeek max
（两点均有最小反例，未命中 V3.1 §2.2）。

下一步仍为 **P3-08A 阶段 B 终审 — C2 / GPT-5.6 Sol / high**：只复核 §11.1 的 undo/hold 语义与 §11.2 的
逐码 `details` 闭集（含正向对照）。通过后才把 ADR-0010 转 Accepted、补阶段 B 结论、标 P3-08A PASS 并生成
`docs/tasks/P3-08B.md`。**P3-08B 未生成。** 请手动切换模型/推理档后在新会话继续。

## Latest final Sol short re-review — P3-08A closure (2026-09-22)

**P3-08A：PASS / ADR-0010 ACCEPTED。** 最终结论见
[阶段 B 报告 §10](reports/P3-08A-sol-contract-freeze.md)。第二轮 DS-H 修正后的两个直接边界全部通过：
`undo → OPEN` 明确恢复保持而不是释放；`MappingApiError` 的 12 个 code 使用逐码严格 `oneOf`，三个跨码/空对象
负向反例均被拒，两个正向对照仍通过。

Sol 独立复跑 `test-results/p3-08a-sol-final-rereview.xml` 为 **58 tests / 0 failures / 0 errors / 0 skipped**；
Ruff、`git diff --check`、`uv lock --check`（95 packages）均 clean。ADR 状态及契约测试断言已同步转为 Accepted。
本任务未改生产代码、migration、依赖、前端、ACL 或真实照片库。

下一任务为 **P3-08B — 新旧人脸映射实现、merge/undo 顺序修复与 P3 故障/浏览器闭环**，任务卡
[docs/tasks/P3-08B.md](tasks/P3-08B.md)。推荐 **DS-H / DeepSeek V4.1 Flash / 高推理**；请在 Codex 中手动
切换模型/推理档后继续。P3-08B 完成后才进入 P3-08C 的独立 DS-H 主审与 Sol 聚焦终审。

## Latest implementation — P3-08B face-redetection mapping and fault/browser closure (2026-09-22)

**P3-08B：PASS（B + D + 一次记录的真实浏览器闭环）。** 详细报告见
[P3-08B 报告](reports/P3-08B-face-redetection-mapping-and-fault-closure.md)。起点 `c7fa68b`（工作树干净）。

本卡实现 ADR-0010 的全部生产面：`0014` 两表（`face_redetection_groups` /
`face_redetection_mappings`）与共享操作日志的第三次 `CHECK` 重建（`FACE_MAPPING_CONFIRM` /
`FACE_MAPPING_REJECT` / `FACE_MAPPING_GROUP`），`schema_version 13 → 14`；候选生成与失效在被写入脸文档的
**同一事务**内完成并严格“退休 → 候选 → 失效 → 保持”；`persons/mapping.py` 落实稳定 `mapping_id`、
五个冻结几何常量、四态候选、永不自动确认、双向继承基数与保守未归类、肯定/否定冲突、目标受控拒绝、
幂等门先于 revision 检查、一次确认一次 `PERSON_CHANGED`（不写图片向量 outbox）与 `max(before,live)+1`
补偿撤销；`face/index.py` 在自动关联写事务内新增**临时保持**来源（与人工锁/活动排除同一时点读取）并把
`face_association_queue` 从“无条件删除”改为保持期保留、confirm/reject 释放后重排队，`undo → OPEN`
恢复保持；`/persons` 新增“重检测待确认”能力门控视图；三条 `/api/v1/face-mappings*` 路径与四个
`face_summary` 计数上线。

同时按 ADR-0010 §10 修复 P3-07B §6 的 merge/undo 顺序缺陷：撤销先把操作影响集合内的有效 `CONFIRMED`
行整体删除一次，再按 before 快照写回（`max(before,live)+1`，每张脸任何语句边界至多一条有效行），并用
“只有 live 与 before 真的不同”的收窄保证语义 no-op 的撤销仍零改动。浏览器闭环中发现并最小修复了一处
真实接缝缺陷：生产写入的 `bbox_json` 形状（`{"bbox":[…]}`）与 P3-06 裁剪读取（`[l,t,r,b]`）不一致，
导致真实检测行永远裁剪失败（`FACE_STATE_CONFLICT/bbox_not_a_list`）；`media/face_crop.py` 现同时接受
两种形状，坏行拒绝语义不变。

实测：专项 **`test-results/p3-08b-focused.xml` 108 / 0 / 0 / 0**（任务卡点名 11 个文件）；
完整 **`test-results/p3-08b-full.xml` 1450 / 0 / 0 / 48**（199.1 s，48 skip 全为未装 pinned 权重的
环境跳过）；Ruff、`git diff --check`、`uv lock --check`（95 packages）均 clean。65 条验收矩阵的
ID→证据映射与真实结论见报告 §5。真实浏览器 smoke（Codex 内置浏览器，非 Playwright/Selenium PASS）：
`/persons` 确认 → 全部视图 → 撤销 → 后编辑后撤销冲突 → 拒绝，`/places` 目录纠错，
`/search` 地点筛选重搜 + 人物纠错后旧会话明确 stale，360×640 覆盖无横向溢出、键盘可达、
DOM/URL/访问日志隐私扫描干净；详见报告 §6。

未验证项（报告 §7，不得当 PASS）：`/search` 页面**没有人物条件输入**（P3-07B 只交付了 API 与地点筛选），
因此矩阵 BROW-002 的“人物+地点 AND 浏览器重搜”未执行，API 级等价证据为
`tests/integration/test_p3_person_search_filter.py` 与本卡 `test_p3_person_place_interleave.py`；
`/places` 的资产归属页面入口只服务待确认建议队列，本夹具无 GPS 事实故未走该路径；
30 万容量/多进程/真实权重升级回退/`0014` 实测迁移耗时/C 级打包未执行。
`.pytest-tmp` 对受限令牌仍为 `PermissionError [WinError 5]`（既有环境事实，未改 ACL），本卡测试改用项目内
全新 basetemp 并经正常 Windows 权限路由执行。

下一任务为 **P3-08C — P3-08B 的独立两阶段审查**，任务卡 [docs/tasks/P3-08C.md](tasks/P3-08C.md)：
**阶段 A：独立新会话 DS-H / DeepSeek V4.1 Flash / 高推理做全量工程主审**（完整 diff、migration、
测试证据、ADR-0010/任务卡一致性、跨模块影响、未验证项裁定），仅命中 V3.1 §2.2 六类具名条件才建议升
DS-MAX；**阶段 B：独立新会话 C2 / GPT-5.6 Sol / high 聚焦终审**（临时保持与写门、人工知识转移矩阵、
revision/幂等/撤销、`STALE` 与队列生命周期、公开闭集与隐私的最小反例）。两阶段必须分别在两个新会话中
完成；模型切换由用户在 Codex 中手动完成。

## Latest independent engineering review, stage A — P3-08C DS-H (2026-09-22)

**P3-08C 阶段 A：`ENGINEERING REVIEW FAIL`。** 见
[阶段 A 报告](reports/P3-08C-dsh-engineering-review.md)。起点 `c7fa68b` + P3-08B 全部未提交改动
（`git status --short` = 25 modified + 18 untracked）。本卡是审查卡，未改生产代码/migration/ADR/companion/
既有测试语义；临时反例探针放在被忽略的 `.pytest-basetemp-p308c/`。

**通过面。** `0014` 两表（真实外键 `old_face_id`、故意无外键 `new_face_id`、自映射 `CHECK`、
`UNIQUE(old_face_id,new_face_id)`、闭集 `state`/`candidate_state`）与第三次日志重建正确，`0001`～`0013`
逐字节未动，`schema_version 13 → 14`；ADR-0010 §2.1～§2.3、§3.1、§3.2、§3.4、§4、§5.1～§5.4、§6、§7、§10
的对应实现均有一致证据（两谓词分离与同事务同时点读取、判定顺序、释放集合只含 confirm/reject、`undo → OPEN`
恢复保持、双向继承基数、保守 `MANUAL_UNASSIGNED`、肯定/否定冲突整批拒绝、幂等先于 revision、
`max(before,live)+1`、一次人工事实变化一次 `PERSON_CHANGED` 且不写图片向量 outbox）；§10 顺序修复用
`_faces_needing_link_restore()` + `_clear_effective_links()` + `forced_keys`/`revision_hints` 落地，且
"语义 no-op 的撤销仍零改动"的既有用例未改通过。`media/face_crop.py` 的读取容忍经探针确认不掩盖坏数据
（arity/degenerate/not-a-list 仍原码拒绝）。地名包示例上界 `13 → 14` 恰当（`content_sha256` 是文件清单
哈希，与兼容区间无关）。证据：专项 `test-results/p3-08c-dsh-focused.xml` **108 / 0 / 0 / 0**；完整
`test-results/p3-08c-full.xml` **1450 / 0 / 0 / 48**（189.0 s，48 skip 为未装 pinned 权重的环境跳过）；
Ruff、`git diff --check`、`uv lock --check`（95 packages）全 clean。

**失败面（必须修）。** 映射错误面没有做到 §8.2 的"逐码旁闭"：真实 HTTP 响应
`GET /api/v1/face-mappings?limit=999` 答 `details={"field":"limit","maximum":200}`、
`GET /api/v1/face-mappings/not-a-face-id` **完全没有 `details`**、
`?limit=many` 与 `?state=decided` 透传通用 `details={"errors":[...]}`。四个响应都被本卡自带校验器
（`contract_errors(body, "MappingApiError")`）判为 `$: oneOf matched 0`：`VALIDATION_ERROR` 的冻结允许键
只有 `reason`/`old_face_id`/`new_face_id`/`asset_id` 且 `reason` 必填。根因是
`persons/read.py:157-168`（`check_mapping_limit` 自造 `field`/`maximum`）、`persons/read.py:389-397`
（state 校验同形）、`persons/read.py:429-439`（`mapping_group()` 丢掉 `CatalogError` 的 `reason`）与
`api/routes.py:951-972`（`state: Literal[...]`/`limit: int | None` 让传输层先答）。现有用例只断言
status 与 `code`，从未读取这些响应的 `details`，因此全绿没有拦住。修复面固定为上述两文件 + 两处测试断言，
不得放宽 companion、不得改 ADR/迁移/前端/其它端点约定。

**需裁定面。** 探针显示"同一对脸在 `STALE` 之后被再次写入"时，映射行永久停留 `STALE`
（`live_candidate_rows()` 排除 `STALE`，`_upsert_mapping_row()` 只刷新证据/`updated_at`），group 报
`NO_CANDIDATE` 且该对脸无法确认；而 §3.3 把 `NO_CANDIDATE` 定义为"本次检测没有匹配脸"、§7 回退条要求
"重新建候选"。该行为保守（不转移/不创建/不删除 authority、不推进 filter revision），但必须在修复轮内
二选一并落盘：在契约轮把 `STALE` 注明为终态，或实现"存活且合格即复活 + 新用例"。

**未验证项裁定。** P3-08B 报告 §7 五条均可接受：`BROW-002` 的 `/search` 人物条件输入属 P3-07B 未交付的
新界面范围（API 级等价证据存在，建议登记为 P3-08D 候选，不在本卡夹带）；`/places` 资产归属入口属地点
功能面；30 万容量/多进程/真实权重升级回退/`0014` 实测耗时/C 级打包不在本卡验收等级（B + D + 记录式
浏览器闭环）内，报告也未标 PASS；矩阵建议的 `test_p3_person_search_sessions.py` 在仓库不存在，§5 已注明
替换为既有 `test_p3_person_search_filter.py`；窄宽检查为内置浏览器 viewport 覆盖。

**下一步（不生成 P3-08D）。** 回到 **DS-H / DeepSeek V4.1 Flash / 高推理**新会话，只修
`persons/read.py`、`api/routes.py` 与对应两处测试断言的错误面，并裁定 `STALE` 终态；修复后由
**C2 / GPT-5.6 Sol / high** 新会话只短审这三条边界（真实 422 的逐码闭集、
保持/释放/队列与 §10 顺序修复未被波及），通过后才标 P3-08C PASS 并生成 `docs/tasks/P3-08D.md`。
未升级 DeepSeek max：两条问题都有最小反例与明确修复面，未命中 V3.1 §2.2 任何具名条件。
模型切换始终由用户在 Codex 中手动完成。

## Latest correction round — P3-08C (2026-09-22, DS-H)

**P3-08C 修复轮：`CORRECTIONS APPLIED / SOL SHORT RE-REVIEW PENDING`。** 见
[修复轮报告](reports/P3-08C-dsh-correction-round.md)。起点 `c7fa68b` + P3-08B 全部未提交改动；未改
ADR-0010、两份 companion、65 条矩阵、migration `0001`～`0014`、依赖、`PhotoSearch.spec`、`web/**`。

- **阶段 A §5.4 的四个真实 422 反例全部闭合**：`?limit=999`/`?limit=many` 答
  `details={"reason":"limit"}`、`?state=decided` 答 `{"reason":"state"}`、坏 `old_face_id` 答
  `{"reason":"old_face_id"}`；根因（`check_mapping_limit` 自造 `field`/`maximum`、state 校验同形、
  `mapping_group()` 丢 `reason`、`api/routes.py` 让传输层先答）按固定面收口，`list_face_mappings()` 的
  `state`/`limit` 改为 `str` 透传、由读取层校验；`PersonService.mapping_page()` 同步改为原样转发。
- **同类第五处一并修好**：`INVALID_CURSOR` 的冻结形状是**空对象**，共享信封却把空 `details` 当可选项
  丢弃，真实 422 因此缺必填字段。不能全局改信封（`PlaceApiErrorDetails` 只接受 7 键对象或 `null`，且阶段 A
  明令不得改其它端点约定），故新增显式 `ApiError(closure_details=True)` 选项，仅映射读取层使用；
  `SESSION_REQUIRED`、`/persons`、`/faces`、`/places` 响应实测逐字节不变，`/face-mappings` 的
  `INVALID_CURSOR` 补上 `details={}`。
- **§6.3 裁定：实现复活（选项 2）**。同一对脸存活且本次合格时，`OPEN` group 的映射行由 `STALE` 回到
  `SUGGESTED`（`+1` revision，不倒退），group 由既有 `_recompute_group()` 重算 `candidate_state`；
  已决策 group 不复活（§7 不许重检测改写人工决定），重复提交仍答 `FACE_MAPPING_CONFLICT`。被改写的既有
  断言只有 `test_removed_candidate_becomes_stale` 末段（原钉住“复写不复活”），并新增
  `test_a_decided_group_is_not_reopened_by_a_reappearing_pair`。

实测：专项 `test-results/p3-08c-fix-focused.xml` **109 / 0 / 0 / 0**；完整
`test-results/p3-08c-fix-full.xml` **1451 / 0 / 0 / 48**（227.3 s，48 skip 全为未装 pinned 权重的环境
跳过）；Ruff、`git diff --check`、`uv lock --check`（95 packages）全 clean；真实 HTTP 闭包探针 6/6
`VALID`（阶段 A 的四个反例 + 外来游标 + 未知 group），信封范围探针证明 4 个其它端点逐字节不变。

**P3-08C 仍未 PASS，`docs/tasks/P3-08D.md` 仍未生成**；未升级 DeepSeek max（两点都有最小反例与固定
修复面，未命中 V3.1 §2.2）。下一步为**阶段 B — C2 / GPT-5.6 Sol / high 新会话**：只读修复轮报告 §6 的压缩
交接包，短审“真实 422 的逐码闭集（含新引入的 `closure_details` 机制）、保持/释放/队列与 §10 顺序修复未被
波及、`STALE → SUGGESTED` 复活与 `OPEN` 边界”三条；通过后才标 P3-08C PASS 并生成
`docs/tasks/P3-08D.md`。模型切换始终由用户在 Codex 中手动完成。

## Latest final Sol short re-review — P3-08C closure (2026-09-22)

**P3-08C：PASS（C2 FINAL SHORT RE-REVIEW）。** 最终结论见
[Sol 聚焦终审报告](reports/P3-08C-sol-focused-review.md)。阶段 B 严格只审修复轮 §6 点名的三条边界，没有
重复阶段 A 全量主审：

- 真实 HTTP 的 `limit=999`、`limit=many`、`state=decided`、坏 `old_face_id`、外来游标、未知 group、
  `FACE_MAPPING_STALE` 与 `FACE_NOT_FOUND` 均落在 ADR-0010 §8.2 的逐码严格 `oneOf`；新增
  `closure_details` 默认关闭，只在映射 `INVALID_CURSOR` 需要必填空对象时显式启用，未改变其它端点信封。
- 保持仍与人工锁/活动排除在自动关联写事务的同一时点读取；confirm/reject 释放后重排队，`undo → OPEN`
  恢复保持；§10 merge/undo 仍先整体清除影响集合内有效链接，再按 before 快照前向恢复。
- 存活且重新合格的 pair 只在 group 仍为 `OPEN` 时由 `STALE → SUGGESTED`，revision `+1` 且不自动确认；
  已决策 group 不被重检测重开，再次提交仍拒绝。

Sol 独立聚焦 JUnit `test-results/p3-08c-sol-focused.xml` 为 **22 tests / 0 failures / 0 errors / 0 skipped**。
首次 basetemp 尝试遇到既有 Windows ACL `PermissionError [WinError 5]`；未改 ACL，使用全新的项目内 basetemp
并经正常权限路由后通过。阶段 A/修复轮完整证据 **1451 / 0 / 0 / 48 environment skips** 仅引用、未在 Sol
阶段重复运行；`git diff --check` 与 `uv lock --check`（95 packages）clean。未触碰真实照片库。

P3-08C 未把范围外证据改写为 PASS：30 万容量、多进程、真实权重升级/回退、`0014` 实测迁移耗时与 C 级
打包仍按既有裁定后置；P3-08B 修复轮未改前端。`/search` 页面尚无人物条件输入，所以 `BROW-002` 的
“人物 any/all × 地点 AND → 人物纠错后旧会话 stale → 重搜”仍未执行，现已独立排入下一任务。

下一任务为 **P3-08D — 搜索页人物筛选与 BROW-002 真实浏览器闭环**，任务卡
[docs/tasks/P3-08D.md](tasks/P3-08D.md)，推荐 **DS-H / DeepSeek V4.1 Flash / 高推理**。它只复用已冻结的
ADR-0009/API 做前端接线和记录式浏览器验证；若需要修改 API/人物 authority，必须停止并另行裁定。请在
Codex 中手动切换模型/推理档后继续。

## Latest implementation — P3-08D search-page person filter and BROW-002 browser closure (2026-09-22)

**P3-08D：PASS（B + 一次记录的真实浏览器 smoke；`SOL_NOT_REQUIRED`）。** 详细报告
[P3-08D 报告](reports/P3-08D-search-person-filter-browser-closure.md)。起点 `c7fa68b` + P3-08B/P3-08C 的全部
未提交改动（开始前 25 modified + 18 untracked，全部保留）。

`/search` 页面接入 P3-07B 已实现、但 UI 缺失的人物条件：人物选择只来自受会话保护的
`GET /api/v1/persons?kind=all&limit=25[&cursor=…]`（一页一读、不物化全表、不读库、不自行解析 `merged_into`），
选项显示「名称或未命名占位 + `#person_id` + 照片/脸计数」并带显式 `label[for]`；`person_ids`/`person_match`
只在真选中时按升序去重发送（未选人时 `all` 选项 disabled 且强制 `any`），成功后显示服务端 canonical 回显；
条件指纹（文本/来源/可用性/地点/后代/地点状态/人物集合与匹配方式/每页数量）变化后拒绝用旧游标续页并要求重搜；
schema <13 或人物读 API 不可用时人物控件明确禁用并说明原因；上传搜图请求不含任何人物字段。

同时把 `CATALOG_FILTER_REVISION_STALE` 的页面文案改为同时点名人物（该 revision 为人物/地点/可见性共用）。
改动面：`web/search.{html,css,js}`、`tests/unit/test_api_frontend.py`（+10 个静态契约用例）、
`docs/diagnostics/frontend-smoke-checklist.md` 新增 §8、新夹具
`tools/diagnostics/search_person_smoke_fixture.py`。未改 ADR-0009/0010、companion、API/search service、
persons/**、migration/schema、依赖、`uv.lock`、`PhotoSearch.spec` 或其它页面。

实测：专项 `test-results/p3-08d-focused.xml` **131 / 0 / 0 / 0**；完整 `test-results/p3-08d-full.xml`
**1461 / 0 / 0 / 48**（221.9 s，48 skip 全为未装 pinned 权重的环境跳过）；Ruff、`git diff --check`、
`uv lock --check`（95 packages）clean。记录式真实浏览器（Codex 内置浏览器，非 Playwright/Selenium PASS）：
`any`=资产 #1/#6、`all`=资产 #1、`人物 #1 × 地点 #1`=资产 #6（与夹具用生产 `search_request` 记录的期望集合
逐条一致）、分页 25→30 且已选 id 不丢、第二标签页改名后旧 continuation 明确
`CATALOG_FILTER_REVISION_STALE` 并保留条件、重搜得到更新后的 canonical 回显、清空人物恢复无人物条件、
同标签的 `#3`/`#4` 各自独立、键盘 `Tab`/`Space`/`Enter` 可达可用、窄宽（内置浏览器实际视口
`clientWidth=380`）无横向溢出；DOM/URL 无路径、无 token、无 person id，访问日志全在
`/api/v1/*`、`/search`、`/persons`、`/web/*`，`/geo` 命中 0，凭证只在启动器横幅与 `token=<redacted>` 出现。

未验证项（见报告 §7，不得当 PASS）：30 万容量下人物条件延迟、多进程会话、`/search` 自然语言解析器、
C 级打包；窄宽为内置浏览器真实视口（`viewport` 覆盖在本机不改变窗口尺寸），非设备级模拟；上传路径在无
图像编码器时只观察到结构化 409。环境事实：受限令牌下 pytest 与服务端都无法使用系统 `%TEMP%`
（既有 `WinError 5`），本卡全部测试与浏览器服务改经正常 Windows 权限路由（并把 `TEMP`/`TMP` 指向项目内
临时目录）执行，未改 ACL。

收口提交：**`d4afd96`**（P3-08B/C/D 合并提交，55 files；本卡工作树起点 `c7fa68b`）。该提交前已重跑
P3-08D 的 focused 前端契约套件（85 / 0 / 0 / 0）确认收口状态与证据一致。

本卡未改任何冻结协议语义，故按任务卡记录 `SOL_NOT_REQUIRED`；**P4 阶段由此开始**。下一任务为
**P4-00A — 拍摄时间/时区/回退来源/新 order 契约**，任务卡 [docs/tasks/P4-00A.md](tasks/P4-00A.md)：
**阶段 A 由独立新会话 DS-H / DeepSeek V4.1 Flash / 高推理草拟并工程预审契约**（A 级），随后
**阶段 B 必须由独立新会话 C2 / GPT-5.6 Sol / high 聚焦终审并冻结**（任务表点名 Sol，不可跳过）。
阶段 A 结束时状态只能写 `ENGINEERING CONTRACT REVIEW PASS / SOL FREEZE PENDING`，不得提前 PASS，
也不得生成 P4-00B。模型切换始终由用户在 Codex 中手动完成。

## Latest contract draft, stage A — P4-00A DS-H (2026-09-22)

**P4-00A 阶段 A：`ENGINEERING CONTRACT REVIEW PASS / SOL FREEZE PENDING`。** 见
[阶段 A 报告](reports/P4-00A-dsh-contract-draft.md)。起点 `8e48fc7`（工作树干净）。

本卡只冻结契约（A 级），交付 ADR-0011（**Proposed**）、伴随物
`docs/decisions/contracts/p4-time-contracts.v1.json`（修复轮后 SHA-256
`80189349bb4ea306225f9eae77ae9266c6d81c42de52c0b72bdc940a0702afd7`）与
`tests/unit/test_p4_time_contracts.py`（修复轮后 39 用例）；未改任何生产代码、migration、`schema_version`、
依赖、`uv.lock`、`PhotoSearch.spec`、`web/**` 或既有 companion，未读真实照片库。

冻结要点：`asset_metadata` 主键 + 六列与 §4.2 逐列一致；`taken_at_sort` 是**非负 civil 键**
（原点 `0001-01-01T00:00:00.000`，**不是** instant，偏移永不参与计算），展示值定宽且与键同序；来源
闭集 `EXIF_ORIGINAL → EXIF_DIGITIZED → EXIF_ROOT → FILE_MTIME` 逐级吞掉坏值、禁止扫描时间/当前时间
兜底，其中只有前两级是「拍摄时间」，`EXIF_ROOT`/`FILE_MTIME` 必须显示「文件时间」且不参与时间过滤；
新 `order=capture_desc` 使用 `c1` arity 4 独立游标并绑定**同一个** `catalog_filter_revision`
（失配 409 stale，不新造 revision）；时间条件以 `capture_from`/`capture_to_exclusive` 左闭右开进入
同一 AND 集合与同一 `query_hash`；新增 cause `CAPTURE_TIME_CHANGED`；`filters` 回显按
`SearchFiltersEchoV2`（ADR-0009 的 **11** 键 + 2 = 13 键）**加性取代**并声明任何既有 companion 文件
都不修改；新增错误码
`INVALID_TIME_RANGE` 只用既有 details 键；`0015_taken_time.sql` 只建表 + 索引 + 库级规则版本键、
`SUPPORTED_SCHEMA_VERSION 14 → 15`、`0001`～`0014` 逐字节不变、未抽取旧库的既有字段逐字不变；因为
`0015` 是打包数据资源（`collect_data_files('photo_search')` + `migration_files()` 连续覆盖要求），
**P4-00B 必须加 C 级打包验收**。P4-00B 前置清单（8 条 D 级故障注入 + 3 条 C 级打包 + 8 条记录式浏览器
步骤 + 命令模板）已写入 ADR §10。

实测：专项 `test-results/p4-00a-contracts.xml` **39 / 0 / 0 / 0**（修复轮后）；Ruff、`git diff --check`、
`uv lock --check`（95 packages）clean。环境事实：受限令牌下 pytest 的 basetemp/`tmp_path` 目录枚举仍是
既有 `PermissionError [WinError 5]`，本卡测试改经正常 Windows 权限路由执行（未改 ACL）；同一受限环境下
`ruff check .` 会打印一条 `os error 5` 遍历警告，正常权限路由下无警告且通过。

未验证项（不得当 PASS）：30 万容量下的 `capture_desc`/时间过滤延迟与最终索引列序、全库抽取耗时、
多进程并发、`0015` 实测迁移耗时、真实内部存储样本覆盖率、GTX 1660 实机；本卡也未跑
`tests/unit tests/integration` 全量套件（A 级不要求）。

阶段 A 收口状态为 `ENGINEERING CONTRACT REVIEW PASS / SOL FREEZE PENDING`；**阶段 B 判定
`FAIL / DO NOT FREEZE`**，四条边界不通过（见
[Sol 聚焦终审报告](reports/P4-00A-sol-contract-freeze.md)）：游标不变量未冻结、C 级判定错误、
`filters` 回显键数写成 13 + 2、`taken_time` 四态不是三计数纯函数。

## Latest correction round — P4-00A DS-H (2026-09-22)

**P4-00A 修复轮：`CORRECTIONS APPLIED / SOL SHORT RE-REVIEW PENDING`。** 见
[阶段 A 报告 §9](reports/P4-00A-dsh-contract-draft.md)。只改契约层（ADR-0011、P4 伴随物、契约测试、
报告与状态），未触碰生产代码、migration、`schema_version`、依赖、`uv.lock`、`PhotoSearch.spec`、
`web/**`、ADR-0001～0010、既有 companion 或既有测试语义。

- **边界 3**：`CaptureCursor` 改为三字段 `prefix`/`arity`/`values`，用 `prefixItems` 逐位冻结
  `known ∈ {0,1}`、`asset_id ≥ 1`、revision ≥ 0，并用 `allOf`+`if/then` 冻结尾部桶 sort 元素恒为 0；
  删除重复的 `catalog_filter_revision` 字段。阶段 B 的三条探针复跑结果由 `[]` 变为
  `$.values[0]: enum`、`$.values[1]: const`、`$.values[2]: minimum`，多余字段得到 `unexpected`。
- **边界 5**：ADR §9/§10 纠正为“`0015` 是打包数据资源”，删除“不触发 C 级”，并把 C 级真实打包 smoke
  （bundle 内 `0001`～`0015` 逐个 SHA-256 一致、`packaged_api_smoke.py`、打包 data root
  `schema_version=15`、缺件必须报 `SCHEMA_INCONSISTENT`）写入 P4-00B 前置清单。
- **边界 6**：`filters` 回显基数纠正为 **11 + 2 = 13**（P3 `SearchFiltersEcho.required` 实测 11 键）。
- **边界 7**：`extracted_assets` 定义改为“按当前 `taken_time_rules_version` 可信的已抽取资产数”，
  版本失配时与 `capture_time_assets` 一并规范化为 `0`，`state` 真正只是三计数纯函数；补失配最小反例。

修复轮证据：专项 `test-results/p4-00a-contracts.xml` **39 / 0 / 0 / 0**；Ruff、`git diff --check`、
`uv lock --check`（95 packages）clean；伴随物哈希更新为
`80189349bb4ea306225f9eae77ae9266c6d81c42de52c0b72bdc940a0702afd7`（ADR §13 同步）。阶段 B 判定通过的
边界 1/2/4 语义未变、无需重审。未命中 V3.1 §2.2 六类条件，不升 DS-MAX。

**P4-00A 仍未 PASS、ADR-0011 仍为 Proposed、`docs/tasks/P4-00B.md` 仍未生成。** 下一步为
**C2 / GPT-5.6 Sol / high 新会话**只短审上述四条失败边界及其直接回归面；通过后才把 ADR 转 Accepted、
标 P4-00A PASS、补状态并生成 `docs/tasks/P4-00B.md`。模型切换始终由用户在 Codex 中手动完成。

## Latest contract freeze — P4-00A PASS (2026-09-22)

**P4-00A：PASS（A 级契约冻结；非实现 PASS）。** [Sol 阶段 B 报告](reports/P4-00A-sol-contract-freeze.md)
已追加修复轮短审 §5，ADR-0011 状态转为 **Accepted**。

短审只复核首次失败的 4 条边界，均已闭合：`CaptureCursor` 用三字段形状与逐位/尾桶约束拒绝原反例并消除
双 revision；`0015` 明确是打包数据资源，P4-00B 必须执行 C 级真实冻结包验收；filters 回显纠正为
**11 + 2 = 13**；规则版本失配时可信抽取/拍摄计数规范化为 0，四态继续是块内三计数纯函数。首次已通过的
时区/civil 键、回退诚实性、单一 AND/revision/摘要语义未改。

Sol 复核证据：原反例探针分别得到 `enum`/`const`/`unexpected`/`minimum` 拒绝；专项 JUnit
`test-results/p4-00a-sol-rereview.xml` 为 **39 / 0 / 0 / 0**；Ruff、`git diff --check`、
`uv lock --check`（95 packages）均 exit 0。pytest 使用全新的项目内 basetemp 并走正常 Windows 权限路由，
未改 ACL。伴随物 SHA-256 为
`80189349bb4ea306225f9eae77ae9266c6d81c42de52c0b72bdc940a0702afd7`。

未验证项不变：30 万容量下的查询/索引形状、全库抽取耗时、多进程、真实 `0015` 迁移、真实内部样本、
GTX 1660；P4-00B 的 D + C + 记录式真实浏览器验收也尚未执行。

下一任务为 **P4-00B — 拍摄时间迁移、增量抽取、API/UI 与打包闭环**，任务卡
[docs/tasks/P4-00B.md](tasks/P4-00B.md)。推荐 **DS-H 实现 + 独立 DS-H 全量工程主审**；必须执行
**D + C + 记录式真实浏览器**。请在 Codex 中手动切换到 DS-H / DeepSeek V4.1 Flash / 高推理的新会话继续。

## Latest implementation — P4-00B taken-time migration, extraction, API/UI and packaging (2026-09-22)

**P4-00B：IMPLEMENTATION + ACCEPTANCE COMPLETE / INDEPENDENT DS-H ENGINEERING REVIEW PENDING
（未标 PASS，未生成下一卡）。** 详细报告
[P4-00B 报告](reports/P4-00B-taken-time-implementation.md)。起点 `8e48fc7` + P4-00A 全部未提交契约交付物；
本卡改动同样未提交（不加 commit）。

交付：`0015_taken_time.sql`（`asset_metadata` 主键 + 六列 + 表级 CHECK + capture 索引 + 库级
`taken_time_rules_version=1`，不回填、不碰 `0001`～`0014`）；`SUPPORTED_SCHEMA_VERSION 14 → 15`；
新的纯 CPU `taken_time_extract` 处理器（复用 P1-04 受控边界与 P2-02 调度，按 `EXIF_ORIGINAL →
EXIF_DIGITIZED → EXIF_ROOT → FILE_MTIME` 逐级吞掉坏值，civil key 不含偏移，观察守卫前后各一次，
提交事务内写行并**至多一次**推进 `CAPTURE_TIME_CHANGED`）；`GET /api/v1/assets` 的
`capture_desc`/`capture_from`/`capture_to_exclusive` 与条件性 `taken_time` 四态块；详情闭合
`taken_time` 对象（无 `taken_at_raw`）；`POST /api/v1/search` 的同一对条件进同一 AND 集合与同一
`query_hash`，filters 回显加性升级为 **11 + 2 = 13** 键；`/` 与 `/search` 的拍摄时间范围、
`capture_desc`、文件时间标注与诚实空结果提示；`taken_time` CLI 与浏览器 smoke 夹具。

实测：专项 `test-results/p4-00b-focused.xml` **268 / 0 / 0 / 0**；完整
`test-results/p4-00b-full.xml` **1598 / 0 / 0 / 48**（196.7 s，48 skip 全为未装 pinned 权重的环境跳过）；
Ruff、`git diff --check` clean，`uv lock --check` 仍 95 packages。D 级按 ADR-0011 §10 逐条执行
（有数据升级、未提交/失败迁移回滚、抽取中断重启、读中变化不采纳且留待重抽、来源离线/位置 MISSING、
多位置确定性、规则版本门禁、抽取写入后 `c1` 游标 stale 且重开不重不漏）。C 级：真实重建 onedir，
bundle 内 `0001`～`0015` 逐个 SHA-256 与源码一致；`packaged_api_smoke.py` 在中文/空格目录
`status=ok`/`problems=[]`、打包 data root `schema_version=15`（含 `asset_metadata`/capture 索引/
规则版本）；独立副本删掉 0015 后真实 HTTP 请求得 `409 SCHEMA_INCONSISTENT`（versions 1..14 vs 期望
1..15）且 data root 零写。记录式真实浏览器（内置浏览器）完成 §10 的 8 项：规范化范围回显与左闭右开、
`capture_desc` 尾桶「文件时间」、有/无偏移显示、空结果 READY 与 PENDING 两种诚实措辞、第二标签页
改地点后 409 stale 并保留条件、上传路径不含 capture 字段、键盘可达 + 380 px 无横向溢出、DOM/URL/日志
隐私扫描（`/geo` 命中 0、访问日志 `token=<redacted>`）。

本卡的三处契约留白裁定与两处兼容处理记录在报告 §9（失配时详情 `null`；输入修订 = 内容哈希 + 录制观察
摘要；失配时 `capture_desc` 仍确定性排序；示例地名包 `compatible_catalog_schema.maximum` 14 → 15；
非模型处理器记录 `photo-search-runtime@1` 能力）。

未验证项（不得当 PASS）：30 万容量下的 `capture_desc`/时间过滤延迟与索引列序、全库抽取耗时、多进程
并发、`0015` 真实库迁移耗时、真实内部样本覆盖率、GTX 1660 实机；窄宽为内置浏览器真实视口而非设备级模拟。

**停止点**：下一步请在**新会话**用 **DS-H / DeepSeek V4.1 Flash / 高推理**做全量工程主审（完整 diff、
含未提交文件、迁移/事务/revision/重启状态、API/search/UI 依赖面、测试真实性、bundle 资源清单、
ADR-0011/任务卡一致性）；通过后才标 P4-00B PASS、更新本文件并生成下一任务卡。模型切换始终由用户在
Codex 中手动完成。

## Latest independent DS-H engineering review — P4-00B PASS (2026-09-22)

**P4-00B：PASS（独立 DS-H 全量工程主审；`SOL_NOT_REQUIRED`，未命中 V3.1 §2.2、未升 DS-MAX）。**
完整主审报告 [docs/reports/P4-00B-dsh-engineering-review.md](reports/P4-00B-dsh-engineering-review.md)；
上文 P4-00B 实现段落的「停止点」已被本阶段满足。观察基线是 `8e48fc7` + P4-00A/P4-00B 全部未提交交付物
（主审开始与结束时被审交付物均为 29 modified + 16 untracked = 45 项；本阶段只新增主审报告与
`P4-00C-A`/`P4-00C-B` 任务卡）。**P4-00A/P4-00B 收口提交已完成**：全部交付物 + 主审报告 + 两张下一任务卡
已作为**一个版本**入库（提交信息 `P4-00A/B: ADR-0011 taken-time contract, 0015 migration, extraction,
API/UI, packaged closure and DS-H review`，父提交 `8e48fc7`；当前提交号用 `git log -1` 获取），工作树干净。

主审独立复现（不引用被审报告的结论）：完整套件 `test-results/p4-00b-review-full.xml` **1598 / 0 / 0 / 48**
（205.9 s）；聚焦套件 `test-results/p4-00b-review-focused.xml` **353 / 0 / 0 / 0**；Ruff / `git diff --check` /
`uv lock --check`（95 packages）在正常 Windows 权限路由下全 clean。C 级用最终源码**真实重建** onedir：
15 个迁移与 27 个 `photo_search` 数据资源**逐个 SHA-256 与源码一致**；中文/空格目录
`packaged_api_smoke` `status=ok`/`problems=[]`、打包 data root `schema_version=15` 且
`asset_metadata`/闭集来源/capture 索引/`taken_time_rules_version=1` 齐全
（`test-results/p4-00b-review-packaged-smoke.json`）；独立副本删掉 `0015` 后真实 HTTP 得
**409 `SCHEMA_INCONSISTENT`**（`versions=[1..14]` vs `expected_versions=[1..15]`）且 data root 零写
（`test-results/p4-00b-review-missing-0015.json`）。夹具独立重建后 `expected` 块与记录文件逐字段相同；
主审另用真实内置浏览器复跑关键步骤，并补测 ADR §11.4 的「左端含、右端不含」最小反例
（`from=2022-07-05T18:31:07.000 ⇒ {#8,#1}`、`to_exclusive=同一时刻 ⇒ {#2}`）。

主审唯一阻断级发现是**证据陈旧**（非实现缺陷）：实现阶段的 C 级 bundle 构建于 19:51，而
`src/photo_search/web/app.js` 在 19:57 又被修过一次，包内 `app.js` 为 43369 B ≠ 源码 45629 B；
根因是新增的包内检查只比对 migration 脚本，不覆盖 `collect_data_files('photo_search')` 的其它资源。
主审已在本会话内真实重建并重跑 C 级三条闭环，缺陷关闭；建议把包内检查扩展为全部数据资源（报告 §5 R1）。
另有四条非阻断建议（`/search` 的 `#capture-state` 从不写入、超长 capture 边界答 `VALIDATION_ERROR`、
任务卡/ADR §10 的 `tools/build_onedir.py` 应为 `tools/packaging/build_onedir.py`、
`EXIF_ORIGINAL+HIGH ⇒ offset` 只在载荷校验器强制）与一条覆盖率建议（右端相等的最小用例）。
报告 §9 的六处契约留白裁定全部被主审**接受**，理由见主审报告 §6。

下一任务按用户要求重新规划为**两段式**（一次会话只用一个模型）：

- **P4-00C-A — HEIC 业务实现：格式策略、方向/GPS/拍摄时间复用与坏文件隔离**，任务卡
  [docs/tasks/P4-00C-A.md](tasks/P4-00C-A.md)：**DS-H / DeepSeek V4.1 Flash / 高推理** 做业务实现与
  **B + D** 验收（新增固定版本 `pillow-heif` + `uv.lock`；不得碰 spec/打包工具/打包 smoke）。
- **P4-00C-B — HEIC 环境与正式包兼容（C1 只做环境）**，任务卡
  [docs/tasks/P4-00C-B.md](tasks/P4-00C-B.md)：**C1 / GPT-5.6 Terra / medium** 做 onedir 重建、pillow-heif/
  libheif 进包、打包 HEIC 端到端、降级 fail-closed 反例、`packaged_api_smoke` 扩展（含 P4-00B 主审建议 R1 的
  全量数据资源审计）；**C 级**。C1 不得改业务语义，发现业务缺陷只交回最小反例。

两段都完成后**另开独立 DS-H 会话**做 A+B 全量工程主审，通过后才标 **P4-00C PASS** 并生成 P4-01。
当前建议：**直接继续使用当前的 DS-H 档**完成 P4-00C-A；之后请在 Codex 中手动切换到 C1 / GPT-5.6 Terra /
medium 执行 P4-00C-B。

## Latest implementation — P4-00C-A HEIC business layer (2026-09-22, DS-H)

**P4-00C-A：PASS（B + D level；`SOL_NOT_REQUIRED`）。** 完整报告
[P4-00C-A 报告](reports/P4-00C-A-heic-business-implementation.md)。起点 `2b8a751`（工作树干净）；
本卡改动未提交（9 modified + 5 untracked，另加本报告与状态两处文档更新）。

交付：新增 `src/photo_search/media/heif.py` 作为**唯一** pillow-heif 注册点（幂等、失败即 fail closed）；
`SUPPORTED_INPUT_FORMATS` 改为按真实可解码能力计算，`HEIC`/`HEIF` 仅在插件与 libheif 真的加载时进入支持闭集，
`AVIF/TIFF/JXL/MPO/RAW/PSD/PDF` 等继续拒绝；HEIC 走与 JPEG **完全相同**的解码/方向归一化/resize 路径；
坏 HEIC 每文件 `DECODE_FAILED` 且不阻断批次；`geo`、`taken_time`、`face_crop` 各加一行幂等注册调用。
依赖：固定 `pillow-heif==1.8.0`（PyPI win_amd64 wheel，内置 libheif 1.23.4；`Requires-Dist: pillow>=11.1.0`
与本项目 Pillow 11.3.0 配对，未升级 Pillow），`uv.lock` 95 → 96 packages。

关键实测（报告 §3）：pillow-heif 会把 EXIF 方向读出后把标签改写为 1，并只在容器同时可写时留下 `irot`。
实测两种文件形态——容器有 `irot`（libheif 已旋转像素）与容器无变换（像素未旋转、标签已被清掉）——
若照抄 JPEG 路径，后者会被静默发布成横向错图。实现按容器是否带 `irot`/`imir` 判别，只在前者缺席时把
`original_orientation` 写回内存 EXIF，再交给**同一个** `exif_transpose`；`read_orientation()` 用同一规则，
因此 `probe_original_frame()` 与解码帧在两种形态下一致。

上传面裁定：**`POST /api/v1/search/image` 仍拒绝 HEIC**（P2-07 白名单显式化为 `UPLOAD_INPUT_FORMATS`，
语义不变）。理由与真实 HTTP 样本（409 `FORMAT_UNSUPPORTED`、details 列出上传白名单）见报告 §5；
让上传面跟随库内能力属独立的加性协议变更，需同步改 P2-07 断言并要求 Sol 聚焦终审。

实测：专项单元 `test-results/p4-00c-a-focused-unit.xml` **85 / 0 / 0 / 0**；本卡新增两文件
`test-results/p4-00c-a-d-level.xml` **31 / 0 / 0 / 0**；完整 `test-results/p4-00c-a-full.xml`
**1632 / 0 / 0 / 48**（190.0 s，48 skip 全为未装 pinned 权重的既有环境跳过）；Ruff、`git diff --check`
clean，`uv lock --check` Resolved 96 packages。D 级六条逐条有证据（报告 §8）：坏文件同批隔离、
写入期被替换/删除、同内容双位置确定性、插件不可用的 fail closed、**存量 JPEG/PNG/WebP/BMP/GIF 产物改动前后
逐字节相同**（`tools/diagnostics/media_artifact_fingerprint.py`，「改动前」由 `git archive 2b8a751 src`
复现；两侧 18 行载荷哈希文件 sha256 均为 `b8df55b5afc164919530fd95aaed61586c8088db52b1f5f1d4477a36514e7b9c`）
⇒ **不递增 processor id**、`0001`～`0015`/全部 companion/ADR 相对基线零改动。

未验证项（不得当 PASS）：30 万容量下的 HEIC 吞吐/内存、真实设备 HEIC 样本（本机无）、
容器 `irot` 与 EXIF 方向同时非 1 的非规范文件、10/12-bit/网格/多帧/带 alpha 的 HEIC 变体、
HEIC 的 XMP 方向、大 HEIC 真实耗时、多进程并发、**C 级打包（属 P4-00C-B）**、GTX 1660 实机。

**停止点**：本卡不生成 P4-01、不碰 `PhotoSearch.spec`/`tools/packaging/**`/`packaged_api_smoke`。
请用户在 Codex 中手动切换到 **C1 / GPT-5.6 Terra / medium** 的新会话执行
[P4-00C-B](tasks/P4-00C-B.md)；两段都完成后另开**独立 DS-H 会话**做 A+B 全量工程主审，
通过后才标 **P4-00C PASS**。未命中 V3.1 §2.2 六类条件，不升 DS-MAX。

## Latest C1 environmental stage + repair round — P4-00C-B (2026-09-23)

**P4-00C-B：PASS（C 级：HEIC 环境与正式包兼容；`SOL_NOT_REQUIRED`）。** 完整报告
[P4-00C-B 报告](reports/P4-00C-B-heic-packaging.md)。起点为 A 段全部未提交交付物 + C1 段环境改动；
本段落改动未提交（环境面 + 一处 A 层业务修正，见下）。

C1 段交付：`tools/packaging/build_onedir.py` 只加 `--collect-all pillow_heif`（PyInstaller 看不到
「先探测能力再动态 import」，且该 wheel 的扩展与 libheif DLL 是同级两个文件）；
`packaged_api_smoke.py` 新增 HEIC 组件清单/哈希、打包 HEIC 缩略图/预览/坏文件断言与 P4-00B 主审建议
R1（包内 `photo_search` **全部**数据资源逐字节一致）。

**审核失败点与修复**：降级副本（去掉包内 libheif DLL）里 HEIC 得到 `DECODE_FAILED`，违反
「必须结构化 `FORMAT_UNSUPPORTED`」。根因是 `register_heif_opener()` **先注册 Pillow 打开器、后查
C 扩展能力**，运行库缺失时异常发生在注册之后，留下「已注册但不可用」的半成品插件。
修复（A 层，`src/photo_search/media/heif.py`）：注册前快照 Pillow 的六个注册表、在 try 内强制读一次
`libheif_version()`、失败即**原地回滚**；并新增忠实复现该形态的单元回归（临时禁用回滚时确实失败）。
对照证据：回滚关闭 `HEIF_in_Pillow=True → DECODE_FAILED`；回滚开启 `HEIF_in_Pillow=False →
FORMAT_UNSUPPORTED`。

实测：真实重建 onedir（21 个 NVIDIA DLL）后
`test-results/p4-00c-b-packaged-smoke.json` **status=ok / problems=[]**——包内
`_pillow_heif.cp312-win_amd64.pyd`（59904 B，sha256 `58ccecb7…`）与
`libheif-30fc1b3c…dll`（2201088 B，sha256 `6223de11…`）与 `.venv` 来源逐个哈希一致；
27 个包内数据资源与 15 个迁移脚本与源码逐字节一致；打包进程里 HEIC 缩略图/预览 200 `image/jpeg` 且尺寸
**与同像素同方向的 JPEG 孪生一致（均 320×240）**；同墙钟「有偏移 / 无偏移」对照对经打包详情端点发布为
`EXIF_ORIGINAL/HIGH(+480min)` 与 `EXIF_ORIGINAL/MEDIUM(null)`，civil key 相同，GPS 事实 VALID/WGS84 一致；
损坏 HEIC 409 `DECODE_FAILED` 且进程不崩。
降级三变体 `test-results/p4-00c-b-degraded-{libheif,extension,both}-smoke.json` 全部
**status=ok / problems=[]**：HEIC 三种请求均 409 `FORMAT_UNSUPPORTED`、能力清单不宣传 HEIC、
JPEG 缩略图仍 200 且既有缓存字节不变。业务全量 `test-results/p4-00c-b-full.xml` **1633 / 0 / 0 / 48**；
Ruff、`git diff --check` clean，`uv lock --check` Resolved 96 packages。

未验证项：真实设备 HEIC 样本与 10/12-bit/网格/多帧变体、大 HEIC 在包内的内存/耗时、pillow-heif 版本错配、
打包进程的 taken-time **生产**路径（`python -m photo_search.taken_time.cli` 无法在 onedir 里运行，本段只验证了
服务/读回语义，生产者侧由 A 段真实调度器证据覆盖）、真实用户环境的安装/升级/卸载回归、GTX 1660 实机。

**停止点**：本段不生成 P4-01。请用户在 Codex 中**另开独立 DS-H / DeepSeek V4.1 Flash / 高推理**会话做
**A+B 全量工程主审**（A 的业务 diff、B 的环境 diff、包内资源与哈希、D/C 证据真实性、ADR/任务卡一致性、
V3.1 §2.2 六类条件）；该主审通过后才标 **P4-00C PASS** 并生成下一卡（预期 **P4-01**）。
本段未改 upload 白名单与任何已冻结信封/错误码集合，故按卡记 `SOL_NOT_REQUIRED`。

## Latest independent DS-H A+B engineering review — P4-00C PASS (2026-09-23)

**P4-00C：PASS（独立 DS-H / DeepSeek V4.1 Flash / 高推理全量工程主审；`SOL_NOT_REQUIRED`；未命中
V3.1 §2.2 六类条件、未升 DS-MAX、不需要额外 Sol 阶段）。** 完整主审报告
[docs/reports/P4-00C-dsh-engineering-review.md](reports/P4-00C-dsh-engineering-review.md)。观察基线
`2b8a751` + P4-00C-A/B 全部未提交交付物（14 modified + 9 untracked）；本阶段只新增主审报告与
`docs/tasks/P4-01.md`，并更新本文件。**收口提交：`9b1ff20`**（P4-00C-A/B 交付物 + 本主审报告 +
`docs/tasks/P4-01.md` + 本文件，一次入库；提交后工作树只剩 P4-01 会话尚未提交的新文件）。

主审独立复现（不引用被审报告结论）：完整套件 `test-results/p4-00c-review-full.xml`
**1633 / 0 / 0 / 48**（203.5 s）；聚焦套件 `p4-00c-review-focused.xml` **98 / 0 / 0 / 0**；
Ruff / `git diff --check` / `uv lock --check`（96 packages）全 clean（受限令牌下 Ruff 仍打印一条既有
`os error 5` 遍历警告，结论为 `All checks passed!`）；真实重建 onedir 后
`p4-00c-review-packaged-smoke.json` **status=ok / problems=[]**（HEIC 缩略图/预览 200，与 JPEG 孪生同为
320×240，损坏 HEIC 409 `DECODE_FAILED`，能力清单宣传 HEIC，打包 data root `schema_version=15`、
`rules_version=1`），三份降级变体 `p4-00c-review-degraded-{libheif,extension,both}-smoke.json` 全
**status=ok / problems=[]**（HEIC 三者 409 `FORMAT_UNSUPPORTED`、不宣传 HEIC、JPEG 与既有缓存不变）。
主审直接核对包内哈希（`_pillow_heif` 59904 B `58ccecb7…`、`libheif` 2201088 B `6223de11…`、
`web/app.js`、`search.js`、`0015_taken_time.sql` 与源码逐个一致），R1 的全量数据资源审计（27 个路径）
成立；D-5 指纹由主审重跑两侧 18 行、sha256 均为
`b8df55b5afc164919530fd95aaed61586c8088db52b1f5f1d4477a36514e7b9c`，并验证「改动前」一侧确实加载了
基线源码树（无 `media/heif.py`、支持集不含 HEIC）⇒ 不递增 processor id 的裁定成立；禁止面
（ADR-0001～0011、全部伴随物、`0001`～`0015`、`taken_time` 契约、`web/**`、`PhotoSearch.spec`）
相对基线零改动。

主审新增独立探针：在独立副本中删除 `libde265`/`libx265`（保留扩展与 `libheif`）后，HEIC 仍
`409 FORMAT_UNSUPPORTED`、不宣传 HEIC、JPEG 孪生仍 320×240 —— 降级面比被审报告声称的更强。

主审发现：**无阻断级**。三条非阻断建议 —— R1（打包组件审计只覆盖扩展 + libheif，实际包内还有 5 个
delvewheel 同级运行库，建议在 P4-07 扩展清单并增加「缺 codec DLL」变体）、R2（`contract` 的能力常量是
import 时快照，生产调用点已全部改读函数，建议收敛）、R3（方向判别器读不到私有句柄时偏向 pillow-heif 的
声明，依赖版本钉死 + 两形态断言守住，升级时须复核）。未验证项继承 A/B 报告（真实设备 HEIC、10/12-bit/
网格/多帧变体、XMP 方向、大 HEIC 耗时/内存、多进程、打包 taken-time 生产路径、真实安装/升级/卸载回归、
GTX 1660、30 万规模）。

下一任务：**P4-01 — 10 万/30 万 exact/ANN 对照、索引维护、过滤分布与并发调度基准**，任务卡
[docs/tasks/P4-01.md](tasks/P4-01.md)，推荐 **DS-H / DeepSeek V4.1 Flash / 高推理**（与当前档一致，可继续
使用当前模型）；验收等级 **B + D**（涉及打包再加 C）；max 仅按 §2.2，无法解释的 Windows 性能异常转 C1。
模型切换始终由用户在 Codex 中手动完成。

## Latest benchmark closure — P4-01 scale bottleneck measurement (2026-09-23)

**P4-01：测量闭环 PASS（B + D），命中停止条件 1 → 30 万瓶颈交回，生产代码零改动。** 完整报告
[P4-01 报告](reports/P4-01-ann-scale-benchmark.md)。起始基线 `0f4a47c`（用户已把 P4-00C 收口入库，工作树
干净）；本卡新增 `tools/diagnostics/ann_scale_benchmark.py`、`tests/integration/test_p4_01_index_scale.py`、
本报告、`docs/tasks/P4-01B.md`，**未改任何生产代码/迁移/契约/打包面**，`SUPPORTED_SCHEMA_VERSION` 仍为 15。

夹具：项目内 `tmp/p4-01-基准 空格`（中文 + 空格路径）真 SQLite + 真 Lance generation（生产 `_schema`）+
真 `jobs`/`artifacts`/载荷文件/`index_entries`；10 万与 30 万两档都真实建库（30 万档 300 000 行，
catalog 1.32 GB、vectors 619 MB、artifacts 3.23 GB）。查询集 1 000 + 50 边界查询，seed 20260923。
**未装 pinned 权重，向量为确定性合成向量（clustered 为主、uniform 为对照）；GTX 1660 记 BLOCKED。**

实测（RTX 4060 Laptop / 32 逻辑核 / 31.7 GB）：

1. **检索路径 `O(N²/1000)`**：生产 `vector_channel` 按 1 000 候选一块、每块一次全表扫描 ⇒ 10 万
   **11 520 ms**（100 块，Recall@20=1.000），30 万 **112 177 ms**（300 块，Recall@20=1.000）；
   §12.2 的 1.5 s 目标在 30 万差约 **75×**。冷缓存首查 110 939 ms，与热值同量级。
2. **当前 IVF_PQ 不能替代**：30 万（585 分区/64 子向量，构建 18.2 s）后遍历 10 965 ms 但 Recall@20
   只有 **0.100**；引擎单次全库查询 374 ms（精确）→ 17.1 ms（ANN，Recall@20 0.077）。20 000 行独立新表
   单变量归因：`num_sub_vectors` 64→128 把 Recall@20 从 0.229 提到 0.395（clustered）/0.241→0.408
   （uniform），`partitions` 39→9 只到 0.276/0.283，**数据分布不是主因**；锁定版 `LanceQueryBuilder`
   不暴露 `nprobes`。30 万档无索引引擎扫描与 numpy 暴力 top-20 **160/160 全等**。
3. **索引维护**：逐事件 `merge_insert` 50.9 ms/行（10 万档 39.3 ms/行）vs 同数据批量写入
   **0.0051 ms/行**（30 万 1.53 s）；按实测 19.6 行/s，30 万行从零约 4.25 小时。`process_next` 重放
   50.6 ms/事件、`semantic_ready` 5/5、generation READY；`audit_and_schedule_repairs` 因 `has()`→`rows()`
   全量物化为 `O(资产数 × 行数)`（200 资产 = 3 052 ms）。
4. **D 级四项注入**（200 资产全真路径）：中断重启零逻辑重复/零丢失；「Lance 已写、outbox 未确认」重放
   幂等；内容变更期间旧向量不可命中、重放后新向量可命中；位置离线后幽灵行存在但不可达。投影滞后补回
   亦验证（`in_topup=true` / `returned_as_hit=false`）。
5. **过滤分布**：wide 72%（10 万 1 808 ms / 30 万 8 236 ms，blocks 与 `ceil(候选/1000)` 完全一致、
   越权命中 0）、narrow 10%（339 ms / 1 513 ms）；**half（来源 + 地点后代）与 thin（地点 + 人物 all）
   两档的权威 SQL 在两档规模上都 >60 s 未返回**（同夹具 wide 只 151 ms、narrow 146 ms），并使 30 万档
   §12.4 第 32 条组合校验无法测量 —— 交回为查询计划级异常。
6. **并发**（3 万档，后台独立进程 200 事件重放 + 前台真实 `search_assets`）：前台 P50 4 693 ms /
   **P95 5 014 ms（目标 ≤1 500 ms，未达标）**，后台 19.9 事件/s、剩余 0。

验收：全量 `test-results/p4-01-full.xml` **1639 / 0 / 0 / 48**（265.9 s，48 skip 为既有未装权重环境跳过）；
本卡专项 `test-results/p4-01-scale.xml` **6 / 0 / 0 / 0**；Ruff `All checks passed!`、`git diff --check`
与 `uv lock --check`（96 packages）退出码 0；机器可读结论 `test-results/p4-01-ann-scale.json`
（scales 100000 + 300000、attribution、injections、concurrency）。测试用项目内 basetemp，沙箱内临时目录
`WinError 5` 按既有做法改走正常 Windows 权限路由，未改 ACL。

**停止点（任务卡停止条件 1）**：上述瓶颈的修复需要在「批量/分片写入、索引维护策略、索引参数与召回承诺、
可能的检索路径选择」之间做一次设计决策，已触及改检索语义/支持承诺的边界，因此本卡**未做任何生产修改**，
只交回最小反例与测量数据。下一任务为 **P4-01B — 检索路径与索引维护瓶颈的修复设计与最小实现**
（[docs/tasks/P4-01B.md](tasks/P4-01B.md)），推荐 **DS-H / DeepSeek V4.1 Flash / 高推理**；完成后另开独立
DS-H 全量工程主审，若改动触及检索语义/召回承诺则补 **C2 / GPT-5.6 Sol / high** 聚焦终审。两条环境/引擎级
异常（同表第三次 `create_index(replace=True)` >9 min 无进展；AND 过滤 SQL 从 150 ms 退化到 >60 s）按
V3.1 §2.2 建议转 **C1**。**P4-02（1 万→10 万→30 万正式容量验证）在瓶颈决策前不应启动**，否则只会重测
同一堵墙。模型切换始终由用户在 Codex 中手动完成。

## Latest design phase — P4-01B bottleneck decision (2026-09-23)

**P4-01B：设计阶段完成（DESIGN HANDED BACK），实现未开始，生产代码零改动。** 决策报告
[P4-01B 报告](reports/P4-01B-bottleneck-decision.md)。基线 `8af86f8`；本卡新增报告、`docs/tasks/P4-01C.md`、
本状态段，以及 `tools/diagnostics/ann_scale_benchmark.py` 的 `--mode design` 设计探针。`src/photo_search/**`
未改一行，schema_version 仍为 15。设计证据：`test-results/p4-01b-design.json`。

设计探针（30 万夹具）给出的三条决定性证据：

1. **块大小就是杠杆，且结果不变**：同一查询在 300 000 行上，无索引时块 1 000 = 92 195 ms（300 块）、
   块 5 000 = 19 757 ms、块 25 000 = 7 282 ms、块 100 000 = 5 466 ms；加 `asset_id` 标量索引（BTREE，
   80 ms 建好）后块 1 000 = 6 618 ms、块 5 000 = 3 602 ms、块 100 000 = 3 518 ms；带 IVF_PQ 时
   块 100 000 = 2 077 ms。**12 种组合的 top-20 与基线逐条相同**（`same_top20_as_first=true`），
   因为 `vector_channel` 合并的是每块 top-k，放大块不改变结果。参考值：引擎单次**无过滤**全库查询
   374 ms（精确）。
2. **过滤退化点被定位到 `COUNT(*)`**：`EXPLAIN QUERY PLAN` + 计时显示 half（来源 1 + 城市 1..5 后代）
   的 `COUNT(*)` >20 s 未返回，而**同条件的 `ORDER BY asset_id LIMIT 5` 只要 0.2 ms**；narrow 的计数
   600 ms、分页 0.1 ms；thin 两者都超时。即瓶颈在「为算 `total` 做的整表计数」，不在过滤谓词。
3. **批量写入快 424×**：30 万行派生表上逐行 `merge_insert` 48.87 ms/行（20 行 977 ms）vs 一次写 500 行
   0.115 ms/行（57.6 ms）。加上 `has()`→`rows()` 的 `O(N²)` 审计，维护面有一组不动外部契约的修法。

待用户裁决的四项（报告 §6，均给出推荐，可直接照推荐执行）：**决策 1** A1 + A2（块 1 000→5 000 +
`asset_id` 标量索引；推荐不做 A3 全局查询快路径）｜**决策 2** B1（暂不引入 ANN，保持召回 1.000；
B2 粗筛+精确重排排后续，B3 换索引类型/B4 下调 §12.3 承诺需另行裁决）｜**决策 3** C1 + C2（批量
`merge_insert` + `has()` 定点查询）｜**决策 4** D2 优先（不改语义地修计数计划；D1 改 `total` 语义或 D3
超时降级属协议面，需 Sol 聚焦终审）。

验收：全量 `test-results/p4-01b-full.xml` **1639 / 0 / 0 / 48**（219.8 s）；Ruff `All checks passed!`、
`git diff --check` 与 `uv lock --check`（96 packages）退出码 0。

下一任务：**P4-01C — 按裁决实现瓶颈修复**（[docs/tasks/P4-01C.md](tasks/P4-01C.md)），推荐
**DS-H / DeepSeek V4.1 Flash / 高推理**（与当前档一致，可继续使用当前模型）；本卡会改
`IndexCoordinator` 写路径，故完成后 **C2 / GPT-5.6 Sol / high 聚焦终审必选**，主审通过后先写
`ENGINEERING REVIEW PASS / SOL FOCUSED REVIEW PENDING`。两条引擎/环境异常（同表第三次
`create_index(replace=True)` 无进展；`COUNT(*)` 计划退化）仍建议按 §2.2 转 **C1**。
**P4-02 在 P4-01C 收口前不启动**。模型切换始终由用户在 Codex 中手动完成。

## P4-01B 补充分析（按用户指定重点：300 次查询 / filter+vector 顺序 / ANN 方案比较）

**仍在 P4-01B 内，纯分析，`src/photo_search/**` 零改动。** 报告已追加 §9/§10
（[P4-01B 报告](reports/P4-01B-bottleneck-decision.md)）；证据 `test-results/p4-01b-strategy-20k.json`、
`...-300k.json`、`...-300k-p16.json`，工具新增 `--mode strategy`（过滤×向量顺序矩阵、引擎下推探针、
单次全局查询上界、ANN 方案对照）、`--fixture-dir`、`--scheme-list/--scheme-partitions`。

1. **修「300 次引擎查询」**（30 万、同一查询、top-20 与基线逐条一致）：块 1 000 无索引 92 195 ms →
   块 5 000 = 19 757 ms → 块 25 000 = 7 282 ms → 块 100 000 = 5 466 ms；再加 `asset_id` 标量索引
   （80 ms 建好）后块 5 000 = 3 602 ms、块 100 000 = 3 518 ms；有 ANN 时块 100 000 = 2 077 ms；
   **一次全局查询（仅无过滤等价）374 ms**。结论：A1（块 1 000→5 000）+ A2（标量索引）是纯参数/索引层修法，
   结果不变；A3（一次全局查询）需单独授权。
2. **filter + vector 顺序**：`place`/`person`/`capture` 不在派生投影里，引擎内 `array_contains(source_ids,1)`
   在 30 万上比不过滤更慢（449 ms vs 317～369 ms）且 `LabelList` 标量索引被引擎拒绝 ⇒ **不引入向量引擎预过滤，
   保持权威先行**。真正的代价是过滤谓词的**查询计划**：30 万块 5 000 下 wide 44 块 18 085 ms、narrow 6 块
   2 754 ms，而 **half/thin 连一块都取不出来（>60 s 中断）**；在 20 000 行上同样可见：half 2 块 19 380 ms、
   thin 1 块 143 406 ms。病灶锁定为 `source`（`file_locations`）与 `person`（`face_person_links`）两个相关
   EXISTS 子查询，必须**先修谓词计划再做块大小调优**。
3. **ANN 方案比较**：20 000 行上 `IVF_PQ`(64) Recall@20 0.232 / 建索引 4 299 ms，而 `IVF_FLAT` 0.671 /
   229 ms、`IVF_SQ` 0.654 / 225 ms、`IVF_HNSW_FLAT` 0.638、`HNSW_FLAT` 0.642（查询都在 11 ms 级），
   精确路径 1.000 / 28 ms；300 000 行上 `IVF_FLAT`/`IVF_SQ` 无论 585 还是 16 个分区都只有 **0.150/0.147**，
   精确路径 1.000 / 300 ms ⇒ **本构建内没有 ANN 方案达到 §12.3 的 0.95**（`nprobes`/`ef_search` 未暴露）。
   结论：**暂不调整索引策略**（不把 ANN 当主路径）；若将来需要 ANN，选 `IVF_FLAT`/`IVF_SQ` 而非 `IVF_PQ`；
   0.95 需在真实模型向量 + 可调 `nprobes` 的引擎上复测。

**需要用户拍板的两件事**：① 是否授权 A3（无过滤时一次全局查询，30 万 0.37 s）；② 是否同意
**暂不调整索引策略**并把 ANN 复测排到真实权重就绪之后。其余三项（A1+A2、权威先行、D2 优先）按报告执行。
下一任务仍是 **P4-01C**（[docs/tasks/P4-01C.md](tasks/P4-01C.md)），推荐档位不变
（DS-H / DeepSeek V4.1 Flash / 高推理；完成后 Sol high 聚焦终审必选）。模型切换始终由用户手动完成。

**并行工作树提示（2026-09-23 19:03 起）**：同一工作树里出现了另一个会话正在进行的 P4-01C 实现
（`retrieval.py` 的 `SCOPE_BLOCK_SIZE=5000`、`store.py` 的 `has()` 定点查询与 `ensure_scalar_index*`、
新测试 `tests/integration/test_p4_01c_retrieval_blocks.py`），方向与 P4-01B §10 的推荐一致。本会话
**未触碰**这些文件。原样全量套件现为 1643 / **3 failures** / 0 / 48（`test-results/p4-01b-full.xml`），
三个失败都是那份在制品的**测试期望**问题（块数被 `MAX_VECTOR_BLOCKS=1000` 截断、monkeypatch 目标没生效、
`has()` 快路径成功所以回退未触发）；排除该在制品文件后的 P4-01B 范围为 **1639 / 0 / 0 / 48**
（`test-results/p4-01b-scope-full.xml`）。P4-01C 的验收应由它自己的会话完成。

## Latest implementation — P4-01C bottleneck repair (2026-09-23)

**P4-01C：IMPLEMENTATION + ACCEPTANCE COMPLETE / INDEPENDENT DS-H ENGINEERING REVIEW PENDING
（未标 PASS，未生成 P4-02）。** 报告 [P4-01C 报告](reports/P4-01C-bottleneck-implementation.md)。
→ 独立 DS-H 全量工程主审通过（`ENGINEERING REVIEW PASS / SOL FOCUSED REVIEW PENDING`）后，
**C2 / GPT-5.6 Sol 聚焦终审为 FAIL（A1/B1/B2 三条真实最小反例）**，DS-H 完成第一轮最小修复后，
**第二轮短复审仍为 FAIL（B1/B2 已收口；A1 给出「旧 revision 后落盘回写」的更强反例）**，
DS-H 已完成第二轮最小修复。**现行状态见本文件末尾：`SOL SHORT RE-REVIEW PENDING（第二轮）`
（未标 P4-01C PASS、未生成 P4-02）**。
起始基线 `cd84bcb`；按用户「按推荐继续」执行 P4-01B §10 的推荐：A1 + A2 / B1 / C1 + C2 / D2。
上一段提到的并行 P4-01C 在制品已按用户要求删除（备份在 `tmp/p401c-wip-backup/`），本卡由本会话重新实现。

交付：`retrieval.py` 的 `SCOPE_BLOCK_SIZE` 1 000 → **5 000**（访问路径参数，结果不变）与
`source`/`person` 相关 `EXISTS` → 等价 `asset_id IN (SELECT ...)`；`store.py` 的 `has()` 定点查询、
`ensure_scalar_index*`（`BTree`、幂等、拒绝即报告，只在维护侧调用）、`upsert_batch` 与
`IndexCoordinator.process_batch`（一批一次批写、一起确认、失败全批回 `PENDING`）；新增 5 个专项用例，
并同步两处断言。**未加 A3，未改任何 ANN 方案，未改迁移/契约/错误码/打包面**，schema_version 仍 15。

30 万实测（同机同查询集）：生产遍历（无 ANN 向量索引）**112 177 ms → 19 489 ms**（300 → 60 块，
Recall@20/@50 仍 **1.000**，引擎扫描 ↔ 暴力 top-20 仍 **160/160**）；**四档过滤全部跑通**——
half 从「>60 s 超时」变为 **1 430 ms（scope）/3 536 ms（walk）**、114 000 候选、23 块，
thin 从「>60 s 超时」变为 **3 740 ms / 991 ms**、2 000 候选、1 块；§12.4 第 32 条组合校验（6 397 候选）
首次完成且 `sql_agrees=true`、无越权命中、投影滞后补回成立。EXPLAIN：half 计数 >20 s → **135 ms**、
thin 计数/分页 >20 s → **888 / 797 ms**。维护：一次写 500 行 **0.118 ms/行 vs 逐行 43.69 ms/行（369×）**，
`process_all` 重放 25 事件 = **1 次引擎写**，崩溃窗口注入（`after_lance`）后重放零逻辑重复，
`asset_id` 标量索引 60.1 ms 建好且四态可报告（`no_table`/`created`/`present`/`unavailable`）。

验收：专项 `test-results/p4-01c-focused.xml` **5 / 0 / 0**；全量 `test-results/p4-01c-full.xml`
**1644 / 0 / 0 / 48**（216.7 s）；Ruff / `git diff --check` / `uv lock --check` 全部 exit 0；
证据 `test-results/p4-01c-ann-scale.json`、`test-results/p4-01c-design.json`。

**停止点**：下一步请在**新会话**用 **DS-H / DeepSeek V4.1 Flash / 高推理**做全量工程主审
（`docs/reports/P4-01C-dsh-engineering-review.md`：完整 diff、块合并精确性、标量索引回退、
`process_batch` 的领取/确认/幂等边界、谓词等价含 NULL 与人工状态、ADR/§9.2/§9.3 一致性、未验证项），
通过后写 `ENGINEERING REVIEW PASS / SOL FOCUSED REVIEW PENDING`；随后由 **C2 / GPT-5.6 Sol / high**
聚焦终审（§11 幂等/提交顺序 + 检索参数），通过后才标 **P4-01C PASS** 并生成 **P4-02**。
未验证项：真模型向量下的效果、A3、索引策略调整（本构建内 ANN 30 万 Recall@20 仍 ~0.15）、
GTX 1660 实机、多世代混批注入。模型切换始终由用户在 Codex 中手动完成。

## Latest independent DS-H engineering review — P4-01C (2026-09-23)

**P4-01C：`ENGINEERING REVIEW PASS / SOL FOCUSED REVIEW PENDING`（独立 DS-H / DeepSeek V4.1 Flash / 高推理
全量工程主审；未标总任务 PASS、未生成 P4-02）。** 主审报告
[docs/reports/P4-01C-dsh-engineering-review.md](reports/P4-01C-dsh-engineering-review.md)。观察基线 `cd84bcb`
+ P4-01C 全部未提交交付物（6 modified + 2 untracked）；本阶段只新增主审报告、更正实现报告的
**三处事实性误述**（§2.1 标题、§2.3 第 5 行、§3.1 第 1 条，只改表述、不改数字与结论）并更新本文件。
**未生成 P4-02**（等 Sol 终审通过）。

主审独立复现（不引用被审报告结论）：全量 `test-results/p4-01c-review-full.xml` **1644 / 0 / 0 / 48**
（202.4 s）、专项 `test-results/p4-01c-review-focused.xml` **5 / 0 / 0**；Ruff `All checks passed!`、
`git diff --check`、`uv lock --check`（96 packages）全 exit 0；禁止面（`0001`～`0015` 与
`SUPPORTED_SCHEMA_VERSION`、全部 ADR 与伴随物、`taken_time`、`web/**`、`api/**`、`persons/**`、
`face/**`、`PhotoSearch.spec`、`tools/packaging/**`、`pyproject.toml`、`uv.lock`）相对基线**零改动**。

最强证据是用 `git archive cd84bcb src` 解出的**基线真代码**做 before/after：同一探针、同一夹具、同一索引
状态下，修复前（块 1 000 与 5 000）与修复后的 top-200 摘要完全相同（`900de11d…`）、top-20 与 numpy 暴力
结果 **20/20**（`p4-01c-review-walk-{before,after}.json`）；12 行块大小扫描（显式钉住索引状态）在两种
**精确**索引状态下的 8 行全部复现基线 top-20（`p4-01c-review-sweep.json`），ANN 列与精确结果重叠仅
**1～7/20**（B1 的独立确认）；`half` 分页 2000 行摘要在两侧相同（`c76226b4…`），修复前 `half`/`thin`
计数 >120 s、`thin` 受限计数 >240 s，修复后 135/888 ms 与 **266**（`p4-01c-review-probe-*.json`）；
独立集合计数与生产 SQL 四档全一致（`p4-01c-review-sets.json`，215 998 / 114 000 / 30 000 / 2 000）。

主审发现：**无阻断级**。R1（§2.3 把 `process_next` 逐事件成绩归给批处理）与 R2（§2.1/§3.1 的
「12 组合 top-20 全同」对 ANN 列不成立）已由主审就地更正；R3 ADR-0009 §3.1 的 `EXISTS` 形状差异需在
下次触碰 ADR 时记录（语义等价已证）；R4 诊断工具三相位共享一张表的索引状态、建议显式记录；
**R5 A2 的生产兑现点**：`src/` 目前没有 `IndexCoordinator.process_all`/`process_next` 的生产调用点
（P2-03 起即如此，非本卡缺陷），「30 万 3.5～4 s」只有在维护侧真正运行时才出现，请把
**「语义 outbox 的生产维护入口」列为 P4-02 前置项**（否则 P4-02 会把未接线误读成已达成）；
R6 若将来新增 `DELETE_VECTOR` 生产者，批内需先按 `record_id` 去重。未命中 V3.1 §2.2 六类条件
（不升 DS-MAX）；两条引擎/环境异常仍建议按 §2.2 转 C1。

未验证项：真模型向量、ANN 质量（§12.3 的 0.95）、§12.2 的 P95 ≤ 1 s（30 万现为 19.5 s 无索引 /
约 3.5～4 s 有标量索引）、A3、多世代混批注入、GTX 1660 实机、`thin` 档真实数据集合等价
（由边界夹具 + `half` 真数据共同支撑）。

**下一阶段（必选）**：请在**新会话**用 **C2 / GPT-5.6 Sol / high** 做聚焦终审（只读压缩交接包：本主审报告、
未决项、关键 diff、最小反例与受影响测试；覆盖 §11 幂等/提交顺序与检索参数），通过后才标
**P4-01C PASS** 并生成 **P4-02**。模型切换始终由用户在 Codex 中手动完成。

### C2 / Sol 聚焦终审结果与最小修复（2026-09-23，同日后续）

**C2 / Sol 聚焦终审：`SOL FOCUSED REVIEW FAIL`**（[终审报告](reports/P4-01C-sol-focused-review.md)；
被审提交 `48cca93`）。终审未复做全量主审，只审 §11 幂等/提交顺序、检索参数、谓词等价与主审 R3/R5/R6，
其自身聚焦 39 / 0 / 0 / 0；C 段（SQL 谓词等价）**PASS**，A/B 段给出三条真实最小反例：

- **A1**：`process_next` 可重领 `IN_FLIGHT`（P2-03 崩溃恢复路径，无 claim token），第二个协调器在批写中途
  重领并确认其中一条后，批的确认阶段报 `INDEX_SUBMISSION_FAILED`，观测到「批内混合确认」。
- **B1**：`search_candidates` 让引擎先按 `top_k` 截断、再在 Python 排序，**完全并列距离**时答案随块大小变化
  （块 1 000 → `(999,1000)`、块 5 000 → `(4999,5000)`），直接违反不变量 1。
- **B2**：`ensure_scalar_index` 只把 `create_index()` 放进 try，`list_indices()` 被拒时直接抛出并中断
  `process_all`，与「拒绝即报告、不得失败」不符。

**DS-H 最小修复（按 V3.1 §11 交回修复；只改 `src/photo_search/indexing/store.py` 与新用例）**：
① B1：`search_candidates` 向引擎请求**每个候选 ID 一行**（`limit=len(ids)`），在既有的
`(distance, asset_id)` 全序排序**之后**才截断，并新增 `CANDIDATE_ROW_COLUMNS` 投影（不回传 512 维向量）；
② A1：确认阶段遇到已被其它协调器置为 `CONFIRMED` 的事件即跳过（同一条派生行、逐事件确认幂等），
本批真实失败仍整批回 `PENDING`，docstring 写明「并发按事件安全、非排他」的前提；
③ B2：`list_indices()` 纳入 try → 目录不可读时返回 `unavailable`，`_table` 仍在 try 之外。
**未改** schema/migration/`SUPPORTED_SCHEMA_VERSION`（仍 15）、ADR/伴随物、信封/错误码/游标、
RRF/BM25/距离公式、打包面。

修复证据：三条新用例在**临时回退修复**后全部失败（`test-results/p4-01c-fix-prefix-sensitivity.xml`
**3 / 3 failures**：B1 `{(4,5),(7,8),(29,30)} != {(1,2)}`、A1 `INDEX_SUBMISSION_FAILED: outbox claim was
lost before confirmation`、B2 `RuntimeError: index metadata unavailable`），修复后专项
`test-results/p4-01c-fix-focused.xml` **8 / 0 / 0**、终审受影响测试面
`test-results/p4-01c-fix-sol-boundaries.xml` **42 / 0 / 0**、全量 `test-results/p4-01c-fix-full.xml`
**1647 / 0 / 0 / 48**（210.2 s）；Ruff / `git diff --check` / `uv lock --check` 全 exit 0。
30 万重测（修复后、重建夹具、卡上原命令）：生产遍历 **19 653 ms / 60 块 / Recall@20 = Recall@50 = 1.000**
（修复前 19 489 ms / 60 块 / 1.000），四档过滤 2 139（44 块）/3 517（23 块）/446（6 块）/926（1 块）ms、
候选数不变，组合校验 6 397 + `sql_agrees=true`（`test-results/p4-01c-fix-ann-scale.json`）；
同夹具块大小扫描复查：无索引 81 963 / 20 252 / 8 467 / 7 186 ms（块 1 000/5 000/25 000/100 000）、
+标量索引 7 145 / 4 279 / 3 899 / 4 130 ms、+ANN 9 091 / 3 437 / 2 566 / 2 548 ms，
**与修复前同量级**，且两种精确索引状态下的 8 行 top-20 与修复前 JSON **逐条相同**
（`test-results/p4-01c-fix-design.json`）。唯一语义变化：完全并列距离时
结果被钉在 `(distance, asset_id)` 全序（修复前随块大小变化），这正是不变量 1 要求的方向。

**（第一轮）现行状态：`SOL SHORT RE-REVIEW PENDING`（未标 P4-01C PASS、未生成 P4-02）。**
请按 V3.1 §11 在**新会话**用 **C2 / GPT-5.6 Sol / high** 只复审失败边界及其直接回归
（A1 交错确认、B1 并列距离、B2 目录拒绝；加上 §7.5 新增的并发/工具面边界），通过后才标
**P4-01C PASS** 并生成 **P4-02**。模型切换始终由用户在 Codex 中手动完成。

### 第二轮短复审（Sol）结果与最小修复：旧 revision 回写（2026-09-23，同日后续）

**C2 / Sol 第二轮短复审：`SOL SHORT RE-REVIEW FAIL`**（[终审报告 §7](reports/P4-01C-sol-focused-review.md)；
被审提交 `a457cba`）。B1（并列距离）与 B2（索引目录拒绝）**PASS**；A1 仍阻断，并给出比第一轮更严重的
反例——「较新 revision 先落盘、旧写者后落盘」：甲在 `_is_superseded` 之后、引擎写之前被暂停，乙此间处理了
较新 revision 并确认；甲恢复后仍把**已算好的旧 revision** 写进 Lance 并跳过确认 ⇒ SQLite 宣布 revision 2
已确认且 `semantic_ready`，实际向量行被回退到 revision 1，而 `vector_channel` 只按
`index_entries.state='CONFIRMED'` 取候选，会发布这条旧向量行（违反任务卡不变量 2/3 与 P2-03
「旧投影事件不得回滚最新状态」）。Sol 的结论：仅在确认阶段重查 SQLite 不足以阻止已发生的旧 Lance 写，
修法必须覆盖「旧写者最后落盘」的顺序。

**DS-H 第二轮最小修复（`src/photo_search/indexing/store.py`，`process_next` 与 `process_batch` 同一纪律）**：
① 领取仍单独提交（保持 `after_lance` 崩溃窗口＝`IN_FLIGHT` 的 P2-03 语义）；
② 用**同一个 `BEGIN IMMEDIATE` 事务**覆盖「权威复读 → `_is_superseded` → 引擎写 → 逐事件确认 →
`_make_generation_ready`」，因此第二个协调器或 `publish_embedding` 都无法把更新的 revision 插进窗口，
旧 revision 不再可能最后落盘；③ 批路径在同一事务开头按 `state='IN_FLIGHT'` 过滤「仍属于自己的事件」，
别人已确认的事件不写、不确认、也不报假失败；本批真实失败仍整批回滚后回 `PENDING`。
代价：写锁在事务期间被持有（批写约 25～150 ms/批、30 万表单行约 40 ms；`busy_timeout = 5 s`，读路径走 WAL
不受影响）。**未改** schema/migration/契约/错误码/排序公式/打包面（`schema_version` 仍 15）。

**复现与验证**：用 `git archive a457cba src` 解出的旧树强制置于 `sys.path[0]`，当前测试文件跑两条直接回归，
旧代码上分别失败于 `assert state["blocked"] is True`（第二个写者能直接进入检查→写入窗口）与
`assert applied == added - 1`（旧实现把被重领的事件算成自己应用）；修复后专项
`test-results/p4-01c-fix2-focused.xml` **9 / 0 / 0**、受影响测试面（+`test_p2_search_topup`）
`p4-01c-fix2-boundaries.xml` **46 / 0 / 0**、全量 `p4-01c-fix2-full.xml` **1648 / 0 / 0 / 48**（214.5 s），
Ruff / `git diff --check` / `uv lock --check` 全 exit 0。新增用例同时断言：窗口对第二个 SQLite 连接
（跨进程模型）与本进程发布者都关闭，且较新 revision 最终一定落在派生行上
（`index_entries.confirmation_revision == Lance.projection_revision == 2`、outbox 全确认、`semantic_ready`
对应最新派生行、存储向量与最新向量同向）。30 万重测（重建夹具）与修复前同量级：生产遍历
**18 080 ms / 60 块 / Recall@20 = Recall@50 = 1.000**、四档过滤 1 878 / 3 295 / 399 / 887 ms、候选数
215 998 / 114 000 / 30 000 / 2 000 与组合校验（6 397 + `sql_agrees`）全不变、维护 31.25 ms/行与
28.39 ms/事件（`test-results/p4-01c-fix2-ann-scale.json`）。

**现行状态：`SOL SHORT RE-REVIEW PENDING（第二轮）`（未标 P4-01C PASS、未生成 P4-02）。**
请按 V3.1 §11 在**新会话**用 **C2 / GPT-5.6 Sol / high** 只复审 A1 的两个顺序（旧写者最后落盘、
并发重领同一事件）及其直接回归；B1/B2 已收口，R3/R5/R6 与未验证项口径不变。通过后才标
**P4-01C PASS** 并生成 **P4-02**。模型切换始终由用户在 Codex 中手动完成。

## Latest closure — P4-01C PASS (2026-09-23)

**P4-01C：PASS（收口）。** 收口依据：**C2 / GPT-5.6 Sol / high 对第二轮修复提交 `479f397` 的短复审
为 `SOL SHORT RE-REVIEW PASS`**（[终审报告 §8](reports/P4-01C-sol-focused-review.md)）。本卡的三段审查
（独立 DS-H 全量工程主审 → Sol 聚焦终审 FAIL → 两轮最小修复 → Sol 短复审）至此全部收敛。

最终证据：全量 `test-results/p4-01c-fix2-full.xml` **1648 / 0 / 0 / 48**（214.5 s）、专项
`p4-01c-fix2-focused.xml` **9 / 0 / 0**、受影响测试面 `p4-01c-fix2-boundaries.xml` **46 / 0 / 0**、
Sol 短复审实测 `p4-01c-sol-rereview2.xml` **46 / 0 / 0**、30 万重建夹具
`p4-01c-fix2-ann-scale.json`（生产遍历 18 080 ms / 60 块 / Recall@20 = Recall@50 = 1.000，
四档过滤、候选数与 §12.4 组合校验不变，维护 31.25 ms/行、28.39 ms/事件）、
Ruff / `git diff --check` / `uv lock --check` 全 exit 0、禁用面零改动（`schema_version` 仍 15）。
唯一语义变化：完全并列距离时结果被钉在 `(distance, asset_id)` 全序（修复前随块大小变化），
即任务卡不变量 1 要求的方向。

交付链（同一工作树，未 push）：`48cca93`（实现 + 独立 DS-H 全量工程主审 + 实现报告）→
`a457cba`（第一轮反例：并列距离确定性 / 并发重领 / 索引目录拒绝 + Sol 终审报告）→
`479f397`（第二轮反例：检查/引擎写/确认同一写事务，旧 revision 不再后落盘）。
收口提交＝本文件所在提交；其实际提交号由 **P4-02A 会话**在起始基线处记录（执行第一步先
`git log -1 --oneline`）。完整收口记录见
[P4-01C 实现报告 §9](reports/P4-01C-bottleneck-implementation.md)。

**下一任务：P4-02A — 语义 outbox 生产维护入口接线与长写事务争用边界（P4-02 前置）**，
任务卡 [docs/tasks/P4-02A.md](tasks/P4-02A.md)；推荐 **DS-H / DeepSeek V4.1 Flash / 高推理**
（与当前档一致，可继续使用当前模型），完成后另开独立 DS-H 全量工程主审，**Sol high 聚焦终审必选**
（改生产维护入口的提交顺序/并发边界，V3.1 §11）。

其后为 **P4-02B — 1 万→10 万→30 万正式容量验证（35～40 万可选压力观察）**，
任务卡 [docs/tasks/P4-02B.md](tasks/P4-02B.md)，推荐 **DS-L / DeepSeek V4.1 Flash / 低推理** 执行 +
独立 DS-H 全量工程主审（默认 `SOL_NOT_REQUIRED`）。P4-02B 的硬依赖是 P4-02A PASS：
R5 要求的「语义 outbox 生产维护入口」未接通前，容量数字只能是夹具数字。
模型切换始终由用户在 Codex 中手动完成。

### Latest C2 / Sol short re-review — P4-01C repair `a457cba` (2026-09-23)

**现行状态：`SOL SHORT RE-REVIEW FAIL`；P4-01C 未标 PASS，未生成 P4-02。**
详见 [Sol 聚焦终审报告 §7](reports/P4-01C-sol-focused-review.md)。C2 只复审先前 A1/B1/B2 失败边界及直接回归：
B1 的 5 001 行全并列探针在块 1 000/5 000 下都返回 `[1, 2]`；B2 的索引目录拒绝已报告 `unavailable`
且不阻断 outbox 维护；受影响测试 `test-results/p4-01c-sol-rereview.xml` **42 / 0 / 0 / 0**。
**A1 仍阻断**：用真实 `publish_embedding` 产生同一资产 revision 1/2，乙先写并确认 revision 2，甲随后把
已准备的 revision 1 写回 Lance 后跳过已确认事件。最终 outbox 两条均 `CONFIRMED`、
`index_entries.confirmation_revision=2`，Lance `projection_revision=1`，`semantic_ready=True`。
按 V3.1 §11 将这条确定性交错交回 DS-H 做最小修复；Sol 下次只复审该失败边界和直接回归。

### Latest C2 / Sol second short re-review — P4-01C repair `479f397` (2026-09-23)

**P4-01C：`PASS`（C2 / Sol 第二轮短复审 `SOL SHORT RE-REVIEW PASS`）。**
报告：[P4-01C Sol 聚焦终审 §8](reports/P4-01C-sol-focused-review.md)。DS-H 第二轮修复把权威复读、
superseded 检查、Lance 写和确认放进同一 `BEGIN IMMEDIATE` 事务；领取事务仍先提交，保留 `after_lance`
的 `IN_FLIGHT` 重放窗口。C2 独立重放旧/新 revision 交错：另一个 SQLite 写者和同进程发布者均被挡在
检查→写入窗口之外，随后处理新事件，最终两条 outbox `CONFIRMED`、
`index_entries.confirmation_revision = Lance.projection_revision = 2` 且 `semantic_ready=True`。
同一事件并发重领的直接回归亦通过；本轮受影响测试 `test-results/p4-01c-sol-rereview2.xml`
**46 / 0 / 0 / 0**。DS-H 全量 JUnit 经核对 **1648 / 0 / 0 / 48**，30 万重测见实现报告 §8；
本短审未重复全量。B1/B2 与谓词等价保持 PASS。

**P4-02 尚未生成**（遵照终审任务边界）。生成下一卡时须把 R5“语义 outbox 的生产维护入口”列为
正式容量验证前置项，并验证新写事务的生产争用/busy-timeout；R3/R6 与真模型向量、ANN 质量、
API P95、A3、多世代混批、GTX 1660、thin 真数据集合等仍按终审报告的未验证口径记录。

## Latest task — P4-02A 语义 outbox 生产维护入口接线（2026-09-23）

**P4-02A：`IMPLEMENTED / VALIDATION COMPLETE（B + D）`，`ENGINEERING REVIEW PENDING`——
未标 PASS、未启动 P4-02B。** 报告 [P4-02A-maintenance-entry.md](reports/P4-02A-maintenance-entry.md)；
基线 `3386f39`（执行前工作树干净）。

交付：新生产入口 `src/photo_search/indexing/maintenance.py::drain_semantic_outbox` 与受控 CLI
`python -m photo_search.indexing.cli {status,drain}`（JSON、退出码 0/1/2、不新增服务/依赖/线程）；
`IndexCoordinator` **只加可观测性**（`write_samples`/`write_rejections`/`_timed_write`；状态机、
确认顺序、引擎写与检索参数零变化）；诊断工具 `tools/diagnostics/index_maintenance_probe.py`
（机器可读 JSON）；10 个新用例。禁止面零改动：`SUPPORTED_SCHEMA_VERSION` 仍 15，migration/ADR/
伴随物/API/Web/打包面未触碰。

实测（`test-results/p4-02a-maintenance.json`，工具退出码 0）：400 资产 + 300 真实工件 →
入口 applied 300 / 2 批 / 328.7 ms、锁等待 p50 0.025 ms / p95 0.033 ms、持锁 p50 2.662 / p95 192.464 ms、
第二次调用 applied 0（幂等）；争用场景（真实前台修订写 30 次 + 内容变更发布 10 次并发）drain
applied 303、锁等待 p95 22.5 ms，前台全部提交且 `catalog_filter_revision` 与事件行数**与实际提交次数
逐一相等**（不静默失败）；`busy_timeout` 实测 **5 498.3 ms** 结构化 `CATALOG_UNREADABLE`（locked）
拒绝 + 有界重试后全部确认；D-1 中途重启、D-2 crash 窗口重放、D-3 内容变更、D-4 两个维护者并发
四项全部成立，且重启 / crash / 不中断三者的派生状态指纹一致。

验收：focused `test-results/p4-02a-focused.xml` **35 / 0 / 0 / 0**；全量 `test-results/p4-02a-full.xml`
**1658 / 0 / 0 / 48**（210.8 s；基线 1648 + 10 新用例）；Ruff / `git diff --check` /
`uv lock --check` 全 exit 0。本机受限令牌下 `--basetemp` 走正常 Windows 权限路由，未改 ACL。

**发现（须交 P4-02B）**：F1 修复审计 `audit_and_schedule_repairs` 实测 13.7 ms/工件
（224 工件 5 480.2 ms），故 `drain` 默认 `repair=False`，30 万档审计成本未测；
F2 冻结领取协议下并发维护者**不分工**（第二个维护者领取在途集合后被串行化、随后跳过），
正确但不提升吞吐，若 P4-02B 要并行维护需另立裁决；F3 冻结包内该 CLI 入口未验证（未触碰打包面，
接启动器将触发 C 级）。

**停止点**：请在新会话用 **DS-H / DeepSeek V4.1 Flash / 高推理**做全量工程主审并写
`docs/reports/P4-02A-dsh-engineering-review.md`（通过则状态写
`ENGINEERING REVIEW PASS / SOL FOCUSED REVIEW PENDING`），随后由 **C2 / GPT-5.6 Sol / high** 聚焦终审
（入口真实性、并发口径、旧 revision 不回滚、busy-timeout 边界）。通过后才标 P4-02A PASS 并启动
**P4-02B**（卡已存在，本轮已补入口命令与 F1/F2）。模型切换始终由用户在 Codex 中手动完成。

## Latest independent DS-H engineering review — P4-02A (2026-09-24)

**P4-02A：`ENGINEERING REVIEW FAIL`（独立 DS-H 全量工程主审）；未标 PASS、未进入 Sol 聚焦终审、
未启动 P4-02B。** 主审报告 [P4-02A-dsh-engineering-review.md](reports/P4-02A-dsh-engineering-review.md)；
观察基线 `9e42b66`（工作树干净）；主审**未修改任何 `src`/测试/工具代码**。

主审独立复现（不引用被审报告结论）：全量 `test-results/p4-02a-review-full.xml` **1658 / 0 / 0 / 48**
（204.5 s）、focused `p4-02a-review-focused.xml` **35 / 0 / 0 / 0**（23.3 s）、证据工具
`p4-02a-review-maintenance.json` 五场景 `ok=true`、Ruff / `git diff --check` / `uv lock --check` 全 exit 0；
禁止面（migration、ADR/companion、`api/web/face/persons/search/places/scheduler/artifacts`、
`PhotoSearch.spec`、`tools/packaging`、`pyproject.toml`、`uv.lock`）相对基线**零改动**，
`SUPPORTED_SCHEMA_VERSION` 仍 15。派生状态指纹在「中断重启 / 不中断 / crash 窗口重放」三者间逐字节一致
（`2ba50ddd…`）；`two_maintainers` 第二维护者实测被串行化（锁等待 434.7 ms）而跳过，合计 applied =
事件数；`contention` 前台 30 修订写 + 10 发布全部提交且计数逐一相等；`busy_timeout` 实测
5 480.6 ms 结构化拒绝 + 有界重试后全部确认。主审另做 3 处变异（去重试识别 / 去可观测性 / 入口不应用）
→ 对应用例各 1 失败（测试非空转），并发两用例连跑 5 轮 10 次执行 0 失败。

**未通过项**：
- **A1（本卡范围内，最小修复项）**：重试循环用 `applied = process_all(...)` 覆盖而非累加，
  当 `busy_timeout` 拒绝发生在「已有批次落地」之后，入口自报 `applied` 少于真实确认数
  （确定性反例：`applied=3` vs `confirmed_delta=5`，outbox 状态本身正确）。
  修法：`applied += process_all(limit=limit-applied)`，并在 docstring/CLI 帮助写明语义；
  复核口径：该探针 + `--mode busy-timeout` + focused。
- **A2（既有面，须另立卡/前置）**：一个不可读工件会让整条 drain **永久停住**
  （整批原子 + 最早优先领取 ⇒ 同批互相拖累；`--repair` 置 RECLAIMED/PENDING_REPAIR 后事件仍留 outbox），
  且架构 §7.2 所述的恢复入口 `ArtifactStore.republish_reclaimed` 目前**只有测试调用点**。
  失败关闭、无静默丢失，但无受支持的生产恢复路径。
- **A3（与 A2 同卡）**：`audit_and_schedule_repairs` 每次运行都为未确认条目以
  `MAX(projection_revision)+1` 新建事件（`PENDING 3 → 5 → 7`），在 drain 被卡住时无界放大 backlog。
- 非阻断观察：实现报告 F1 的「约 2 倍离散」表述已就地更正（同一代码两测 7.99 / 7.912 ms 每工件）；
  前台侧本轮 0 次真实拒绝（同一条 `Catalog.write` 路径 + drain 侧实测支撑）；两维护者用例的
  10 ms 阈值存在极窄抖动窗口（5 轮未复现，建议下一轮改用工具同款判据）。

未命中 V3.1 §2.2 六类升级条件（不升 DS-MAX），无需转 C1。

**停止点**：按 §6.1 做最小修复 → 重跑 §6.1 探针、`--mode busy-timeout`、focused 与全量 →
更新报告/状态 → 再由 **C2 / GPT-5.6 Sol / high** 聚焦终审；A2/A3 另立小卡（建议优先于 P4-02B 容量宣称）。
模型切换始终由用户在 Codex 中手动完成。

## Latest fix round — P4-02A A1（2026-09-24）

**A1 已修复并复核；A2/A3 未纳入本卡（须另立小卡）。状态：`SOL FOCUSED REVIEW PENDING`
——未标 P4-02A PASS、未启动 P4-02B。** 记录见
[实现报告 §12](reports/P4-02A-maintenance-entry.md) 与
[主审报告 §6.1](reports/P4-02A-dsh-engineering-review.md)。

修复：`IndexCoordinator` 增加结果计数 `applied_events`（只计真的确认：`process_batch` 的批量确认与
`process_next` 的两条确认分支；「别人已确认而跳过」不计），入口 `drain_semantic_outbox` 以它报告
`applied`（本次调用确认的事件数，含被拒尝试前已落地的批次，且不超过 `limit`）、以
`confirmed_delta` 报告净确认数，并把剩余额度（`limit - applied`）传给重试。

复核（最终冻结工作树）：修复前的同一反例在临时还原后仍失败（`assert 3 == 5`，
`test-results/p4-02a-fix-a1-prefix.xml`），修复后该用例通过（`p4-02a-fix-a1.xml` 2/0/0）；
focused `p4-02a-fix-focused.xml` **36 / 0 / 0 / 0**（25.0 s）；全量 `p4-02a-fix-full.xml`
**1659 / 0 / 0 / 48**（207.5 s）；证据工具 `p4-02a-fix-maintenance.json` 五场景 `ok=true`
（entry applied 300 = confirmed_delta 300、第二次 0；two_maintainers 合计 25；contention applied 300
收尾 +10；busy_timeout 1 次结构化拒绝 5 480.8 ms + 重试后 `{CONFIRMED 24}`；派生指纹仍 `2ba50ddd…`）；
Ruff / `git diff --check` / `uv lock --check` 全 exit 0。

**停止点**：请在**新会话**用 **C2 / GPT-5.6 Sol / high** 做聚焦终审（只审入口真实性、并发口径、
旧 revision 不回滚、`busy-timeout` 边界，**含本次 A1 修复边界**与其直接回归；A2/A3 不在其范围，
另立小卡）；通过后才标 **P4-02A PASS** 并启动 **P4-02B**。模型切换始终由用户在 Codex 中手动完成。

## Latest C2 focused review — P4-02A（2026-09-24）

**P4-02A：`PASS`（`SOL FOCUSED REVIEW PASS`，审查基线 `95c7104`）；P4-02B 尚未启动。**
报告：[P4-02A-sol-focused-review.md](reports/P4-02A-sol-focused-review.md)。C2 仅复核生产 CLI 入口、
两维护者与前台写并发、旧 revision 不回滚、`busy_timeout` 和 A1 的部分进展后拒绝；定向 JUnit
`test-results/p4-02a-sol-focused.xml` **7 / 0 / 0 / 0**，`git diff --check` exit 0。
主审修复轮全量 JUnit 经核对 **1659 / 0 / 0 / 48**，本轮未重跑全量。首次受限令牌无法创建
`.pytest-tmp`，属 setup 环境错误；按正常 Windows 权限路径重跑通过，未改 ACL。

**A2/A3 仍未解决**：不可读工件可永久卡住 drain，反复 `--repair` 可无界增加未确认事件；
它们超出 P4-02A 允许修改范围，本次聚焦通过不等于异常恢复通过。P4-02B 正式容量 PASS 前须另立小卡
收口恢复入口和审计幂等；真实模型、ANN/API P95、GTX 1660、冻结包 CLI 等未验证项仍按报告口径保留。
模型切换始终由用户在 Codex 中手动完成。

## Latest task — P4-02C 语义索引恢复面与审计幂等（2026-09-24）

**P4-02C：`IMPLEMENTED / VALIDATION COMPLETE（B + D）`，`ENGINEERING REVIEW PENDING`
—— 未标 PASS、未启动 P4-02B、未改动 P4-02B 的依赖结论。** 本卡即 P4-02A 主审 A2/A3 的收口卡
（[任务卡](tasks/P4-02C.md)）：基线 **`36b8d81`**（先把上一轮 P4-02A 收口产物入库），报告
[P4-02C-recovery-and-audit-idempotency.md](reports/P4-02C-recovery-and-audit-idempotency.md)。

交付：① **A2-a 隔离**——`process_next`/`process_batch` 共用
`CLAIMABLE_EVENT_PREDICATE`（不领取工件已 `RECLAIMED` 的事件；`PUBLISHED` 但校验失败仍失败关闭），
被隔离事件保持未确认并由 `drain` 报告的 `awaiting_reexecution = {count, sample, sample_truncated}`
结构化上报；② **A3 审计幂等**——`_schedule_existing` 审计路径复用同一 artifact 的最新未确认事件，
不再每次 `MAX+1` 造新行（真实缺失仍排程）；③ **A2-b 生产恢复入口**——
`maintenance.reexecute_reclaimed_artifacts` + 受控 `python -m photo_search.indexing.cli reexecute`
（`--dry-run`/显式 `--model-dir`/`--manifest`/`--device`，无静默默认与静默 CPU 回退，
非逐字节等价的重执行被 `ARTIFACT_INVALID` 拒绝）。禁止面零改动：`SUPPORTED_SCHEMA_VERSION` 仍 15、
outbox/entry 状态词表未变、migration/ADR/companion/API/Web/打包面/依赖未触碰。

实测：`test-results/p4-02c-recovery.json`（工具 exit 0、10/10 检查为真）——坏工件先
`ARTIFACT_MISSING` 零进展 → `--repair` 后 applied 2 / `{CONFIRMED 2, PENDING 1}` / awaiting 1 →
注入编码器重执行（与原字节逐字节相同）→ 同一事件确认、`confirmation_revision == Lance
projection_revision == 0`、`semantic_ready = 43`；连续三次 `--repair` 的 outbox 行数**恒为 3**
（主审曾实测 3→5→7→9），而真实丢失的确认仍被排程；数值漂移/维度不足/NaN 三种重执行全部被结构化拒绝
且零落地。验收：focused `p4-02c-focused.xml` **7 / 0 / 0 / 0**、受影响面
`p4-02c-boundaries.xml` **36 / 0 / 0 / 0**、全量 `p4-02c-full.xml` **1666 / 0 / 0 / 48**（217.7 s）、
`--mode all` 探针六场景 `ok=true`（entry applied 300 / 第二次 0 / 持锁 p95 119.6 ms / 审计 9.24 ms/工件；
contention applied 301；busy_timeout 1 次拒绝后全部确认），Ruff / `git diff --check` /
`uv lock --check` 全 exit 0。

**BLOCKED**：真实中文 CLIP 权重的端到端重执行未跑——默认模型目录
`%LOCALAPPDATA%\PhotoSearchData\models` 与 `D:\PhotoSearchData` 实测均不存在，
故重执行语义只用确定性注入编码器证明；真实模型等价性/耗时/显存、CLI 正路径、
30 万档对领取谓词与审计成本的影响均按报告 §9 保留为未验证项。

**停止点**：请按 V3.1 §11 在**新会话**用 **DS-H / DeepSeek V4.1 Flash / 高推理**做主审并写
`docs/reports/P4-02C-dsh-engineering-review.md`（完整 diff、隔离谓词与审计幂等语义、
D 级注入真实性、禁止面核对、≥3 处变异测试）；通过后状态写
`ENGINEERING REVIEW PASS / SOL FOCUSED REVIEW PENDING`。随后由 **C2 / GPT-5.6 Sol / high**
聚焦终审（领取谓词、revision/幂等、旧 revision 不回滚、恢复链与失败关闭边界）——
**Sol 必选**；通过后才标 P4-02C PASS，并把 **P4-02B**（[任务卡](tasks/P4-02B.md)，推荐
DS-L / DeepSeek V4.1 Flash / 低推理）的前置更新为「A2/A3 已收口」。模型切换始终由用户在 Codex 中手动完成。
