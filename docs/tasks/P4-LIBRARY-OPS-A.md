# P4-LIBRARY-OPS-A（V2）— 图库建库与同步入口统一

**状态**：READY / 文档方案，尚未实施。**日期**：2026-10-11。**参考仓库**：`szzeng/aisearch`，核对 `master @ 3b9d472f3ed6ae70a383d3c826c0c3eb48fab94c`；开工必须重核 HEAD、worktree、并行任务和 `docs/development-status.md`，不覆盖其他任务改动。

**优先级**：P1，先做。**推荐模型**：GPT-6 Luna / Medium；如果需要修改队列协调，用 Luna / High 定点自审。**测试**：只做直接相关小型合成 / JS 行为测试，禁止全量回归、30 万压测、真实模型和真实 `PhotoSearchData`。

## 一、目标与操作定义

把图库的日常动作统一为**两个命令 × 两个作用范围**，保留原有模型/扫描器语义。

| 范围 | 建库及历史补齐 | 日常文件变化 |
|---|---|---|
| 全部图库 | `继续全部建库` → `mode=build`、所有当前注册来源 | `同步全部变化` → `mode=refresh`、所有注册来源 |
| 单个图库 | `开始/继续建库`（按已知状态）或 `建库与补齐` → `mode=build, source_ids=[id]` | `同步变化` → `mode=refresh, source_ids=[id]` |

建库继续处理持久 backlog，包括语义、可用的增强阶段；同步变化针对新增/删除/移动/改写文件，不是全量重新做 AI；完整校验扫描仍是单独的高级诊断动作，不等同建库/增量同步。`SEARCH_READY` 不等于人物/OCR/地点全完成。

## 二、最新代码核对：必须修正上一版方案的边界

- `web/index.html`、`web/app.js` 的首页全局“刷新图库”仅触发 `refresh`；来源级可触发 `build`，另有“完整校验扫描”。
- `api/models.py::LibraryRefreshRequest.source_ids` 的 **`max_length=64`**：旧卡直接把所有图库 ID 放进数组，当来源超过 64 会 422。必须改为**全部来源使用 `source_ids=null`/不传**，不能截断前 64 个；单来源继续显式 ID。
- `api/refresher.py::LibraryRefresher.start` **正在运行时仅允许显式非空 ID 的 build 入进程内 FIFO**，当前全局 `build + source_ids=None` 会被拒绝。为真正实现全局续建，需**最小扩展现有队列对 all-scope 的支持**，而不是新造独立队列或给每个图库发 N 次请求。
- `refresh/pipeline.py` 已能按全部来源构造 `requested` 并执行 `build`，有持久任务状态、幂等复用及 `BUILD_ROUND_CAP`；不要重新写建库逻辑。
- `refresh/progress.py` 按来源给出较重的真实进度；`/sources/summary` 用于轻量首屏。不能为了每张卡片显示百分比再一次性并发拉取所有 `/sources/{id}`，造成 N+1 首屏退化。
- 已有 `P4-SOURCE-RESUME-A / P4-SOURCE-PROGRESS-A` 与 `P4-OCR-LIFECYCLE-A` 的状态工作，不重复修 OCR 版本退役或重建进度协议。

## 三、实施要求

### A1. 唯一入口、低成本首屏

1. 首页全局区显示 `继续全部建库` 和 `同步全部变化`；每张来源卡保留简洁可发现的 `建库/补齐` 与 `同步变化`（可使用“更多”收纳次要动作）。**不依赖取得全量来源详情才能点击单来源建库**。
2. 来源状态已可靠加载时：未建 → “开始建库”；确实剩余任务 → “继续建库”；已建 → 同步为主要动作，“检查并补齐建库”仍可从更多操作手动发起。状态未加载显示中性“建库/补齐”，不能猜测“全部完成”。
3. 把“完整校验扫描”“删除来源”“技术明细”放到“更多/高级管理”。保留删除来源确认、原文件不会被删除的说明。原有“查看图库照片”、添加来源、改名、存储维护不丢失。不要另建一个重复的维护导航。
4. 全局动作零来源时不可提交。来源列表是轻量获取；只在用户展开时加载单来源复杂进度。改动已有缓存版本标识，窄屏按钮、键盘可达性与 aria 状态保持可用。

### A2. 全部来源 build 的最小队列修复（本卡唯一允许的后端改动）

1. UI 全局 build 发送 `POST /api/v1/library/refresh`，body `{ "mode": "build" }`（不传 `source_ids`，即全局模式）；全局 refresh 用 `{ "mode": "refresh" }`。来源级两个动作使用 `[source_id]`。**不得组装超过 64 ID 的数组**。
2. `LibraryRefresher.start` 允许 `mode=build + source_ids=None` 在其他任务运行时作为**一个 all-scope 请求**排入现有单 worker FIFO。队列内部用明确的 all-scope 标记 `None`，不得用空数组假扮某来源集合。队列持有范围而非 10 万个资产 ID。执行时从 catalog 获取当时已注册来源；在 UI 解释“全部=执行开始时登记的所有图库”。
3. `pending_builds` 现为 `list[list[int]]` 且用于单来源 UI；保持旧字段兼容。**增加一个可选 `pending_all_build: bool` 等价状态**或其它清晰且向后兼容的全局排队表示，不把 all-scope 编码成 `[]` 让前端误认为零来源。
4. 重复点击同一个全局请求必须去重；已有全局 build 正运行时再点全局不入队；全局 build 排队时重复点击不增加第二个。**单来源与全局范围交叠时可以有限重复目录枚举，但不能重复写入同 revision 的 AI 结果**；禁止为追求零重叠引入复杂全局计划器。
5. 若运行中的任务是 `refresh`，全局 build 应排队；`refresh` 自身延续现有“不入队”规则，忙碌时前端说“有任务执行中，请结束后重试”，不可把 `accepted=false` 说成启动成功。
6. `mode=build, source_ids=None` 空库不应触发虚假的“成功建库”或意外全资产处理；前端拦截零来源、后端对竞态空库安全地跳过。关闭应用仍只丢失**未开始**的进程内请求，不丢持久的扫描/识别成果；不做自动开机续跑。

### A3. 状态文案必须诚实

- 区分 `正在扫描/索引/增强`、`已排队`、`搜索可用但增强未完成`、`本轮完成`、`部分失败`、`来源离线/模型不可用`；有 backlog 时保留继续入口。
- 把按来源持久进度、**进程内**任务排队状态、**本轮临时**刷新统计分开；应用重启后不能把已消失的队列仍画为“已排队”。
- `BUILD_ROUND_CAP` 有剩余任务时不是“完全建库完成”，用户可重复点击继续。不要修改状态枚举以强制显示圆满完成。

## 四、最小可运行验收（4 组）

1. **基础按钮/作用域**：合成 3 来源，全部 build 请求体不含 `source_ids`，单独 build/refresh 分别带单 ID，全局 refresh 无 ID，完整校验扫描保持原入口；0 来源不能提交。
2. **队列行为**：A 正在 build → 点击全部 build → 排入一个 all-scope → 重复点击去重 → A 结束 → 全部建库串行启动；A 正在 refresh → 全部 build 仍可排队；关停丢排队状态但保留持久进度。
3. **大于64来源**：用 65 个轻量假来源验证全局请求仍被接受，不产生超过64 ID 的 API body，不漏来源；不需要 65 个真实图片目录、真实模型或大规模识别。
4. **进度/失败**：已建 A、未完成 B、离线 C；不重复生成 A 已确认的语义向量，B backlog 推进；离线/失败不能误显示“全部完成”；UI 不引入 N+1 明细请求。Node + pytest 定向模拟即可。

执行 `node --check`、变动 Python 的 Ruff/`py_compile`、定向 `pytest -q --junitxml=...`、`git diff --check`。优先复用 `tests/unit/test_api_refresher_queue.py`、`tests/unit/test_api_frontend.py`、`tests/integration/test_p4_02y_i_source_build.py` 的小样例，不跑整文件巨大基线也可以。

## 五、严格不做 / 交付

不改扫描完成事务、SHA-256 身份、识别模型、LanceDB 索引、schema、原图，不做 USN、并行 GPU 或持久队列，不重构人物/地点、OCR 退役。只改触及入口和队列所需的 `web`、`api/refresher.py`、必要的 `api/models.py` 与小测试。记录 HEAD、diff、JUnit 数字、用户手测步骤与未测项，并更新 `docs/development-status.md`；如已有其他任务在同文件施工先核对冲突，不能覆盖。