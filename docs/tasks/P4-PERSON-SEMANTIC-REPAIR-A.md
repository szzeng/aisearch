# P4-PERSON-SEMANTIC-REPAIR-A — 修复人物相册命名后语义搜索归零

**状态：READY / 待实施（仅任务设计）**  
**日期：2026-10-10**  
**优先级：P1（个人使用功能受阻）**  
**建议实施：GPT-6 High；复核：受影响面简审即可。**

## 1. 目标与现场证据

**目标**：人物命名、合并或人工指定之后，其已有照片仍能在人物相册内按视觉内容搜索；若索引正追赶，应能通过已有维护机制恢复，**不需要重新建立整个图库**。同时修复阻止索引维护 CLI 启动的循环导入。

用户已验证（真实图库，仅读）：

- 人物 `person_id=19` 有 **131** 张有效照片；
- 当前 Chinese-CLIP 活动 generation 中，该人物 `CONFIRMED=0`，但全库 `CONFIRMED=2924`；
- 相册 `POST /api/v1/search` 返回 `authoritative.total=131`、`vector.engaged=true`、`vector.engine_blocks=0`、`vector.candidates=0`、`hits=[]`；
- `semantic_complete=true` **仅代表 generation READY**，`pending_semantic.count=0` 在文本搜索时也**不代表当前人物索引齐全**；
- `python -m photo_search.indexing.cli status/drain` 均在模块加载阶段抛 `ImportError: ... is_busy_rejection ... partially initialized module`，**此前的 drain 未执行、未恢复任何向量**。

**高概率原因，尚待验证**：人物改名触发 `PERSON_CHANGED` → 已有照片向量重投影排入 outbox，并从 `CONFIRMED` 暂降为 `PENDING_REPAIR`；索引维护没有及时追赶，导致相册范围内没有可检索候选。**不得提前写成“改名删除了向量”。** 无 `index_entries`、世代切换或 outbox 失败也可能造成同样症状。

## 2. 开工顺序与最小修复

### A. 先修 CLI 冷启动循环导入（必做）

已确认 import 环：

`indexing/maintenance.py → scheduler/projection.py → persons/__init__.py → persons/service.py → persons/albums.py → api/queries.py → search/documents.py → indexing/maintenance.py`。

最小方案优先：将 `search/documents.py` 顶层 `from photo_search.indexing.maintenance import is_busy_rejection` 移到 `catch_up()` 捕获 `CatalogError` 的局部路径中；也允许把纯 busy 判断下沉到无反向依赖的底层模块，但**二选一**。不得改变 busy 分类、等待预算和现有错误传播语义。

**验收**：用全新 Python 子进程启动 `indexing.cli --help`、`status --data-root <隔离库> --json`、`drain --data-root <隔离库> --limit 0 --json`，均不再循环导入；`--limit 0` 不写入。还要用下述集成场景里的真实待办验证一次非零 drain，不能以 `--help` 代替执行证明。

### B. 一次只读分流，随后只修真实根因（必做）

使用 `persons/members.py::MEMBER_PREDICATE` 和当前激活 `profile` 的 `generation_id + space_id`，按 **DISTINCT asset_id** 统计人物范围：

- `CONFIRMED / PENDING_REPAIR / STALE / NO_ENTRY` 四类照片数（总和必须等于人物有效照片数）；
- 对应 `index_outbox` 的 `PENDING / IN_FLIGHT / CONFIRMED`、最新修订与失败码；
- 核对 `PERSON_CHANGED` 影响范围及 `schedule_projection_refresh` 是否把已确认条目降级；核实后台/刷新流程是否有实际消费入口。

在**合成库**做上述自动诊断；真实图库仅采用用户提供的 `131/0/2924` 证据，**不擅自读取、修改 `PhotoSearchData` 或开启真实服务**。

根据结果只走对应分支：

1. **`PENDING_REPAIR` + 未完成 outbox**：优先复用 `IndexCoordinator` / `drain_semantic_outbox`。补齐人物修改后**可自动或经现有相册维护入口触发**的、事务外、有界、幂等的追赶；服务重启后积压仍可继续。不得在人物写事务内同步写 LanceDB，也不得要求日常使用者重新建库。
2. **`NO_ENTRY`，且没有待重投影事件**：核对当前世代、原始图片和现有 embedding artifact；只有确实缺少向量时才复用 `indexing/semantic.py` 的**增量**建库选择器。现有机制可完成的直接接线；若需大型重构，记录阻断与准确恢复命令，不扩大本卡。
3. **有 `CONFIRMED` 却仍 `engine_blocks=0`**：对照 `retrieval.py::_CONFIRMED_BLOCK_SQL`、规范人物 ID 和过滤参数，修最小查询缺陷。
4. **outbox 被错误阻断**：报告 `last_error_code`，走现有幂等重试/恢复；绝不手工将 entry 标为 `CONFIRMED`。

注意：图片向量 `index_entries`、人脸候选向量 `face_index_entries` 是**不同索引**，不能混为一谈。

### C. 搜索空态（仅一处小改动）

若相册内有权威照片、Vector engaged、`engine_blocks=0` 且 FTS 无候选，显示“此人物的语义索引尚未就绪，可先查看全部照片”，而非一律提示无匹配。利用**已有响应字段**即可；不要修改冻结 `POST /api/v1/search` 响应。若 `engine_blocks>0` 但未命中，保持真实“没有找到匹配的照片”。

## 3. 最小测试（仅必要的 2～3 个小场景）

复用已有合成 SQLite 夹具、fake encoder / 向量替身，不访问用户图库、不加载模型/GPU：

1. **CLI 冷启动 smoke（1 组）**：全新子进程 `--help/status/drain --limit 0` 成功；复用现有 busy 锁测试证明 `catch_up` 语义不变，不再增加一套锁压力测试。
2. **核心集成（1 组）**：构造 3 张照片（其中 2 张属于同一未命名人物、1 张属于别人），先使该人物内容词有向量命中；命名后验证有效成员未丢、投影队列正确；通过**实际生产 drain 或接线的维护入口**恢复 `CONFIRMED`；人物相册搜索重新命中且不混入别人照片。检查幂等重放不产生重复事件；**不重新编码图片**。
3. **分支小断言（可并入第 2 组）**：完全没有向量条目的照片不得被虚报成已确认；搜索缺索引时使用新空态，全部照片浏览不受影响。若本轮根因属于别的分支，替换为那个分支的单个直接回归。

仅跑上述定向 pytest（最多 1～2 个测试文件）、所改 Python 文件的 Ruff、所改 JS 的 `node --check`、`git diff --check`。**不跑全仓、30 万规模、真实模型、GPU、浏览器矩阵或打包。**

## 4. 修改范围和不可破坏项

先检查：`src/photo_search/indexing/{maintenance,cli,store,semantic}.py`、`search/documents.py`、`persons/{operations,members}.py`、`places/revision.py`、`search/retrieval.py`、`web/persons-albums.js`、现有维护接线处；**只改根因所需最少文件**及紧邻测试。

不改 schema/migration、模型/space/阈值、RRF/FTS/Top-K、图片与人脸归属/忽略/撤销协议、`CONFIRMED → PENDING_REPAIR → CONFIRMED` 安全状态机、事务确认顺序、旧 revision 防回退、搜索信封；不引入新常驻任务框架。已实施的 `P4-SEARCH-INTENT-UNIFIED-A` 与本卡独立，不重复改自然语言解析器。

## 5. 交付与真实试用

- 交付 `docs/reports/P4-PERSON-SEMANTIC-REPAIR-A.md`：标明 A/B/C/D 根因、CLI 修复前后、最小测试结果、实际修复了哪个入口；解释 `131/0/2924`，**不得把合成测试说成真实图库已恢复**。
- 若有既存积压，明确给出无需全库重建的**安全、可重复**恢复方式（包括现有 `drain`/增量补建选择及前提），并解释未来命名后怎样追赶。
- 用户现场只需复测一名已命名人物：向量数由 0 上升，搜索有命中，非本人照片不混入。现场操作必须由用户确认后执行。
- 更新 `docs/development-status.md`。完成即停，不顺带做 GC、统一搜索或 UI 重构。
