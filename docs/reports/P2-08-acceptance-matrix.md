# P2-08 — 恢复、过滤、增量、后台竞争与地点人工锁/投影/缺包综合验收报告

日期：2026-09-14。状态：**PASS（B + D 级：完整 unit/integration + 11 条具名故障注入/恢复用例 +
真实浏览器闭环）**。

本卡是 **P2 阶段的收口验收**：不改生产语义，把前序任务各自证明过的能力（扫描/身份、lease、
artifact/outbox/index、地点核心与地名包、派生文本层、双通道/RRF/权威补查、搜索会话/profile/反馈、
搜索页与上传搜图）放进同一份对抗性场景里联测。执行模型（记录用）：本次会话在 Codex 桌面端执行，
任务卡推荐 **DS-H 执行 + C2 high 独立审查**；模型/推理档由用户在 Codex 中手动设置，本任务未尝试、
也未声称自动切换。全部夹具位于 `%TEMP%` 与本次创建的临时工作目录；没有读取、修改、移动或删除任何
真实照片库，没有联网。

> **未发现与 ADR-0002/0003/0005 冲突的真实缺陷，因此没有修改任何生产代码**
> （`src/photo_search/**` 一行未动，`0001`～`0008`、冻结 ADR/JSON schema、依赖均未改）。
> §5 记录了两处「发现的行为事实」（一处是已文档化的进程内缓存边界，一处是本设计期望的投影追赶语义），
> 供后续决策参考，均不构成本次的最小修复触发条件。

## 1. 基线与交付边界

| 项 | 实际值 |
|---|---|
| 起始 HEAD | `34f7fc084ae5b6334d44f885a972bec814bff8ae`（P2-07）；开始时 `git status --porcelain` 为空 |
| 本卡提交 | 见 `git log -1 --format=%H -- docs/reports/P2-08-acceptance-matrix.md`（未 push） |
| 前置 | P2-07 PASS；`SUPPORTED_SCHEMA_VERSION = 8`、migration 连续 `0001`～`0008`；P2-02～P2-07 既有测试原样通过 |
| 新 migration | **无**；`schema_version` 仍为 8，`0001`～`0008` 逐字节未改 |
| 新增测试 | 3 个文件 + 1 个共享夹具模块，**11 个具名用例** |
| 改动生产文件 | **无** |
| 新增工具/文档 | 本报告；`docs/development-status.md`；`docs/tasks/P3-01.md`（下一卡） |

| 交付物 | 位置 |
|---|---|
| 场景 1–5（恢复/重启/迟到结果/内容变化） | `tests/integration/test_p2_acceptance_recovery.py`（5 例） |
| 场景 6、7、8、10、11（增量/过滤/后台竞争/升级边界/上传） | `tests/integration/test_p2_acceptance_search.py`（5 例） |
| 场景 9（地名包生命周期） | `tests/integration/test_p2_acceptance_datasets.py`（1 例） |
| 共享夹具与只读快照工具 | `tests/p2_acceptance_fixtures.py` |

## 2. 场景 → 注入方式 → 实测结论（任务卡 1–11 条）

每条都是**具名用例**、在 `%TEMP%` 夹具上注入**已有注入点**，并回读 SQLite 权威事实。所有断言都由
`uv run --no-sync pytest -q -p no:cacheprovider <file>` 真实验证。

| # | 用例 | 注入方式（既有注入点） | 实测结论 |
|---|---|---|---|
| 1 | `test_an_interrupted_scan_resumes_after_a_restart_without_false_missing` | `service.run_scan(source_id, interrupt_after=2)`；真实关闭写入进程并重新 `CatalogService.open`（时钟前进 120 s 使 lease 过期）；真实 `os.replace` 移走来源根目录 | 中断后 `scan_run=RUNNING`、job 持租约、3 个新位置待确认、`locations_missing=0`、`assets` 不变；离线窗口内扫描 `BLOCKED`＋`SOURCE_OFFLINE`、**仍无缺失**、人工归属行与 `catalog_filter_revision` 逐项不变；恢复卷后**同一个 `scan_run` 被接管**并 `SUCCEEDED`（`files_seen=6`、`locations=6`、`assets=6`、`scans=2`：初始成功扫描 + 被接管的那次），人工归属仍指向同一 `place_id` |
| 2 | `test_a_crash_before_the_sqlite_commit_recomputes_and_stays_consistent` | `monkeypatch.setattr(scheduler.artifact_store, "persist_result", crash)`（提交前抛异常），随后同 lease 重放；`IndexCoordinator.process_all` 重放 outbox | 崩溃后该资产的 `artifacts=0`/`index_outbox=0`/无 `*.tmp`、job 仍 `RUNNING`（无半完成记录，其余 3 个既有条目不受影响）；重放后 `artifacts=1`/`outbox=1`/`index_entries=PENDING_REPAIR`；确认后 `semantic_ready=true`，`POST /api/v1/search` 的同一资产同时带 `fts`+`vector` 名次、`semantic_ready=true`、`pending_semantic.count=0`（文本与向量通道状态一致） |
| 3 | `test_a_lance_write_without_confirmation_is_named_and_replays_once` | `process_next(failpoint="after_lance")`；用新的 `IndexCoordinator` 重放；`POST /api/v1/search` 实查 | 崩溃后 `index_outbox=IN_FLIGHT`、`semantic_ready=false`；查询**不静默变少**：该资产命中行 `matched_by=["fts"]`、`pending_semantic.entries[].entry_state=PENDING_REPAIR`、`pending_events=1`；重启重放后引擎行数仍为 4（无逻辑重复）、`pending_semantic.count=0`、该资产带回 `vector` 名次 |
| 4 | `test_an_expired_lease_and_a_late_resolution_cannot_change_locked_facts` | 过期租约后 `scheduler.submit(..., now_ms=+10 min)`；另一 worker 接管；安装地名包 + GPS + 解析，人工 assign→unassign 后再跑一次解析 | 迟到提交 `STALE_LEASE` 且 job 行逐列不变、artifact/outbox 零新增；接管后恰好 1 份产物；人工 `MANUAL_UNASSIGNED` 行不变、`asset_place_exclusions.active=1`、`suggestion_view.state=candidates`、被排除地点不再出现在候选里（`excluded_candidate_count=1`）——迟到机器结果不能覆盖人工锁/排除 |
| 5 | `test_replaced_content_never_inherits_the_old_results` | 真实覆盖文件字节（尺寸不同）+ `run_scan(interrupt_after=0)`；随后完整重扫；`documents.rebuild_all` | 中断后该位置 `PRESENT` 但 `asset_id IS NULL`（不继承更早执行的观测）、`locations_missing=0`、旧资产的 `search_documents`/`index_entries` 逐列不变；完整扫描后新内容绑定到**新** `asset_id`（旧资产仍 `ACTIVE`、旧向量条目仍 `CONFIRMED`、新资产未就绪）；文本投影按 P2-04 规则各自成为独立行（旧资产不再有 PRESENT 位置→文件名为空，新资产拿到该文件名）；已发布的旧缩略图字节与时间戳不变且仍可 200 取回，新内容生成自己的缩略图 |
| 6 | `test_add_rename_move_and_delete_stay_consistent_with_search` | 真实 `os.replace` 改名 + 移到子目录、新增、删除；一次 `rescan` | `files_seen=3`、`locations_missing=3`（两个旧路径 + 被删文件）；改名/移动后位置改绑但**同一 `asset_id`**、`catalog_filter_revision` 不变；改名后**第一次** `GET /assets?text=renamed` 与 `POST /search(text=renamed)` 都命中；`place_ids` 的有效归属、`GET /assets` 与 `POST /search` 在同一 `catalog_filter_revision` 下给出一致结果；被删资产只在 `availability=missing` 下出现 |
| 7 | `test_place_and_source_filters_stay_exact_and_reject_illegal_combinations` | 建 国家/城市/同名城市/自定义 地点并真实归属/取消；第二个来源放跨来源副本 | 祖先展开命中子级、`include_descendants=false` 立刻为空；同名地点各自命中自己的资产、OR 合并正确；`manual_unassigned` 与 `unassigned` 各自语义正确（人工锁没有有效归属）、`assigned`=2；`place_ids + place_state=suggested` → 422 `INVALID_PLACE_FILTER`；无地名包时 `suggested` 确定为空；跨来源副本共享身份（`assets` 仍 3、`locations` 4），来源条件与地点条件 AND |
| 8 | `test_a_live_session_answers_with_identifiable_staleness_codes` | 真实 place 写；真实 GPS/解析 job；`activate_profile` 切换；`fresh_app` 模拟重启；`resolver_module.clear_content_cache()`（模块自带的「新进程」助手） | 同一会话内续页 `page_offset=1` 无重复；独立会话对同一条件给出**相同冻结顺序**；地点编辑后续页 → 409 `CATALOG_FILTER_REVISION_STALE`；新的机器解析改变可见候选集后（`catalog_filter_revision` 不变）续页 → 409 `PLACE_SUGGESTION_REVISION_STALE`；会话中途切换 profile → 409 `PROFILE_GENERATION_MISMATCH`（`reason=SESSION_BINDING_CHANGED`）；重启后的进程 → 409 `SEARCH_SESSION_EXPIRED`——都不是空页、也不是静默新排序 |
| 9 | `test_the_name_package_lifecycle_never_touches_manual_places` | 删除活动包文件 / 同长度篡改 / 还原 / 安装第二个包 / `rollback_dataset` / 重启（`reopen_service`） | 缺包：`/places/datasets` 与 `dataset_status` 都报 `missing`+`PLACE_DATASET_MISSING`+`resolver_available=false`，旧建议仍可读、人工归属/排除/地点目录逐项不变，**冷进程**新解析被阻塞（`PLACE_DATASET_MISSING`，job 记稳定码，零新 resolution 行）；篡改同长度字节 → `invalid`+`PLACE_DATASET_INVALID`；还原后回到 `active`；安装第二包 → `geo-mini-cn-2` ACTIVE、旧包 RETAINED；重启后指针仍是第二包；回退 → 第一包 ACTIVE，人工状态与建议视图逐项不变（详见 §5.1 的 warm/cold 说明） |
| 10 | `test_an_old_ready_generation_keeps_serving_a_cancelled_upgrade` | `create_generation(state="PENDING")` + `create_profile` + `activate_profile` | 半成品升级目标被结构化拒绝（`PROFILE_GENERATION_MISMATCH`/`GENERATION_NOT_READY`），活动指针仍是 `profile-1`，旧 READY generation 行逐列不变，`POST /search` 仍由 `profile-1`+旧世代正常服务。**未声称**任何未实现的模型升级/切换能力 |
| 11 | `test_upload_search_writes_nothing_and_reuses_the_authoritative_recheck` | 真实中断扫描在途 + 真实人工地点编辑 + `monkeypatch.setattr(routes, "DEFAULT_LIMITS", ...)` 注入小上限 | 人工编辑把该资产的已确认向量条目降级为 `PENDING_REPAIR` 后，上传搜图**不静默给少结果**：该资产出现在 `pending_semantic.entries`（`reason=ENTRY_NOT_CONFIRMED`）而不在命中行里；投影重放回 `CONFIRMED` 后上传搜图把它列为 `hit_reason=SEMANTIC`；坏图 409 `DECODE_FAILED`、像素超限 422 `UPLOAD_TOO_MANY_PIXELS`、字节超限 413 `UPLOAD_TOO_LARGE` 三条失败路径与成功路径都让 8 张表的计数、`catalog_filter_revision`、`assets.created_at` 与临时目录集合逐项不变，在途 `scan_run` 仍 `RUNNING`；取消归属后会话续页 → 409 `CATALOG_FILTER_REVISION_STALE`，同一筛选条件下文本与图像两条通道都给出 0 命中（同一权威复核） |

## 3. 验收命令与结果

| 命令 | 结果 |
|---|---|
| `uv run --no-sync pytest -q -p no:cacheprovider tests/unit tests/integration` | exit 0；`--collect-only` 计 **802** 例（P2-07 基线 791 + 本卡 11），进度行 1 个 `s` → **801 passed / 1 skipped**（131 s 量级） |
| 本任务 3 个新文件（连续两次全绿） | exit 0：`test_p2_acceptance_recovery.py` 5、`test_p2_acceptance_search.py` 5、`test_p2_acceptance_datasets.py` 1 |
| `uv run --no-sync ruff check .` | exit 0，`All checks passed!` |
| `git diff --check` | exit 0，无输出 |
| `uv run --no-sync uv lock --check` | exit 0，`Resolved 95 packages`（依赖未变） |
| C 级判定 | **未触发**（按卡：未改前端资源、打包入口/package-data、依赖、`workers/**`，也没有生产修复） |

本环境沿用既有已记录事实：`uv` 默认缓存在沙箱内返回 `os error 5`、pytest 的 `--basetemp` 在沙箱内被
ACL 拒绝，因此上述命令在沙箱外执行（无功能影响）；用例数用 `--collect-only` + 进度行的 `s` 统计，
退出码作为成败依据。本卡另外确认一条工具事实：用 Windows `Start-Process -ArgumentList` 启动服务时，
含空格的参数**必须自带引号**，否则参数在空格处被切开（见 §6.3）。

## 4. 真实浏览器闭环（任务卡第 12 条）

夹具：`uv run --no-sync python tools/diagnostics/search_smoke_fixture.py --photos 6 --reset
--work-dir "%TEMP%\P2-08-acceptance"` → 6 张合成照片、1 个 READY 世代（资产 #1、#6 已确认向量）、
地点 `#1 杭州西湖` → 资产 #6、激活 profile `search-smoke-profile`。
服务：`python -m photo_search --port 58310 --data-root "<夹具数据目录>"`（仅 127.0.0.1）。
浏览器：Codex 内置浏览器，真实端口、真实一次性会话链接。逐步观察：

| # | 操作 | 实测观察 |
|---|---|---|
| 1 | 打开一次性链接 → `/` → 点导航「搜索图片」 | 303 落到 `/`；shell 显示「图库 schema v8（ok）」，来源 `搜索样片` 状态 在线、资产 6、在库位置 6、缺失 0；进入 `/search` 后顶部提示「当前搜索配置 search-smoke-profile · 图像世代 image-gen-7ee29839419503a3f311cfbe 本进程没有查询编码器…页面不会把这种情况显示成「没有结果」」 |
| 2 | 每页数量 2 → 清空查询文本 → 点「搜索」 | 状态行「已显示 2 行 · 候选共 6 个 · 还有下一页」；两行分别「第 1/2 名 资产 #1/#2 仅筛选…通道名次：权威列表次序 1/2」；结果区同时出现「文本投影仍在追赶：还有 6 个资产…」与「本次没有查询文本…（hit_reason=FILTER_ONLY）」 |
| 3 | 第二个标签页打开 `/places` | 会话共享成功（未要求重新登录）：地点目录显示 `城市 杭州西湖 #1 启用 origin USER`、目录 revision #1；地名包状态如实显示「未安装地名包（unavailable）」「解析器不可用（新解析会被阻塞）」；操作日志有 1 条 `ASSIGN rev 1 COMMITTED`。（本构建无地名包 ⇒ 该页的「建议与批量整理」没有可选资产，因此并发修改改用同一数据根上的**第二个写者**走生产 `PlaceService.write_assignment` 路径） |
| 4 | 并发写入：资产 #2、#3 → 地点 #1 | 两次 `ASSIGN` 都 `COMMITTED`；`catalog_filter_revision` 1 → 3 |
| 5 | 回到搜索页点「加载下一页」 | **过期提示**：「本次查询没有成功：[CATALOG_FILTER_REVISION_STALE] the effective place of at least one asset changed after this search started（请求编号 60a49d8a6c2fdbb7）」＋「地点或可见性在翻页期间发生了变化，旧会话不再有效；请重新搜索。」——不是空页、不是静默新排序 |
| 6 | 地点 place_id=1 + 地点状态=已归类 + 每页 5 → 「搜索」 | 「已显示 3 行 · 候选共 3 个 · 已到末尾」：资产 #2、#3（第 4 步刚写入的）与 #6，均为「仅筛选…权威列表次序 1/2/3」 |
| 7 | 再并发写入：资产 #4 → 地点 #1（revision 3 → 4）→ 点「按当前条件重搜」 | 「已显示 4 行 · 候选共 4 个 · 已到末尾」，第 3 名是**新补回的资产 #4**，#6 顺延到第 4 名——重搜后权威补查把并发新增的归类补了回来 |
| 8 | 真实移走来源根目录（`os.replace` 到 `照片库 离线中`）后在 shell 点「扫描」 | 来源卡状态 在线 → **离线**；「扫描已结束。扫描状态 BLOCKED · 尝试 0/3 · 进度 0% · 最近错误 SOURCE_OFFLINE」；缺失位置仍为 **0**（离线不产生删除结论） |
| 9 | 回到搜索页点「刷新覆盖率」 | 四项独立渲染：来源可用性「登记来源 1 / 在线 0 / **离线 1** / 在库位置 6 / 缺失位置 0 / 资产总数 6」；地点覆盖「已归类 4 / 未归类 2」；语义索引「已确认 1 / 待修复 1 / 未确认 outbox 1 / 已确认 outbox 2 / 活动世代 …（READY）/ LAGGING」；文本投影「0 / 6 仍在追赶」；末行仍是「不换算成一个整体准确率」 |
| 10 | 服务访问日志 | `POST /api/v1/search` → **409 Conflict**（续页过期）；随后的重搜 200；`POST /api/v1/sources/1/scan` → 202；`GET /api/v1/places/coverage`、`/api/v1/stats`、`/api/v1/sources` 均 200；不出现任何绝对路径或坐标 |

第 5、7、9 步合起来就是本卡要求的闭环：**先搜索 → 期间用另一个写者修改地点 → 续页得到可识别的过期码 →
重搜补回结果**，并额外覆盖了一个**离线来源**提示。浏览器侧的已知限制沿用 P2-07 §8.2：本构建没有
真实文本/图像编码器，因此 `SEMANTIC`/`TEXT+SEMANTIC` 行与「语义索引仍在追赶」在浏览器里仍不可达
（其数据路径由 §2 的场景 3/11 覆盖）。

## 5. 联测发现的行为事实（均不构成本次最小修复触发条件）

### 5.1 地名包：同一进程内「已校验过的解析」在包文件被删除后仍可用，直到进程重启

- **症状**：删除活动包的 `places.jsonl` 后，`GET /api/v1/places/datasets` 与 `dataset_status` 都报
  `missing`/`resolver_available=false`，但**同一进程内**再跑一次解析仍会成功（返回与包内容一致的
  `CANDIDATES`）；把缓存清掉（等价于**新进程**）后同一资产才被 `PLACE_DATASET_MISSING` 阻塞。
- **根因**：`geo/resolver.load_active_content` 在 `cache is None` 时使用模块级 `_CONTENT_CACHE`，
  键为 `dataset_id:content_sha256`；该模块明确注释「executor warms it…a cold process (or a
  recovered job) verifies the package again on first use」，并提供 `clear_content_cache()`
  （注释为「tests and diagnostics model a fresh process」）。
- **判定**：这**不是**与 ADR-0005 冲突的缺陷。ADR-0005 §8 的复核承诺绑定在**重启**路径上
  （「提交后重启按新指针复核，坏包阻塞解析」，且「缺包／坏包…只阻塞解析任务」）。缓存键含**已校验的
  内容哈希**，因此不可能把另一个包的内容当成当前包；本卡按 ADR 的承诺实测了冷进程路径并全绿。
- **未做修改**：改动它会改变「每次解析是否重新校验整包 SHA-256」的运行代价与语义，属于运行边界/
  数据语义决策；如用户希望「运行中进程也必须每任务复核磁盘」，应作为独立卡片由 **C2** 决策，
  本卡不擅自扩大范围。

### 5.2 人工地点编辑会先把该资产的向量条目降级为 `PENDING_REPAIR`（这是期望的「投影追赶」语义）

地点事务通过 P2-03 的 `schedule_projection_refresh` 在同事务里排入刷新事件，并把
`index_entries` 置为 `PENDING_REPAIR`（`confirmed_at=NULL`），直到 indexing 重放该事件。实测表现：

- 编辑后立刻做上传搜图/文本查询：该资产**不在命中行里**，但会在 `pending_semantic.entries` 里被具名
  （`entry_state=PENDING_REPAIR`、`pending_events=1`、`reason=ENTRY_NOT_CONFIRMED`）——不会静默变少；
- 浏览器覆盖率视图同步显示「待修复条目 1 / 未确认 outbox 1」（§4 第 9 步），与库内事实一致；
- 重放后条目回到 `CONFIRMED`，该资产才重新进入向量通道。

这与 ADR-0003 §7.2「只有第 5 步完成语义能力才显示 READY」和 P2-05「投影滞后要权威补查」一致，
因此**不改**。

### 5.3 夹具层面的两次修正（非产品缺陷）

- 三张「只差 1 个颜色分量」的合成 JPEG（101/102/103,40,60）被编码成**逐字节相同**的文件，于是按内容
  身份合并成 1 个资产（3 个位置）。这不是产品缺陷（内容身份本来就该这样），但会掩盖夹具意图；
  已改用差异明显的颜色，并在场景 8 加了一条 `len({asset_id}) == 3` 的自检断言。
- `Start-Process -ArgumentList` 的数组元素不会自动加引号：含空格的 `--data-root` 会在空格处被切开，
  表现为 argparse 报「invalid choice」。加引号后正常；这条只影响本次 smoke 的启动方式，不影响产品。

## 6. P2 阶段收口：已实现并验收 / 未实现 / 排入 P3、P4

**P2 已实现并验收（本报告与 P2-01～P2-07 报告为证据）**：目录身份与缺失/重映射（ADR-0002）、
JobRequest/Result 五字段身份与 `device_policy`/运行时能力（ADR-0003）、持久调度/租约/退避/暂停恢复、
artifact/outbox/幂等 LanceDB 提交与修复、地点核心（目录/GPS 事实/人工归属/取消锁/批量/撤销/纯 SQLite
筛选，ADR-0005）、离线地名包与 suggestion-only 附近城市、派生文本层与受控 MATCH、双通道并行召回与
RRF/权威补查、搜索协议与会话/游标/profile 原子激活、显式反馈与精排适配器边界、搜索页/上传搜图/地点
筛选/分项覆盖率，以及本次的综合对抗性验收。

**P2 未实现（不得描述为已完成）**：

- 真实图文编码器与真实精排/VLM 权重仍未接入应用进程 ⇒ 打包/开发进程里文本查询 409
  `QUERY_ENCODER_UNAVAILABLE`、上传搜图 409 `IMAGE_ENCODER_UNAVAILABLE`，浏览器里看不到
  `SEMANTIC`/`TEXT+SEMANTIC` 行；没有任何召回/排序质量结论。
- 没有产品侧的 `search_profiles`/`index_generations` 创建入口，也没有模型升级状态机
  （DRAFT→…→ACTIVE→RETAINED 的 upgrade_run、试建库、覆盖率校验、原子切换、回退追赶）——本卡只实测了
  「旧 READY 世代继续可用」这一半。
- 人物/OCR 完全未实现（P3/P4）；地点侧的精确自动归类、围栏、地点合并/改父级仍按 V3.1 §7.2 后置。
- 无常驻调度线程：`photo_search.geo.extract`/`geo.resolver`/`scheduler.engine` 不在冻结包内。

**排入 P3/P4 的欠项（沿用前序记录，不在本卡解决）**：拍摄时间 `taken_at`（P4-00A/B）、HEIC（P4-00C）、
产品 remap（P4-05A/B）、真实 16 MiB/60 MP 上传、DOM 自动化、跨进程会话、真实 USB/SMB 卷级故障、
人物同名合并/拆分/纠错、ANN 维护与规模基准。

## 7. 未验证项与残留边界

1. **真实模型与语义质量**：本卡所有「语义命中」都来自确定性假编码器；没有真实模型、GPU 延迟、
   召回率或人工标注 holdout 结论（沿用 P2-05/06A/06B/07）。
2. **浏览器侧语义行不可达**（§4 限制）：`SEMANTIC`/`TEXT+SEMANTIC` 与「语义索引仍在追赶」提示需要
   进程内有编码器；其数据路径由集成用例覆盖，页面的渲染由 P2-07 静态断言覆盖。
3. **没有 DOM 自动化**（沿用 P1-05B/P2-03C/P2-07）：前端证据 = 静态契约断言 + 本报告记录的
   真实浏览器闭环。
4. **搜索会话是进程内状态**：本卡用 `fresh_app`/`clear_content_cache` 在进程内模拟「新进程」，
   没有真正跨进程/多 worker 的会话与并发写者压力测试；地点/反馈是持久表，两者语义不同。
5. **非快照隔离读**：`GET /assets`、页面与会话内复核都逐行用最新权威条件复核，但没有跨请求一致
   快照（沿用 P1-04/P2-03A/P2-06A/P2-07）。
6. **规模未测**：候选分块扫描、`MAX_VECTOR_BLOCKS`、`top_k=1000`/`page_size=100` 上界、
   16 MiB/60 MP/20 s 上传上限、`search_feedback` 的规模与并发写入都只在小型夹具上验证公式；
   `CATCH_UP_BATCHES × DRAIN_BATCH_SIZE` 的单请求追赶上界仍沿用 P2-04 记录。
7. **地名包**：只用极小合成子集（真实 GeoNames 未使用）；最近点是城市点线性扫描；切包不自动重算
   既有 resolution；§5.1 的进程内缓存边界；`/places/datasets` 的 `missing`/`invalid` 判定依赖
   磁盘复核，因此与「运行中进程仍可解析」并存（已如实记录）。
8. **卷级故障**：断盘仍以真实 `os.replace` 移走来源根目录模拟，不是真实外接盘/网络盘的
   `ERROR_NOT_READY`/USB 拔出/SMB 断链；该平台特有形态仍未在真实硬件上验证。
9. **产品入口缺口**：`POST /sources/{id}/remap`、profile/generation 创建入口、升级状态机、
   `PLACE_OPERATION_NOT_UNDOABLE` 的「目标被后续编辑」分支在页面上仍不可达（沿用前序记录）。
10. **反馈不影响排序**：`search_feedback` 只记录；在线学习属新的产品决策（P2-06B §11）。

## 8. 结论与交接

P2-08 的 11 条具名对抗性场景（扫描中断与重启、提交前终止、Lance 已写未确认、过期租约与迟到结果、
处理期间文件变化、增量一致性、过滤正确性、后台竞争与过期码、地名包生命周期、升级边界、上传搜图组合）
全部在 `%TEMP%` 夹具上真实注入并通过，加上 1 次真实浏览器闭环（含离线来源提示），因此标记 **PASS**。
`schema_version = 8`、migration `0001`～`0008`、ADR、冻结 JSON schema、依赖、`src/photo_search/**`
与前端/打包入口**零改动**；本卡只新增测试与文档。

下一任务是 **P3-01 — OCR 任务、原文/区域/置信度与 FTS 更新**（V3.1 §8），任务卡见
[docs/tasks/P3-01.md](../tasks/P3-01.md)，推荐 **DS-L（调度逻辑部分可标注 DS-H；运行库疑难转 C1）**，
验收 **B（涉及迁移/打包再加 D/C）**。
