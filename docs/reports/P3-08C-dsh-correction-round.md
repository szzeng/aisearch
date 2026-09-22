# P3-08C 修复轮报告：映射面逐码旁闭与 `STALE` 裁定（DS-H）

- 状态：**CORRECTIONS APPLIED / SOL SHORT RE-REVIEW PENDING**
- 日期：2026-09-22
- 起点：`c7fa68b` + P3-08B 全部未提交改动（`git status --short`：25 modified + 18 untracked，与本轮
  开始时一致）
- 输入：阶段 A 报告 [`P3-08C-dsh-engineering-review.md`](P3-08C-dsh-engineering-review.md) §5（必须修复
  面）、§6（需裁定面）、§9（压缩交接包）
- 允许面：阶段 A §5.4 固定的修复面（`persons/read.py`、`api/routes.py`、两处测试断言）＋该面必须触达的
  转发层（`persons/service.py`）、读取链（`api/models.py` 无改动）、以及 §6.3 二选一中的"实现复活"分支
  （`persons/mapping.py` + 新用例）
- 未改动：ADR-0010、两份 companion（含 65 条矩阵）、migration `0001`～`0014`、依赖、`pyproject.toml`/
  `uv.lock`、`PhotoSearch.spec`、`web/**`、前端、ACL、真实照片库
- 模型：本轮为 **DS-H / DeepSeek V4.1 Flash / 高推理**（用户手动切换）。未升级 DeepSeek max：两条问题均有
  最小反例与固定修复面，未命中 V3.1 §2.2 任何具名条件
- **P3-08C 未 PASS，`docs/tasks/P3-08D.md` 未生成**

## 1. 结论摘要

1. 阶段 A §5.2 的四个真实 HTTP 反例全部修好：`limit=999`、`limit=many`、`state=decided`、
   `GET /face-mappings/not-a-face-id` 现在分别答 `details={"reason":"limit"}`、`{"reason":"limit"}`、
   `{"reason":"state"}`、`{"reason":"old_face_id"}`，全部通过本卡自带 companion 校验器。
2. 同一缺陷族的**第五处**在收口时被发现并一并修好：`INVALID_CURSOR` 的冻结形状是**空对象**，而共享信封
   把空的 `details` 当"未提供的可选项"丢弃，真实 422 响应因此**缺少必填 `details`**，同样被判
   `$: oneOf matched 0`。修复落在映射路径自己的读取层 + 一个显式的信封选项，其它端点响应逐字节不变。
3. §6.3 的二选一裁定为**实现复活**：同一对脸在"存活 + 本次合格"时，`OPEN` group 的映射行从 `STALE`
   回到 `SUGGESTED`（`revision + 1`，不倒退），group 由既有的 `_recompute_group()` 重算；已决策 group
   保持 §7 的"重检测不改写人工决定"，不复活。
4. 独立复跑：专项 **109 / 0 / 0 / 0**，完整 **1451 / 0 / 0 / 48**（48 为未装 pinned 权重的环境跳过），
   Ruff、`git diff --check`、`uv lock --check`（95 packages）全 clean。

## 2. 改动清单

| 文件 | 改动 | 归属 |
|---|---|---|
| `src/photo_search/persons/read.py` | `check_mapping_limit()` 解析 `str` 并统一答 `details={"reason":"limit"}`；`mapping_page()` 的 state 校验答 `{"reason":"state"}`；`mapping_group()` 透出 `CatalogError` 的 `reason`；`decode_mapping_cursor()` 为 `INVALID_CURSOR` 交付空对象闭包 | §5.4 固定面 |
| `src/photo_search/api/routes.py` | `list_face_mappings()` 的 `state`/`limit` 改为透传 `str`，由读取层校验；移除不再使用的 `check_mapping_limit` 导入 | §5.4 固定面 |
| `src/photo_search/persons/service.py` | `PersonService.mapping_page()` 不再 `int(limit)`/`str(state)` 预转换，原样转发 | 固定面的必要转发层（否则新签名不可达） |
| `src/photo_search/api/errors.py` | `ApiError`/`error_payload()`/`error_response()` 增加显式 `closure_details` 选项：**仅当**调用方声明"这是某契约的完整冻结闭包"时，空的 `details` 也照原样渲染 | 第五处缺陷的最小机制（见 §3.2） |
| `src/photo_search/api/handlers.py` | `_api_error()` 把 `error.closure_details` 传给信封 | 同上 |
| `src/photo_search/persons/mapping.py` | `_upsert_mapping_row()` 对"存活且合格"的 `STALE` 对脸在 `OPEN` group 内复活为 `SUGGESTED`（`+1` revision） | §6.3 二选一（复活分支） |
| `tests/unit/test_p3_face_redetection_api_privacy.py` | `test_pagination_bounds_are_enforced` 读取 `details`；`test_mapping_endpoints_are_closed_over_http` 扩到 6 个真实拒绝并用 companion 校验器逐条判定 | §5.4 两处断言 |
| `tests/integration/test_p3_face_redetection_mapping.py` | `test_removed_candidate_becomes_stale` 末段改为冻结复活后的语义；新增 `test_a_decided_group_is_not_reopened_by_a_reappearing_pair` | §6.3 新用例 |

未改 `api/models.py`、`api/queries.py`、`api/security.py`、`persons/contracts.py`、`persons/operations.py`、
`face/**`、`scheduler/**`、`catalog/**`、`web/**` 与任何既有错误码集合。

## 3. 逐条修复与证据

### 3.1 阶段 A §5.2 的四个反例（真实 HTTP，修后）

探针 `.pytest-basetemp-p308c/probe_api_closure.py`（被忽略的临时脚本，驱动真实 ASGI 应用）：

```powershell
$env:UV_CACHE_DIR=(Resolve-Path '.uv-cache').Path
uv run --no-sync python .pytest-basetemp-p308c/probe_api_closure.py
```

| 请求 | 修后 HTTP body | companion 判定 |
|---|---|---|
| `GET /face-mappings?limit=999` | `{"code":"VALIDATION_ERROR",...,"details":{"reason":"limit"}}` | VALID |
| `GET /face-mappings?limit=many` | `{"code":"VALIDATION_ERROR",...,"details":{"reason":"limit"}}` | VALID |
| `GET /face-mappings?state=decided` | `{"code":"VALIDATION_ERROR",...,"details":{"reason":"state"}}` | VALID |
| `GET /face-mappings/not-a-face-id` | `{"code":"VALIDATION_ERROR",...,"details":{"reason":"old_face_id"}}` | VALID |

三条根因都按 §5.4 收口：`check_mapping_limit()` 自己解析 `limit`（含非数字与空值）并以 `reason` 回答；
`mapping_page()` 的 state 校验同形；`mapping_group()` 把 `check_face_id()` 的
`CatalogError(details={"reason": ...})` 原样带进 `ApiError`（非 `VALIDATION_ERROR` 的 `CatalogError` 仍
`raise` 原样，不吞码）；`list_face_mappings()` 两个查询参数改为 `str` 透传，使传输层不再先答
`{"errors":[...]}`。`clamp_page_limit` 等其它列表的边界语义未动。

### 3.2 第五处（同族，阶段 A 未列）`INVALID_CURSOR` 缺必填空 `details`

阶段 A §5.3 把"`INVALID_CURSOR.details == {}`"记为已满足，但那只在 **`ApiError` 层**成立；共享信封
`error_payload()` 把空的 `details` 视为"未提供的可选项"而丢弃，因此**真实 HTTP 体**缺一个 companion 必填
字段：

| 请求 | 修前 | 修后 | companion |
|---|---|---|---|
| `GET /face-mappings?cursor=<pf1:...>` | `{"code":"INVALID_CURSOR","message":...,"request_id":...}`（**无 details**） | `{...,"details":{}}` | 修前 `$: oneOf matched 0`；修后 VALID |
| `GET /face-mappings/aaaaaaaa…` | `{...,"details":{"old_face_id":"aaa…"}}` | 不变 | 修前修后 VALID |

探针 `.pytest-basetemp-p308c/probe_api_closure_b.py` 给出上述实测。

**为什么不能全局改信封。** 其它端点的冻结面并不都接受空对象：`PlaceApiError.details` 只能是"7 键
`PlaceApiErrorDetails` 或 `null`"，而 `places/service.py` 确有 `fail("INVALID_CURSOR", ...)` 这类无
`details` 的拒绝。若把"空 `details` 也渲染"做成全局规则，这些响应会变成 `{}` 从而违反 P2-03 的冻结契约；
阶段 A §5.4 也明确"不得改动其它既有端点的通用约定"。

**采用的机制。** `ApiError(..., closure_details=True)` 显式声明"这个 `details` 是某契约的完整冻结闭包，
即使是空对象也要照原样渲染"；`error_payload()` 默认行为不变（`details is None` 或"空的普通 `details`"
仍不渲染）；`_api_error()` 原样转发该标记。只有映射读取层对 `INVALID_CURSOR` 打这个标记。

**范围证据**（探针 `.pytest-basetemp-p308c/probe_envelope_scope.py`，实测响应键集合）：

| 端点 | 修后 body 键 | 结论 |
|---|---|---|
| `GET /api/v1/sources`（无会话）`SESSION_REQUIRED` 401 | `code,message,request_id` | 与修前逐字节相同 |
| `GET /api/v1/persons?cursor=<pf1:...>` `INVALID_CURSOR` 422 | `code,message,request_id` | 与修前逐字节相同 |
| `GET /api/v1/faces?...` `VALIDATION_ERROR` 422 | `code,message,request_id` | 与修前逐字节相同 |
| `GET /api/v1/places?cursor=<pf1:...>` `INVALID_CURSOR` 422 | `code,message,request_id` | 与修前逐字节相同 |
| `GET /api/v1/face-mappings?cursor=<pf1:...>` `INVALID_CURSOR` 422 | `code,details,message,request_id` | 按 §8.2 补上空对象 |

### 3.3 `STALE` 裁定：实现复活（§6.3 二选一）

选择 **选项 2（实现复活）**：ADR-0010 §3.3 把 `NO_CANDIDATE` 定义为"旧人工锚点仍存活但**本次检测没有
匹配脸**"，§7 的回退条也要求按"当时的存活行 × 本次写入行"重建候选；选项 1 需要在契约轮改已 Accepted 的
ADR-0010，本卡明令不可改。复活规则因此写进实现，并保持全部既有不变量：

- 触发条件：同一 `old_face_id`/`new_face_id` 对**当前存活**且**本次合格**（`_upsert_mapping_row()` 只在
  合格对脸上被调用），且**该 group 仍为 `OPEN`**；
- 结果：该行 `state='SUGGESTED'`、`inherits_person=0`、`match_state=本次证据`、`revision + 1`（不倒退），
  `last_operation_id` 不动（与撤销路径一致）；group 由既有的 `_recompute_group()` 重算
  `candidate_state` 并在内容变化时 `+1` revision；
- 只对 `OPEN` 生效：`reject` 后的 group（`REJECTED`）即便候选重新出现也不复活，保持 §7"重检测不改写人工
  decision"与 §5.3"重复提交答 `FACE_MAPPING_CONFLICT`"；
- 不带任何副作用：不确认、不建关联、不写 control/exclusion、不推进 `catalog_filter_revision`、不写投影；
  复活的 `OPEN` 候选按 §3.4 重新进入保持集（`hold_face_ids()` 里 group `OPEN` 且行非 `STALE`）。

最小反例探针（`.pytest-basetemp-p308c/probe_min_b.py`，合成库，无真实照片）：

```text
1) after upgrade: UNIQUE_HIGH SUGGESTED
2) after candidate removed: NO_CANDIDATE STALE
3) same pair written again: rebuilt == new_face: True | face row alive: True
     | mapping state: SUGGESTED | group candidate_state: UNIQUE_HIGH
   live_candidate_rows: [<new_face_id>]
   detail candidates: [<new_face_id>]
   confirm accepted
```

新用例 `tests/integration/test_p3_face_redetection_mapping.py::
test_a_decided_group_is_not_reopened_by_a_reappearing_pair` 固定边界：`reject` 后候选消失 → 行
`STALE`、group `REJECTED`/`NO_CANDIDATE`；同一对脸再写入 → 行仍 `STALE`、候选数组仍为空、重复提交答
`FACE_MAPPING_CONFLICT`（`review_already_decided`）。

被改写的既有断言：`test_removed_candidate_becomes_stale` 末段原先钉住"复写不复活"，与本轮裁定相反，故
改为钉住复活后的语义（行 `SUGGESTED`、`revision + 1`、group 回 `UNIQUE_HIGH`、候选数组含该脸、该脸零有效
关联与零 control）。矩阵 MAP-013/MAP-031 本身只要求"失效置 `STALE`、`+1`、group 重算、失效先于保持"，
该改写不触碰 companion。

## 4. 独立复跑证据

| 命令 | 结果 |
|---|---|
| 专项 11 文件（卡 §验收等级阶段 A 命令）`test-results/p3-08c-fix-focused.xml` | **109 / 0 / 0 / 0**（阶段 A 为 108，本轮 +1 新用例） |
| 完整 `tests/unit tests/integration` `test-results/p3-08c-fix-full.xml` | **1451 / 0 / 0 / 48**（227.3 s；48 skip 全为未装 pinned 权重的环境跳过） |
| `uv run --no-sync ruff check .` | `All checks passed!` |
| `git diff --check` | clean（exit 0） |
| `uv lock --check` | `Resolved 95 packages`（依赖未改） |
| 真实 HTTP 闭包探针（§3.1/§3.2） | 6/6 拒绝全部 `companion MappingApiError -> VALID` |
| 信封范围探针（§3.2） | 5 个端点：4 个逐字节不变 + 1 个按冻结形状补空对象 |

Windows 环境事实沿用：`.pytest-tmp` 对受限令牌仍为 `PermissionError [WinError 5]`，本轮改用项目内全新
basetemp（`.pytest-basetemp-p308c/fix-*`，被 `.gitignore` 忽略）并经正常 Windows 权限路由执行；未改 ACL。

## 5. 未验证项与需要 Sol 裁定的点

1. **`closure_details` 是新增的显式机制**（映射面之外的端点默认行为逐字节不变，§3.2 有实测），但它是
   本轮为满足 §8.2 而引入的；请 Sol 判定该机制是否属于"最小且不放宽契约"的收口，或应改为其它等价做法。
2. **已决策 group 不复活**是本轮按 §7 做的工程裁定（`REJECTED`/`CONFIRMED` 的 group 即使候选重新出现也
   保持其决定与 `NO_CANDIDATE` 的审核视图）；ADR 未逐字规定该分支，另无最小反例指向相反行为。
3. 阶段 A §8 的未验证项裁定全部沿用，本轮未扩大：`BROW-002` 的 `/search` 人物条件输入仍属 P3-08D
   候选（不在本卡夹带）；30 万容量/多进程/真实权重升级回退/`0014` 实测耗时/C 级打包未执行、未标 PASS。
4. 本轮未跑真实浏览器闭环（阶段 A 与 P3-08B 的记录式 smoke 仍有效；本轮只改错误面与一条
   `STALE → SUGGESTED` 转换，前端未改）。

## 6. 压缩交接包（阶段 B / Sol 只读这些）

- **基线**：`c7fa68b` + P3-08B 全部未提交改动；本轮改动文件见 §2（生产 6 个、测试 2 个）。
- **要复核的三条边界**：
  1. 真实 HTTP 的逐码闭集：`?limit=999` / `?limit=many` / `?state=decided` /
     `GET /face-mappings/<bad id>` / `?cursor=<pf1:...>` / 写请求的 `FACE_MAPPING_STALE` 与
     `FACE_NOT_FOUND` —— 用 `tests/unit/test_p3_face_redetection_api_privacy.py::
     test_mapping_endpoints_are_closed_over_http`（内联 companion 校验器）即可复现，命令见 §4。
  2. 保持/释放/队列与 §10 顺序修复未被波及：`tests/integration/test_p3_face_redetection_hold.py`、
     `tests/integration/test_p3_face_redetection_decisions.py`、
     `tests/integration/test_p3_person_undo_ordering.py`。
  3. `STALE → SUGGESTED` 复活及其 `OPEN` 边界：`tests/integration/test_p3_face_redetection_mapping.py`
     （`test_removed_candidate_becomes_stale`、`test_a_decided_group_is_not_reopened_by_a_reappearing_pair`）；
     最小反例探针 §3.3。
- **关键 diff 定位**：`persons/read.py::check_mapping_limit/mapping_page/mapping_group/
  decode_mapping_cursor`；`api/routes.py::list_face_mappings`；`persons/mapping.py::
  _upsert_mapping_row`（`STATE_STALE` 分支）；`api/errors.py::error_payload/error_response/ApiError`；
  `api/handlers.py::_api_error`。
- **禁止扩大**：不得为通过而放宽 ADR-0010 或两份 companion；不得改 migration `0001`～`0014`、依赖、
  `PhotoSearch.spec`、`web/**`；不得把未执行的浏览器/30 万/C 级步骤写成 PASS。

## 7. 停止点

本轮到此停止：**P3-08C 仍为未 PASS**，`docs/tasks/P3-08D.md` **未生成**。下一步为
**阶段 B — C2 / GPT-5.6 Sol / high 新会话**，输入为本报告 §6 的压缩交接包，只短审 §6 列出的三条边界；
通过后才把 P3-08C 标为 PASS、更新状态并生成 `docs/tasks/P3-08D.md`。模型切换由用户在 Codex 中手动完成。
