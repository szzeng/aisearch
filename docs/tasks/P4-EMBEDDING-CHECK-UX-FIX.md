# P4-EMBEDDING-CHECK-UX-FIX — 搜索模型「检查」无反馈最小修复

- **状态**：TODO / 待执行；创建日期：2026-10-20。
- **优先级**：P1（直接阻碍用户判断 Qwen 候选索引能否切换）。
- **建议执行模型**：GPT-6 Medium；若实际触及切换资格、安全门禁或模型身份契约，停止本卡并升级评审。
- **仓库**：`szzeng/aisearch` / `master`；执行前对照最新 HEAD、`AGENTS.md` 和 `docs/development-status.md`。
- **验收方式**：定向前端行为测试 + 必要的已有接口回归 + 语法检查；个人项目，不做全仓测试、30 万图压测或真实模型建库。
- **性质**：只修「检查」的用户反馈和状态一致性；**不实际安排或执行模型切换**。

## 1. 问题与代码依据

用户在「图库管理 → 搜索模型」点击 Qwen 的「检查」后，页面看起来没有变化，不知道检查是否开始、是否完成、为什么不能切换。

已核对当前实现：

1. `src/photo_search/web/app.js` 的 `renderProviders()` 将模型状态大量归并为「索引待检查」，并把「图库覆盖：待检查」写死；`GET /embedding-management/status` 的 `switch_eligible` 默认是 `false`，**不能**被当作上一次显式检查的结论。
2. 点击 `检查` 已真实调用 `POST /embedding-management/providers/{provider_id}/check`，保存到页面内存 `embeddingChecks[provider_id]`，然后重新渲染；保存结果目前主要只用于决定「安排切换」按钮是否出现。重绘会替换原按钮，旧节点上的 `disabled` 不构成稳定的执行中状态。
3. `src/photo_search/api/routes.py` 的显式检查已在线程内执行，返回 `status`、`switch_eligible`，以及按情况存在的 `coverage`、`lance`、`selection_error`、`profile_id` 或 `reason=NO_GENERATION`。**HTTP 200 不等于通过**。
4. 异常当前写到整块模型区的 `#active-search-model`，会覆盖“当前搜索模型”的说明，且不易分辨是哪张模型卡出错。
5. 检查可能有大量磁盘 I/O，当前 HTTP 接口不提供阶段进度；本卡只显示诚实的“请求进行中”，**禁止伪造百分比或虚构已完成阶段**。

## 2. 用户可见的最小行为

| 场景 | 模型卡片必须显示 | 按钮规则 |
| --- | --- | --- |
| 尚未显式检查 | 「索引待检查」；覆盖不明则「图库覆盖：待检查」 | 「检查」可点击；不因状态接口默认字段显示「可切换」 |
| 点击后请求未返回 | 立即显示「正在检查索引…（可能需要一些时间）」；同卡标记忙碌 | 变为不可重复触发的「检查中…」，不能产生多个相同检查请求 |
| 检查通过且具备资格 | 「检查通过，可以安排切换」；显示响应中的真实 `covered/required`，必要时显示百分比 | 若不是当前活动模型，出现「安排切换」 |
| 检查已完成但不具备资格 | 「检查完成，暂不能切换」；显示可理解的原因和实际缺口 | 保留「重新检查」，不显示「安排切换」 |
| `status=not_ready` / `NO_GENERATION` | 「尚无可检查的索引，请先建库」 | 不显示「安排切换」 |
| HTTP/网络检查失败 | 在**本模型卡片**显示「检查失败：…」，仍保留原活动模型说明 | 允许重试；不得把请求错误冒充“索引不完整” |
| 刷新页面、换入新候选 generation、建库重新开始/继续/完成、模型卡身份变化 | 清除不再可信的检查结论，恢复待检查 | 不沿用旧「可切换」按钮 |
| 已经是活动模型 | 保持「正在使用」身份；检查结论可独立显示 | 不显示无意义的「安排切换」 |

失败原因只做短文本映射：优先利用响应中的 `selection_error.code`、`selection_error.details.reason`（按真实结构读取）、`reason` 和 `coverage` 的 `missing/offline_assets/pending_outbox/pending_repair/stale_entries/unreadable_artifacts` 等字段；无法识别则显示安全的错误码与「请查看检查详情/重试」，**不得显示绝对本机路径或将原始对象直接拼为文字**。覆盖数只展示后端真实值；`required=0` 时不能算作 100% 或可切换。

## 3. 最小实施要求

1. **优先只改 `src/photo_search/web/app.js`**；确需布局时少量修改 `src/photo_search/web/styles.css`。复用现有模型卡与 `#active-search-model`，在各模型卡内部增加一个简短、可访问的状态行（例如 `role="status"` / `aria-live="polite"`）。不重做图库管理页。
2. 为每个 `provider_id` 保存少量**临时前端**状态：`idle/checking/passed/blocked/error`、实际检查响应、请求标识及对应的 `provider_id + processor_id + space_id + generation_id + profile_id` 快照。不要把显式检查结果回写到轻量状态接口，更不要伪造 `provider.switch_eligible`。
3. 点击后**同步更新视觉状态**，然后发出原有单个 `POST .../check`。在重新渲染/模型状态刷新过程中也须保留 `checking`，防止旧按钮 DOM 被替换后重新点击；同模型重复点击只保留一份有效请求。响应/异常结束后清理 busy，晚到结果必须核对请求序号及候选模型身份，不能覆盖更新后的 UI。
4. 检查完成后在当前卡展示响应内容，区分「HTTP 200 且 `switch_eligible=true`」「HTTP 200 但 `switch_eligible=false`」「`not_ready`」「请求报错」。以**显式检查结果**决定切换按钮，且检查响应的 `profile_id` 应与当前模型身份/有效 Profile 一致，不能仅凭缓存中的布尔值。
5. 建库任务启动、暂停后继续、任务终态变动、generation/profile 身份变化、重新进入模型页面及刷新页面时，按需使旧结果失效；不要求刷新后恢复历史检查结论。若图库在未可察觉的后台发生变化，页面最多说明「最近一次检查通过」，正式切换仍由已有后端重新核验，不宣称持续有效。
6. 保持原「安排切换 → 正常退出 → 重启生效」流程和确认对话框；不修改 `selection.py`、`offline_transition.py`、`activate_profile` 或事务/锁逻辑。只有发现现有 `/check` 确实缺少渲染所必需的安全字段时，才允许一个小型响应字段补充，并写明原因；禁止顺带重构完整检查算法或增加高频后台扫描。
7. **性能边界**：轻量 `GET .../status` 不自动触发深度检查；仅用户点击（以及现有安排切换前的服务端二次核验）发生深度 I/O。不新增轮询、数据库迁移、任务表、检查阶段 API 或额外模型/GPU 加载。

## 4. 最小验证

- 用现有隔离合成数据或 JS/Node DOM/fetch 夹具测试至少：点击即检查中；重复点击只发一个请求；成功展示真实覆盖与切换按钮；`200 + switch_eligible=false` 显示原因且无切换按钮；`NO_GENERATION`；网络/服务异常显示卡内错误并可重试；刷新/重绘/任务状态或模型身份变化使旧结果失效；活动模型不出现重复切换；旧响应晚到不污染新卡。
- 尽量复用 `tests/unit/test_embedding_management_status.py`、`tests/unit/test_p4_embedding_management_fix.py` 及已有前端行为夹具；测试必须覆盖实际 UI 状态变化，不能只用字符串存在断言代替交互验证。
- 至少运行 `node --check src/photo_search/web/app.js` 与涉及到的最小测试。仅修改前端时不要求跑整套 Python 测试/Ruff。可用时进行一次**隔离**浏览器短验收，不能为了本卡读取或更改真实 `D:\SZ\project\PhotoSearchData`。
- 不需 30 万图库压测、Qwen/CUDA 实机验收或全仓测试；不能将此前尚未完成的完整切换/索引正确性验收标为通过。

## 5. 完成标准与交付

用户点「检查」后立即知道**正在检查**；结束后知道**通过/未通过/请求失败**、真实覆盖数和下一步该怎么做；只有当前检查结果支持且身份匹配时才出现「安排切换」。检查失败/结果过期时不误导用户，也不增加平时页面 SQL/GPU 负担。

仅提交本修复所需代码、少量定向测试及简短报告。保护用户未提交改动；若需修改安全门禁/索引算法，先报告并将其另立任务，不借本卡扩大范围。
