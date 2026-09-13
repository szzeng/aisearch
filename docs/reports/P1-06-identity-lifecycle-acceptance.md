# P1-06 — 重复、移动、替换、断盘、重映射、重启验收

状态：**PASS**（D 级验收真实完成：全套 `pytest` + ruff + `uv lock --check` + `git diff --check`，
并逐条真实执行了任务卡要求的 6 类对抗性场景）。
执行模型（记录用）：本次会话在 Codex 桌面端执行，任务卡推荐 **DS-H / DeepSeek V4.1 Flash / 高推理**；
模型/推理档由用户在 Codex 中手动设置，本任务未尝试、也未声称自动切换。

本任务性质：**验收闭环，不新增功能**。任务卡：`docs/tasks/P1-06.md`。
基线：P1-05 收口提交 `e2654caa9daba8f86dadf31fe7afd5fc8fe621e8`
（`git log -1 --format=%H -- docs/reports/P1-05-*.md` 复核一致；其后 `c892176` 只新增 P1-05A/P1-05B
任务卡、`0daad95` 是 P1-05B、`b708847` 是 P1-05A 脱敏补强，均不改变 P1-05 基线语义）。
开始实现时 HEAD 为 `b708847`，`git status --short` 为空（工作树干净）。
冻结契约：`docs/decisions/ADR-0002-catalog-identity-migration-scan-contract.md`。

**没有修改任何生产代码。** `src/photo_search/**` 一行未动（含 `catalog/**`、`ingestion/**`、`jobs/**`、
`api/**`、`web/**`）；`schema_version` 仍为 1；`0001_initial.sql`、ADR-0002、`pyproject.toml`、
`uv.lock` 均未改动。全部 16 个新用例在**第一次按契约语义写对之后**即通过：本轮对抗性验收**未发现**
与冻结契约冲突的真实缺陷，因此没有触发「最小修复」分支。

---

## 1. 交付物清单

| 交付物 | 位置 |
|---|---|
| 对抗性验收闭环（16 个新用例，全部新建文件） | `tests/integration/test_catalog_identity_lifecycle.py` |
| 本报告、状态更新、下一任务卡 | `docs/reports/P1-06-identity-lifecycle-acceptance.md`、`docs/development-status.md`、`docs/tasks/P2-01.md` |

既有测试文件**一条未删、一条未弱化**；本次只新增文件与文档。

## 2. 开始前必须先检查（任务卡「开始前必须先检查」第 1-5 项）的实测结论

1. **语义落点核对（读源码，未改动）**：`store.upsert_asset`（`sha256` 唯一内容身份，`byte_size`
   不一致即 `ASSET_SIZE_MISMATCH`）、`store.bind_location_content`（提交时再校验观察值）、
   `store.observe_location_detailed`（size/mtime 变化即清空 `asset_id`，同一次执行内不同拼写同
   `path_key` 即 `PATH_KEY_COLLISION`）、`store.reconcile_missing` / `count_unbound_locations` /
   `reset_execution_coverage`（缺失与覆盖只属于「当前完整执行」）、`store.set_last_successful_scan`
   （必须指向同来源 `SUCCEEDED` 扫描）、`scanner.execute_scan`（最终事务内缺失对账 + `SUCCEEDED` +
   `last_successful_scan` + job 成功）、`jobs.claim_job` / `_start_running`（接管 `attempts += 1`、
   超上限转 `FAILED`）、`0001_initial.sql` 的 `UNIQUE(source_id, path_key)` 与
   `UNIQUE(subject_type, subject_id, input_revision, task_type, processor_id)` —— 结论：**与任务卡
   描述一致，可按下述场景执行**。
2. **已有覆盖清点（避免重复造测试）**：`tests/integration/test_catalog_scan.py`
   已覆盖跨来源共享资产 + 丢失一个位置、同路径换内容重绑且保留旧资产、离线来源 BLOCKED 且不产生
   MISSING、中断后同 `scan_run` 恢复、同盘改名与删除；`tests/integration/test_catalog_scan_streaming.py`
   已覆盖子目录移动、稳定等待、hash 前后校验、权限失败 BLOCKED、完整成功才产生缺失；
   `tests/unit/test_catalog_jobs.py` 已覆盖 lease/接管/`attempts`/迟到提交。这些用例都**只验证片段**，
   且**没有**把「内容寻址的机器产物共享/不重算」「重映射」「断盘期间产物仍可读」「杀进程 + 停机期间
   替换/删除」「>1000 文件」「替换恰好命中既有内容」串成一次可独立验收的闭环。本次新增的正是这些缺口。
3. **API/前端不重复验收**：未改 `src/photo_search/api/**` 与 `src/photo_search/web/**`；与本任务
   相关的 API 事实只用一次只读清点（见第 5 项）。
4. **夹具约束**：全部夹具在 pytest `tmp_path`（`%TEMP%`）下；未读取、未修改、未移动、未删除任何真实
   照片库；断盘用「重命名/移动来源根目录」这一真实文件系统事件模拟（`os.replace`），并在 §5 说明其局限。
5. **重映射现状（实测）**：仓库里**不存在** `POST /api/v1/sources/{id}/remap` 或任何等价的
   `source_roots.root_path` 更新产品入口（`rg` 全仓仅命中任务卡与 `paths.py` 注释）。用
   `create_app().openapi()["paths"]`（`create_app` 无 I/O）实测到 14 条注册路径，包含
   `/api/v1/sources/{source_id}`，**没有任何路径包含 `remap`**。因此本任务的重映射验收按任务卡第 5 项
   走「测试夹具层的 `source_roots.root_path` 受控更新」，并**不新增**产品 API 或迁移。

## 3. 场景 → 用例映射、夹具与断言

夹具统一用 `CatalogService.open(data_root=tmp_path/"PhotoSearchData", clock=注入时钟,
wall_clock_ms=注入 wall, stability_sleep=wall.advance)`；文件 `mtime` 一律回拨 1 小时，因此稳定窗口
不产生真实等待（`stability_wait_ms == 0`）。媒体场景使用**可解码的小 JPEG**（P1-03 契约要求真实图像），
其余内容身份场景使用任意字节（扫描层不要求图片格式）。

| 卡片场景 | 用例 | 夹具 | 断言的库内事实 |
|---|---|---|---|
| 重复内容 | `test_duplicate_content_shares_one_asset_and_one_artifact` | 同一内容 4 份：同来源 3 份（含中文/空格目录、>220 字符长路径、只读原文件）+ 另一来源 1 份 | `assets_total == 1`；4 个位置全部绑定同一 `asset_id`；`sha256`/`byte_size` 正确；4 个原文件 `(size, mtime_ns)` 扫描前后完全一致（原图只读）；缩略图**只发布 1 个**且路径正是 `<data_root>/thumbnails/catalog-thumbnail@1/<sha[:2]>/<sha[2:4]>/<sha>.jpg`；第二次生成报 `already_published == 1`；删除同来源 1 份后该位置 `MISSING`、其余 `PRESENT`、来源内 `assets` 仍为 1、产物字节与时间戳不变；删除另一来源唯一位置后资产行仍存在（来源内 `assets` 仍为 1、全局 `assets_total` 仍为 1）且产物仍 `published == 1` |
| 移动 | `test_move_keeps_identity_and_reuses_the_same_artifact` | 同来源 `a.jpg` → `2024 年/照 片 一.jpg`（真实 `os.replace`） | 新位置 `PRESENT` 且 `asset_id` 不变；旧位置 `MISSING` **并保留最后已知绑定**；位置数 3、`assets_total == 2`、`locations_missing == 1`；缩略图路径与 `(size, mtime_ns)` 完全不变、`already_published == 1`（未重算、未新增） |
| 替换 | `test_replacing_content_rebinds_without_touching_the_old_asset` | `a.jpg` 被不同长度新内容覆盖 | 新位置绑定到**新内容**的 `asset_id`（`sha256` = 新字节）；旧资产仍 `ACTIVE` 且 `sha256`/`byte_size` 不变；`assets_total == 3`；扫描中每个位置绑定的 `asset_id` 的 `sha256` 都等于该位置当前字节（不存在「旧指纹指向新文件」） |
| 替换（内容已存在） | `test_replacement_matching_another_position_reuses_that_asset` | 把 `a.jpg` 覆盖成 `b.jpg` 的字节 | `a.jpg` 重绑到 `b.jpg` 的既有 `asset_id`；`assets_total == 2`（不新增第三个资产）；旧资产仍 `ACTIVE` |
| 替换（同尺寸 + 还原 mtime，对抗性） | `test_same_size_replacement_with_restored_mtime_is_still_detected` | 同长度不同字节，并把 `mtime_ns` 精确还原 | 完整扫描仍重新 hash → 位置改绑到新内容资产、旧资产保留：证明「stat 相同也不复用旧身份」 |
| 替换（互换） | `test_swapping_two_positions_rebinds_both_to_existing_content` | 两个位置用真实 `os.replace` 交换内容 | `a.jpg` → 内容 B 的 `asset_id`、`b.jpg` → 内容 A 的 `asset_id`；两位置 `PRESENT`；`assets_total == 2`（不新增身份） |
| 替换（未确认显式） | `test_replaced_content_stays_explicitly_unconfirmed_when_the_scan_stops` | 覆盖后扫描 `interrupt_after=0` 停止 | 该位置 `PRESENT` 但 `asset_id IS NULL`（内容待确认）、`last_seen_scan_id` 指向本次扫描；`scan_run` 保持 `RUNNING`、`error` 为空；`locations_pending_content == 1`、`locations_missing == 0`；`last_successful_scan` 仍为 1（未静默成功）；恢复后绑定到新内容 |
| 替换（stat 完全不变窗口） | `test_same_size_replacement_window_is_corrected_by_the_next_complete_scan` | 同尺寸 + 还原 mtime 后停止扫描 | 停止后位置保留**旧观测**（旧 `asset_id`、旧 size/mtime，符合 ADR-0002 §5.1「size/mtime 只是快速变化提示」），但**没有**静默成功（`RUNNING`、`last_successful_scan` 不变、无 MISSING）；恢复扫描后改绑新内容、旧资产保留（见 §5 残留窗口） |
| 断盘/离线 | `test_unreachable_source_never_becomes_a_deletion_conclusion` | 成功扫描 + 生成 2 个缩略图后，真实重命名来源根目录 | 扫描 `BLOCKED`（`finished_at IS NULL`、`error.code == SOURCE_OFFLINE`）、job `BLOCKED`/`attempts == 0`/租约已清空；`status == OFFLINE`、`last_successful_scan` 不变；`locations_present == 2`、`locations_missing == 0`（**没有**批量删除结论）；两个已发布产物字节/时间戳不变、`media-status` 仍 `published == 2`；未生成的 preview 报 `source_missing == 2`，单独请求报 `SOURCE_FILE_MISSING`；恢复卷后同一 `scan_run` 续跑 `SUCCEEDED` 且 `locations_missing == 0`；随后真正删除的文件才在**下一次完整成功扫描**中变成 `MISSING`（`BLOCKED` 扫描永远不产生缺失） |
| 断盘（扫描中掉盘） | `test_source_vanishing_during_confirmation_blocks_the_scan` | 内容确认阶段真实移走来源根目录（注入 `scanner.sha256_file`） | `FILE_READ_ERROR` → 扫描/job `BLOCKED`；2 个位置仍 `PRESENT`、`locations_missing == 0`、`assets_total == 0`、`last_successful_scan` 为 `NULL`；原文件仅被夹具移动、字节未变；恢复后同 `scan_run` 完成且无缺失 |
| 边界（枚举↔确认窗口） | `test_position_vanishing_before_confirmation_is_blocked` | 枚举后、内容确认前真实删除文件 | 保守 `BLOCKED`、位置保持 `PRESENT`、无 MISSING、无资产；下一次完整扫描才把该位置判 `MISSING`（且 `asset_id IS NULL`） |
| 重映射 | `test_remapping_the_root_preserves_identity_and_positions` | 真实 `os.replace(root, "D 盘符/照 片 新 根")` + 单写事务内 `UPDATE source_roots SET root_path` | 产品 remap 入口确实不存在（OpenAPI 14 条路径，含 `/api/v1/sources/{source_id}`，无 `remap`）；仅改映射时位置行**零改写**；重扫 `SUCCEEDED`、`files_seen == 2`、`locations_missing == 0`；`location_id`/`path_key`/`asset_id`/`size`/`mtime_ns`/`availability` 投影**逐项不变**（`last_seen_scan_id` 前进到 2）；`assets_total` 不变、artifact 未重算 |
| 重启/中断恢复 | `test_restart_recovery_takes_over_the_same_scan_run` | 6 个文件扫描 `interrupt_after=2` → **关闭服务**（模拟进程被杀）→ 停机期间替换 `p1.jpg`、删除 `p5.jpg` → 新 `CatalogService` 重开 | 接管同一 `scan_run`（`scan_id == 1`、`scans == 1`、`jobs == 1`、`reused == True`）；`files_seen == 5`、`locations == 6`（无重复行）；未改动的 `p0.jpg` 身份不变；被替换的 `p1.jpg` 绑定新内容、旧内容资产仍 `ACTIVE`；`p5.jpg` 从未确认 → `MISSING` 且 `asset_id IS NULL`；`locations_pending_content == 0`；`attempts == 1`（一次丢失执行）、`progress == 1.0` |
| 重启（多次接管） | `test_repeated_takeovers_increase_attempts_monotonically` | 两次中断 + 两次真实租约过期接管 | `attempts` 依次 0 → 1 → 2（每接管 +1）；全程 `scans == 1`、`jobs == 1`、位置/资产各 4、无缺失、无待确认 |
| 边界（0/1 文件、空目录） | `test_empty_and_single_file_sources` | 空目录（含空子目录）→ 1 个文件 → 删除该文件 | 0 文件扫描 `SUCCEEDED`（0 位置 / 0 资产 / `last_successful_scan == 1`）；1 文件 `PRESENT`；删除后 `MISSING` 但资产保留（`assets_total == 1`） |
| 边界（>1000 文件） | `test_large_source_scan_has_no_duplicates` | 1001 个互不相同的小文件 | `files_seen == 1001`、`locations == 1001`、`assets == 1001`、`path_key` 全不重复、无缺失；删除 1 个后 `locations_missing == 1`、资产仍 1001 |

## 4. 实际运行的命令与退出码

| 命令 | 结果 |
|---|---|
| `uv run pytest -q tests/integration/test_catalog_identity_lifecycle.py` | exit 0，**16 passed**（本任务新增用例） |
| `uv run pytest -q tests/unit tests/integration`（含 `--junit-xml` 记录） | exit 0，`tests=337 failures=0 errors=0 skipped=1 time=127.111s`（即 336 passed / 1 skipped） |
| `uv run ruff check .` | exit 0，`All checks passed!` |
| `uv lock --check` | exit 0，`Resolved 95 packages`（无依赖变更） |
| `git diff --check` | exit 0（无空白/冲突标记问题） |

失败日志未进入上下文：本轮没有失败用例（首次按契约语义写对即通过，前两次失败均为测试夹具自身写错——
非图像字节被送进媒体管线、以及把 `scan_run` 归属字段当成身份字段比较——修正后即全绿）。

## 5. 真实发现、冻结契约一致性与「不可声称」

**结论：本轮对抗性验收未发现与 ADR-0002 冲突的真实缺陷，也没有做任何生产代码最小修复。**
逐条对照冻结不变量：

- 内容身份 = 完整 SHA-256：同内容 4 个位置共享 1 个 `asset_id`；同尺寸 + 还原 mtime 的替换仍被
  重新 hash 识别（`test_same_size_...`）；两位置互换内容时各自跟随内容身份，不新增身份。
- `(source_id, path_key)` 位置唯一且**来源相对**：重映射来源根后位置键不变，因此 0 缺失、零改写。
- 只有完整成功扫描才产生缺失结论：来源不可达（开始前/确认阶段）与枚举↔确认窗口都只 `BLOCKED`，
  `locations_missing` 恒为 0；真正删除的文件要等下一次 `SUCCEEDED` 才变 `MISSING`。
- 所有位置缺失的资产仍被保留、仍可列出、已有产物仍可读：跨来源场景里删掉一个来源的唯一位置后，
  资产行与 `ACTIVE` 状态保留，已发布缩略图字节不变。
- job/scan_run 状态机、lease 与 `attempts`：每次接管恰好 +1（0→1→2），停机期间替换/删除都不影响
  「同一 `scan_run`、无重复行」；`interrupt_after` 停止的执行不留负向事实、不写成功。
- `schema_version = 1`、无新依赖、无新表/迁移：`uv lock --check` 与源码 diff 均可核对。

**残留窗口（已显式测试并记录，不构成违约）**：若文件内容被替换但**字节数不变且 `mtime` 被还原**，
扫描层的变化提示（size/mtime）不会触发，此时若扫描被中断，位置会在下一次完整扫描之前**保留上一次
观测**（旧 `asset_id`、旧 size/mtime）。ADR-0002 §5.1 已明确「size/mtime 只是快速提示……支持低优先级
完整性复查」，且 P1-02 的扫描对**每个位置每次完整扫描都重新计算完整 SHA-256**，所以下一次完整扫描必然
纠正（`test_same_size_replacement_window_is_corrected_by_the_next_complete_scan` 已实测）。该窗口内
`last_successful_scan` 不前进、不会静默成功。若后续要求「中断窗口内也必须立即解除绑定」，那属于改变
缺失/身份判定边界，必须先交 **C2** 更新 ADR-0002，本任务不自行决定。

**断盘模拟的局限（必须如实记录）**：

- 「离线」是用真实 `os.replace` 移走来源根目录模拟的（拒绝访问/路径不存在），**不是**真实外接盘/网络盘
  的卷级故障（`ERROR_NOT_READY`、USB 意外拔出、SMB 断链）。因此「卷突然消失」这一平台特有失败形态
  仍**未在本机真实硬件上验证**；若后续出现该类失败，按任务卡升级 **C1 / GPT-5.6 Terra / medium**。
- 扫描进行中掉盘同样是用「确认阶段真实移走根目录」模拟；未注入 Windows 特有的卷错误码。
- 未测「两个进程同时写同一个 `catalog.db` 时扫描中途被第二个写者打开」：沿用 P1-02 记录的跨进程写者
  约束，本任务**不新增任何承诺**。
- 重映射只验证到**夹具层**（受控 `root_path` 更新 + 重扫的身份/位置/产物不变）；`POST /sources/{id}/remap`
  的**产品能力**（新根校验、来源重叠复查、映射审计、API 契约）**仍缺**，因此该项的产品层入口标记为
  `VALIDATION PENDING — 缺产品能力，不缺验收能力`。本任务按卡片禁止为此新增产品 API/迁移。
- 稳定窗口、媒体产物契约、API/前端契约沿用 P1-02～P1-05 已冻结的默认值与语义，本任务未改。

## 6. 交接记录

- 本任务新增文件：`tests/integration/test_catalog_identity_lifecycle.py`、本报告、`docs/tasks/P2-01.md`；
  更新 `docs/development-status.md`。生产代码、迁移、依赖、ADR-0002 **零改动**。
- 主线下一任务：**P2-01 — JobRequest/Result、ProcessorSpec、device_policy、RuntimeCapabilities、
  EmbeddingAdapter、SearchProfile 契约**，推荐先由 **C2 / GPT-5.6 Sol / high** 冻结契约，随后由
  **DS-H** 按冻结协议实现（任务卡见 `docs/tasks/P2-01.md`）。
- 模型切换始终由用户在 Codex 中手动完成：下一任务建议使用 **C2 / GPT-5.6 Sol / high**，请手动切换后继续。
