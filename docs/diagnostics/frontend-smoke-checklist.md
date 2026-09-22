# 前端浏览器 smoke 核对清单（P1-05 / P1-05B / P2-03C）

这份清单把 P1-05 交付的前端闭环固化成可重复执行的人工核对流程。它记录 P1-05 报告 §6 实际观察过的
10 个步骤、每步的期望观察、以及「不满足即不得标 PASS」的判据，避免每次都要重新设计夹具与步骤。

适用范围：`src/photo_search/web/**` 服务的本机页面。§3-§5 是 `index.html`（来源页 / 按加入时间
浏览 / 详情 / 离线提示）；§6 是 P2-03C 新增的 `places.html`（地点整理）。
**所有夹具都是 `%TEMP%` 下的合成图片**；任何一步都不得读取、修改、移动或删除真实照片库。

## 0. 为什么不自动化

本仓库**不引入 Node/npm/打包链，也不引入无头浏览器依赖**（`pyproject.toml` 只有 Python 依赖，
仓库内不存在 `package.json`）。因此：

- 可自动化的部分是「静态契约」：`tests/unit/test_api_frontend.py` 断言外壳/脚本/样式无外部引用、
  无令牌落存储、只经 `/api/v1` 取数、关键文案与路径守卫存在等。
- 浏览器内的交互闭环（会话跳转、扫描进度、翻页、离线提示、窄视口、键盘可达）**只能人工核对**，
  本清单就是它的可重复版本。
- 若要引入真正的 DOM 自动化，必须先有单独的 ADR 与依赖决策；在此之前，任何「前端已自动化测试」的
  说法都是不准确的。

已知限制（必须与清单一起阅读）：

- 窄视口 ≠ 操作系统级 200% 缩放。清单用窄视口 + 无横向滚动作为代理，真实 200% 缩放未验证。
- `order=time_desc` 只按**入库时间**排序；EXIF 拍摄时间维度（`taken_at`）需要 ADR-0002 §7 的加列迁移
  与 C2 决策，尚未实现。
- 页面里的产物状态是详情请求时刻的快照；失败 code 以图片请求的实时响应为准（两者可能相差一次请求）。

## 1. 生成夹具

```text
uv run python tools/diagnostics/frontend_smoke_fixture.py --photos 60 --bulk 2000 --corrupt
```

- `--photos 60`：60 个独立 JPEG（含 `子目录 中文/` 与 `nested/deep/` 子目录）。
- `--bulk 2000`：`bulk/` 下 2000 个独立 JPEG，让一次扫描慢到能观察到 `RUNNING` 进度。
  （默认 60 个文件时，扫描可能在浏览器第一次轮询前就 `SUCCEEDED`，看不到中间态。）
- `--corrupt`：1 字节的 `corrupt.jpg`，用于第 8 步。
- `--too-new`：额外生成 `schema_version=当前支持版本+1` 的数据目录（P2-02 起为 3），
  用于第 10 步（不要与正常数据目录混用）。该值由 `schema.SUPPORTED_SCHEMA_VERSION` 推导，
  新增迁移后自动跟随，不需要手工改这个脚本。
- `--offline bulk/b0007.jpg`（可重复）：删掉指定原图，用于第 6/7 步（无需重扫即可看到实时离线提示）。
- `--reset`：只删除该脚本自己创建的库/数据目录后重建；不触碰工作目录之外的任何路径。

脚本会打印来源根目录的绝对路径、启动命令与 10 步期望观察。

## 2. 启动真实端口的服务

```text
uv run python -m photo_search --port <free-port> --data-root "<脚本打印的数据目录>"
```

控制台会打印一次性会话链接（形如 `http://127.0.0.1:<port>/api/v1/session?next=/&token=…`）。
该链接是凭证的唯一来源，也是唯一允许凭证出现的地方（访问日志中应为 `token=<redacted>`）。

## 3. 核对步骤（逐条记录结果）

| # | 操作 | 期望观察 | 不满足即不得 PASS 的判据 |
|---|---|---|---|
| 1 | 打开控制台打印的一次性链接 | 303 落到 `/`；标题「本机图片搜索」；来源页显示「还没有登记来源…」引导文案 | 落到空白页 / 无限重试 / 标题或引导文案缺失 |
| 2 | 填显示名 + 粘贴来源根目录，点「注册来源」 | 201；卡片显示显示名、状态、资产/位置计数、最近成功扫描；**不显示任何绝对路径** | 列表里出现盘符路径，或注册失败无结构化错误 |
| 3 | 点该来源的「扫描」 | 先「扫描已排队（scan #…，job #…）」，随后同一行出现 `RUNNING` + 递增进度，最终 `SUCCEEDED … 100%`，来源计数刷新 | 无进度反馈；进度倒退；终态不是 SUCCEEDED 且无结构化原因 |
| 4 | 切「按加入时间浏览」 | 首批 50 张、文案「按加入时间从新到旧」；缩略图按需出现（未滚动到的图块暂无 `img`）；点「加载更多」变 100 | 一次性加载全部；文案暗示拍摄时间；翻页后重复或缺失 |
| 5 | 点首图「查看详情」 | 预览渲染；身份与时间含「加入时间…（入库时间，不是拍摄时间）」；全部位置含来源显示名 + 相对路径 + 可用性 + 大小 + mtime；产物状态与 code；「打开原图」指向 `/api/v1/assets/{id}/original` | 出现绝对路径；缺少位置/产物状态；「打开原图」指向任意路径接口 |
| 6 | 用 `--offline` 删掉的某个原图，在图块上点「刷新」（**不重扫**） | 该图块**仍显示缩略图**并标注「原文件不可用」 | 破图 / 空白占位 / 无任何提示；或缩略图消失 |
| 7 | 打开该资产详情并点「打开原图」 | 新标签返回结构化 JSON：`{"code":"SOURCE_FILE_MISSING",…}` | 返回空白、HTML 错误页或未脱敏的绝对路径 |
| 8 | 打开 `corrupt.jpg` 详情 | 可见 `DECODE_FAILED` 文本，无破图；产物状态给出 code（含路径时显示「错误详情包含本机路径，已省略。」） | 出现破图；出现绝对路径（含双反斜杠形态）；无 code |
| 9 | 把窗口缩到约 640px 宽；再用 Tab/Enter 操作 | 无横向滚动（`documentElement.scrollWidth <= innerWidth`）；网格重排为多列；Tab 依次可达 `select`/按钮，Enter 可打开详情；图片有 `alt` | 出现横向滚动条；控件无法用键盘到达或激活；`img` 无 `alt` |
| 10 | 用 `--too-new` 的数据目录启动并打开页面 | 页面显示 `SCHEMA_TOO_NEW` 结构化提示（含 request_id），来源页显示结构化错误而**不是**空列表 | 显示空图库误导用户；或页面崩溃、无提示 |

## 4. 必做的 DOM 断言（可复制）

在页面控制台执行（或在自动化里用等价读取）：

```js
// 正文里不得出现盘符路径或用户目录形态（双反斜杠与单反斜杠都算）
/[A-Za-z]:\\|\/Users\//.test(document.body.innerText);

// 窄视口下不得横向溢出
document.documentElement.scrollWidth <= window.innerWidth;

// 页面不把令牌写进浏览器存储
Object.keys(localStorage).length + Object.keys(sessionStorage).length === 0;
```

再看一次网络面板：所有请求都在 `/api/v1` 下；`/api/v1/session` 只出现一次凭证，且访问日志里是
`token=<redacted>`。

## 5. 结果记录

每一步都要在任务报告里记「步骤 / 实际观察 / 通过或失败 / 失败原因」。任何一步失败：

- **不要**在本清单的框架内顺手改实现；把失败当作缺陷单独报告（前端/浏览器兼容问题推荐
  C1 / GPT-5.6 Terra / medium）。
- 该步标 `VALIDATION PENDING` 或 `FAIL`，不得因为其它步骤通过就整体标 PASS。

参考实现：P1-05 报告 §6（10 步的实际观察）与 §8（未验证项）。

## 6. 地点整理页（P2-03C）

页面：`/places`（`src/photo_search/web/places.html` + `places.js` + `places.css`），由
`src/photo_search/api/web.py` 从 package data 提供。它与 `/api/v1/places` **不是同一个路径**：
文档是公开的，地点记录仍只来自会话保护的 `/api/v1`。

同样不引入 Node/npm 与无头浏览器：可自动化的部分是 `tests/unit/test_api_frontend.py` 的静态契约
断言（无外链、无凭证落存储、无 `innerHTML`、只经 `/api/v1` 取数、冻结词表与错误码文案）与
`tests/integration/test_p2_place_page.py`（页面发出的每个请求形状）；浏览器内交互只能人工核对。
本节的 10 步就是那一遍人工核对。

### 6.1 生成夹具

```text
uv run --no-sync python tools/diagnostics/places_smoke_fixture.py --reset
```

- 生成 14 张合成 JPEG：12 张杭州附近（有候选）、1 张东海海上（成功空结果）、1 张无 GPS。
- 在同一数据根里完成真实扫描、安装并激活 `tools/places/examples/mini-cities.spec.json` 构建的
  合成地名包、跑真实 GPS 与「附近城市」处理器，并预置目录：甲市/乙市，两市下各一个「同名地点」，
  再加一个自定义地点「我的标记」。
- 打印启动命令、来源根目录、活动包目录（第 8 步要删的文件）与 10 步期望观察。
- 只写自己工作目录内的合成文件，不发网络请求，不读真实图库/真实地名包；`--reset` 只删除它自己
  创建的目录。

### 6.2 启动真实端口的服务

```text
uv run --no-sync python -m photo_search --port <free-port> --data-root "<脚本打印的数据目录>"
```

打开控制台的一次性会话链接（`next=/`），再用导航里的「地点整理」进入 `/places`。

### 6.3 核对步骤（逐条记录结果）

| # | 操作 | 期望观察 | 不满足即不得 PASS 的判据 |
|---|---|---|---|
| 1 | 打开 `/places`（先走一次性链接） | 标题「地点整理」；地名包面板显示真实状态（本夹具为 `active`）；无外链、无本机路径 | 出现 `http(s)://` 外链 / 盘符路径 / 未建立会话的 401 循环 |
| 2 | 新建 `city` 与 `custom` 各一个；再用「编辑此地点」改名 | 创建后目录出现新行；改名成功且 revision +1；**重放同一旧 `expected_revision`** → 提示 `PLACE_ASSIGNMENT_CONFLICT` 与 `current_revision`，要求重新载入 | 改名后 revision 不前进；冲突时静默覆盖或只显示无信息的失败 |
| 3 | 名称填「同名地点」→ 查询目录 | 两条同名记录各自显示祖先链（`甲市 › 同名地点 #id` / `乙市 › …`）与稳定 `#place_id`，无自动合并；「只看它的后代」把 `ancestor` 过滤填入并只列后代 | 两条同名被合并/覆盖；无祖先链；祖先链显示猜测出的名称而不是 `#id` |
| 4 | 建议面板「加载 / 重新加载」→ 展开一条「查看候选与当前归属」→「确认第一条建议」 | 列表显示 12 条待确认建议；候选行标注 `SUGGESTION_ONLY`、距离与依据；确认后该资产离开列表，展开里 `mode=MANUAL_ASSIGNED`、`origin=CONFIRMED_SUGGESTION`、`manual_lock=是` | 候选被展示成已归类；确认后资产仍在建议列表；origin 不是 `CONFIRMED_SUGGESTION` |
| 5 | 「全选本页」或「冻结全部结果快照」→ 搜索地点→「选为目标 place_id」→「批量确认归属」 | 「逐批 / 单条结果」逐条给出批次、operation、`changed`、`replayed`；快照把 N 个 `asset_id` 冻结（提示含「asset_id 快照」）；把目标 place_id 清空再点（用键盘清空输入框）→ 前端拒绝且不发请求 | 提交时重跑动态查询；只显示总数不显示逐批结果；失败批被显示成部分成功 |
| 6 | 每页条数选 10 → 加载 → 另一标签页确认一条建议 → 回本页点「冻结全部结果快照」（或「加载更多」） | 提示「建议集合已变化，需重新加载」+ `PLACE_SUGGESTION_REVISION_STALE`，列表**不**拼接两版候选 | 静默追加第二页；或把过期候选当当前候选展示 |
| 7 | 操作日志（升序，最新在最下方）→ 对一条 COMMITTED 操作「撤销此操作」；再对同一条（现已 REVERSED）撤销一次 | 第一次成功并在「撤销结果」显示新 `UNDO` 操作与 `undo_of_operation_id`；第二次显示 `PLACE_OPERATION_NOT_UNDOABLE` 并刷新目录/建议 | 撤销删除了日志或降低了 revision；被拒时无说明 |
| 8 | 删掉脚本打印的 `places.jsonl` →「刷新地名包状态」 | 状态变为 `missing` + `PLACE_DATASET_MISSING`；目录查询、新建/改名、人工归属、筛选照常；既有机器建议仍可读（只有新解析阻塞），不报错也不伪造 `ACTIVE` | 显示伪 `ACTIVE`；缺包导致目录或人工归属不可用；把缺包伪装成空图库 |
| 9 | 窄视口约 640×900 + Tab/Enter 走完第 2、4、7 步 | 无横向滚动（所有元素 `scrollWidth <= clientWidth`，网格按 `min(100%, …)` 收成单列）；按 Tab 依次可达输入框/按钮/复选框，Enter 可提交 | 出现横向滚动条；控件无法用键盘到达 |
| 10 | 观察页面文本与服务端访问日志 | 页面与响应中没有盘符/UNC 路径，也没有精确坐标；坐标只在 `/api/v1/assets/{id}/geo`；访问日志中所有请求都在 `/api/v1/*` 或 `/web/*`（页面路由 `/`、`/places`），`geo-hits = 0` | 出现绝对路径；页面请求了 `/geo` 或渲染了坐标 |

### 6.4 必做的 DOM 断言（可复制，与 §4 相同）

```js
// 正文里不得出现盘符路径或用户目录形态（双反斜杠与单反斜杠都算）
/[A-Za-z]:\\|\/Users\//.test(document.body.innerText);

// 窄视口下不得横向溢出
document.documentElement.scrollWidth <= window.innerWidth;

// 页面不把令牌写进浏览器存储
Object.keys(localStorage).length + Object.keys(sessionStorage).length === 0;

// 地点页不得请求坐标端点：内置浏览器的只读求值作用域没有 performance/console，
// 因此这一步用服务端访问日志核对（本仓库 P2-03C 报告 §6 的实际做法）：
//   Select-String -Path <server.out.log> -Pattern 'HTTP/1.1'  → 所有路径都在 /api/v1/* 或 /web/*，且不含 /geo
```

已知限制（必须与本节一起阅读）：

- 批量确认走 `/api/v1/place-assignments/batch`，而该请求体**没有** `origin` 字段（ADR-0005 §6.2
  只冻结了资产、动作与 revision），因此批量确认记录 `origin=MANUAL`；只有单条「确认第一条建议」与
  勾选 1 条时的语义是 `CONFIRMED_SUGGESTION`。页面把这一点写在批量面板里，不夸大 provenance。
- 页面不触发后台解析：候选只能来自已存在的机器产物（本夹具由脚本用真实处理器预置）。
- `GET /assets` 与建议列表都不是快照隔离读，只绑定 `suggestion_revision`（P2-03A §10.2 / P2-03B §10.7）。
- 坏包时既有机器建议仍可读，只有新的解析阻塞（P2-03B §10.6）。
- 第 9 步的 640×900 视口：本次内置浏览器的 `viewport` 覆盖没有改变实际窗口（`innerWidth` 仍为
  1280），所以 640px 渲染本身没有被直接截图。可复现的代证据是「513 个元素中 0 个存在内部横向溢出
  （`scrollWidth <= clientWidth`）」加上 `minmax(min(100%, 24rem), 1fr)` 的网格与 `max-width: 100%`
  的输入控件；需要真实 640px 像素证据时应换用可设视口的浏览器或 C1 复核。

## 7. 人物整理页（P3-06）

页面：`/persons`（`src/photo_search/web/persons.html` + `persons.js` + `persons.css`），由
`src/photo_search/api/web.py` 从 package data 提供。它与 `/api/v1/persons`、`/api/v1/faces`、
`/api/v1/person-operations` **不是同一个路径**：文档是公开的，人物/脸/操作记录仍只来自会话保护的
`/api/v1`。可自动化的部分仍是静态契约（`tests/unit/test_api_frontend.py` 的 P3-06 段）与页面发出的
每个请求形状（`tests/integration/test_p3_persons_page.py`、`test_p3_persons_read_api.py`、
`test_p3_face_thumbnail.py`），浏览器内交互只能人工核对，本节就是那一遍人工核对。

### 7.1 生成夹具

```text
uv run --no-sync python tools/diagnostics/persons_page_smoke_fixture.py --reset
```

- 在 `%TEMP%\PhotoSearchPersonsSmoke` 下生成 17 张合成 JPEG（中文/空格目录名）、真实扫描，然后
  **直接 seed** 23 张权威脸行、15 个人物（2 个已命名、13 个未命名分组）与 1 张
  `MANUAL_UNASSIGNED` 脸（与冻结的 unassign 写入形态一致：无有效 link + 人工锁 + 活动排除）。
- 不下载任何模型：本机没有 buffalo_l 权重，页面需要一个**已存在**的人物目录，因此夹具 seed 权威事实
  而不是跑检测；真实检测链路仍由 P3-02/P3-03 的报告覆盖。
- 只写自己 `%TEMP%` 目录内的合成文件；`--reset` 只删除它自己创建的目录。

### 7.2 启动真实端口的服务

```text
$env:PHOTO_SEARCH_DATA_ROOT = "<脚本打印的 data_root>"   # 中文/空格路径直接用环境变量传，避免参数编码
.\.venv\Scripts\python.exe -m photo_search --port <free-port>
```

控制台打印一次性会话链接（`?next=/&token=…`）。这是凭证唯一允许出现的地方：访问日志里记为
`token=<redacted>`。

### 7.3 核对步骤（逐条记录结果）

| # | 操作 | 期望观察 | 不满足即不得 PASS 的判据 |
|---|---|---|---|
| 1 | 打开一次性链接（`next=/persons`） | 303 落到 `/persons`；标题「人物整理」；`app-meta` 报 `schema v13（ok）`；导航含返回/搜索/地点 | 落到空白页 / 无限重试 / 401 循环 |
| 2 | 「已命名人物」视图 | 人物卡显示真实裁剪头像、`#id`、revision 与「有效成员：脸 N · 照片 N」 | 出现盘符路径 / 破图 / 计数与 `#id` 详情不一致 |
| 3 | 「未命名分组」视图 | 未命名人物显示「暂无头像」占位而不是破图 | 无空态说明；把无头像当错误 |
| 4 | 每页条数改 10 → 点「加载更多（沿用当前游标）」 | 第一页 10 条「还有更多」，第二页 3 条「已到末尾」，合计行数正确且 `#id` 不重复 | 翻页重复/漏行；游标被当成新查询 |
| 5 | 「待手动归类」视图 | 只列 `MANUAL_UNASSIGNED` 脸，显示「人工取消归类（带锁）」「有效归属 无」 | 列出普通成员脸；把建议当成已归属 |
| 6 | 点某人的「载入此人的脸」 | 逐张脸显示 face_id 前缀、模式、控制 revision、所属 asset 与有效归属 | 勾选粒度粗于 face_id；同图其它脸被自动带入 |
| 7 | 删掉夹具里某张原图后再点「载入此人的脸」 | 该脸显示「脸图不可用」占位；服务端返回 409 结构化错误；页面不出现任何路径 | 破图 / 空白 / 显示绝对路径 |
| 8 | 「保存名字 / 同名合并」用**过期 revision** 提交 | 页面显示服务器 `[PERSON_REVISION_CONFLICT]`（含 request_id）并刷新，不静默覆盖 | 静默写入；失败被显示成成功 |
| 9 | 用一个已属于别人物的名字保存 | 服务器执行同名合并，页面显示「#old 已同名合并到 #new」，列表刷新后旧 id 不再单独出现 | 前端自行判断同名；合并结果不显示 canonical |
| 10 | 「加载 / 刷新操作日志」→「撤销此操作」 | 日志含类型/状态/revision/时间/影响项数；撤销后给出新操作并刷新日志（原操作变「已撤销」） | 撤销删除日志或回退 revision；失败无说明 |
| 11 | 缩到 640 px 与 481 px，再放到 1280 px | 三个宽度下 `documentElement.scrollWidth <= clientWidth`，卡片从多列收成单列 | 出现横向滚动条 |
| 12 | 用 Tab/Enter 走一遍表单与按钮 | 焦点依次可达 skip 链接、导航、按钮与输入框；每个输入控件有 `label[for]`，每张图有 `alt` | 控件无法用键盘到达；无 `alt` |
| 13 | 页面内 DOM 断言（见 §7.4） | 正文无盘符/UNC/用户目录；无 `http(s)://` 外链；`img src` 全是相对 `/api/v1/faces/{id}/thumbnail` | 出现绝对路径；出现外链；`img` 指向非受控 URL |
| 14 | 服务端访问日志 | 全部请求在 `/api/v1/*`、`/persons`、`/`；凭证记成 `token=<redacted>`；`/geo` 命中 0 次 | 页面请求 `/geo` 或任何非 `/api/v1` 数据路径 |

### 7.4 必做的 DOM 断言（可复制）

```js
// 正文里不得出现盘符路径或用户目录形态（双反斜杠与单反斜杠都算）
/[A-Za-z]:\\|\\\\\?\\|\/Users\//.test(document.body.innerText);   // 期望 false

// 窄视口下不得横向溢出（clientWidth 已扣掉滚动条）
document.documentElement.scrollWidth <= document.documentElement.clientWidth;   // 期望 true

// 每个控件都有 label，每张图都有 alt，且图片只指向受控 id
Array.from(document.querySelectorAll("input, select, textarea")).every((c) => !!document.querySelector('label[for="' + c.id + '"]'));
Array.from(document.querySelectorAll("img")).map((i) => i.getAttribute("src"));

// 页面不请求坐标端点：内置只读求值作用域没有 performance/console/fetch，
// 因此该条用服务端访问日志核对（P3-06 报告 §5 第 14 步）：
//   Select-String -Path <server.out.log> -Pattern 'HTTP/1.1'  → 全在 /api/v1/* 或 /persons、/，且不含 /geo
```

### 7.5 已知限制

- 本机没有 buffalo_l/PP-OCRv4 权重，夹具直接 seed 权威人物/脸事实；**页面与只读接口不依赖模型**，
  但「真实检测→分组→页面显示」的整链不在本次观察范围内（沿用 P3-02/P3-03 记录）。
- 导航入口默认 `hidden`，由 `/capabilities` 的 `schema_version >= 13 && schema_status == "ok"` 决定
  是否显示。本次只观察到「schema 13 → 显示」一侧；旧库保持隐藏只有静态断言。
- 内置浏览器的只读求值作用域没有 `localStorage`/`sessionStorage`，运行时读取不可用；该条按「脚本
  静态契约 + 无写入代码」判定。
- 窄视口用浏览器 `viewport` 覆盖得到（本次 640/481/1280 三个宽度都实测通过），不是操作系统级
  200% 缩放。

## 8. 搜索页的人物筛选（P3-08D）

页面：`/search`（`src/photo_search/web/search.html` + `search.js` + `search.css`）。人物条件复用
ADR-0009 已冻结的 `person_ids`/`person_match`，选择来自会话保护的 `GET /api/v1/persons?kind=all`
分页读；本页不读数据库、不猜内部 id、不按名字合并。可自动化的部分仍是静态契约
（`tests/unit/test_api_frontend.py` 的 P3-08D 段）与 API 级等价证据
（`tests/integration/test_p3_person_search_filter.py`、`test_p3_person_place_interleave.py`），
浏览器内交互只能人工核对，本节就是那一遍人工核对。

### 8.1 生成夹具

```text
uv run --no-sync python tools/diagnostics/search_person_smoke_fixture.py --reset
```

- 先调用 `search_smoke_fixture` 生成可搜索的合成图库（6 张 JPEG、1 个已激活 `SearchProfile`、
  1 个已人工归类的地点），再 seed 人物权威：`爸爸 #1`（2 张照片，含地点那 1 张）、`叔叔 #2`
  （与 `爸爸` 共享 1 张照片，因此 any/all 不同）、2 个**都显示「未命名人物」**的人（`#3`/`#4`）、
  以及若干未命名人物，共 30 个 active canonical 人物（> 一页 25 个，所以有第二页）。
- 脚本同时用生产 `search_request` 跑出权威期望集合并打印，浏览器观察要与它一致：
  `dad=[1,6]`、`any(A,B)=[1,6]`、`all(A,B)=[1]`、`dad+place#1=[6]`、无人物条件 `=[1..6]`。
- 不下载模型、不读真实图库；`--reset` 只删除脚本自己的目录。人物权威本身拒绝两个
  `name_key` 相同的 active canonical 人物（P3-03 同名合并），因此「同一显示标签」的现实形态是
  未命名占位，而不是重名。

### 8.2 启动真实端口的服务

```text
$env:PHOTO_SEARCH_DATA_ROOT = "<脚本打印的 data_root>"   # 中文/空格路径用环境变量传，避免参数编码
.\.venv\Scripts\python.exe -m photo_search --port <free-port>
```

打开控制台打印的一次性链接（`?next=/search&token=…`）。这是凭证唯一允许出现的地方：访问日志里记为
`token=<redacted>`。

> 受限令牌下服务端可能无法在系统 `%TEMP%` 建上传暂存目录（`stage_upload` 报 `PermissionError`）；
> 这是环境限制。按仓库既有做法改用项目内临时目录（设置 `TEMP`/`TMP`）或经正常 Windows 权限路由启动，
> 不要把该 500 当产品结论。

### 8.3 核对步骤（逐条记录结果）

| # | 操作 | 期望观察 | 不满足即不得 PASS 的判据 |
|---|---|---|---|
| 1 | 打开 `/search` | 人物面板出现；标题「人物筛选（只作用于普通搜索）」；状态行「本页 25 个人物（active canonical） · 还有更多」；每个选项显示受控名称占位 + `#person_id` + 照片/脸计数 | 面板缺失；一次性物化全部人物到 DOM；选项只有名字没有 `#id` |
| 2 | 未选人时看「人物匹配方式」 | `全部（all）` 是 disabled，`清空人物选择` 是 disabled；提交只发 `person_ids` 缺失的请求 | `all + []` 可被选/可被提交 |
| 3 | 勾 `爸爸 #1` → 搜索 | 后端回显 `person_ids=[1]、person_match=any`；结果为资产 `#1`、`#6` | 回显缺失或在页面本地过滤出结果 |
| 4 | 再勾 `叔叔 #2` → 选 `全部（all）` → 搜索 | 回显 `person_ids=[1, 2]、person_match=all`；结果只剩资产 `#1`（`all` 是 AND 收窄） | `all` 被降级成 `any`；顺序/去重不稳定 |
| 5 | 回到 `任一（any）`，地点 `place_id=1` → 搜索 | 结果只剩资产 `#6`（人物 × 地点 AND，与 §8.1 期望一致） | 两组条件互相覆盖或本地二次过滤 |
| 6 | 每页数量改 1 → 搜索；在第二个标签页把 `#1` 改名并保存 | 第一页给出「候选共 2 个 · 还有下一页」；改名返回新 revision 且 `changed=true` | 翻页无游标；改名被静默忽略 |
| 7 | 回搜索页点「加载下一页」 | 结构化 `[CATALOG_FILTER_REVISION_STALE]` + 「请重新搜索」；人物/地点条件与上一轮回显**保留** | 静默拼接两版结果；条件被清空；降级成空网格 |
| 8 | 点「重新载入人物」→「按当前条件重搜」 | 选项标签更新为改名后的人名，已选 `#1` 仍在；重搜成功并给出当前 canonical 回显 | 选择在重绘后丢失；回显用的是旧 id 或本地猜测 |
| 9 | 清空人物选择 → 搜索 | 回显 `person_ids=[]、person_match=any`，结果回到无人物条件（本夹具 6 行） | 清空后仍发送人物条件/仍按人物收窄 |
| 10 | 键盘：`Tab` 进人物匹配方式与复选框，`Space` 勾选，`Enter` 提交 | 焦点依次可达 `人物匹配方式 → 清空人物选择 → 重新载入人物 → 人物选项`；`Space` 只改被聚焦的那一项（`#3` 与 `#4` 不联动） | 控件不可键盘到达；同标签的两个人被一起勾选 |
| 11 | 选一个人物后「上传搜图」提交一张合成图 | 结构化 `RUNTIME_CAPABILITY_UNAVAILABLE / IMAGE_ENCODER_UNAVAILABLE`（或成功），**不是** 422「multipart 未定义字段」——证明上传请求没有带人物字段 | 返回 `VALIDATION_ERROR`/multipart 未知字段；页面把上传当成人物筛选生效 |
| 12 | 窄视口（约 640px 及以下）+ 页面内 DOM 断言（见 §8.4） | `documentElement.scrollWidth <= clientWidth`、无内部横向溢出的元素、人物面板收成单列 | 出现横向滚动条 |
| 13 | 观察页面文本、URL 与服务端访问日志 | 正文无盘符/UNC/用户目录；URL 只有 `/search`（无 `token=`、无 person id）；请求全在 `/api/v1/*`、`/search`、`/persons`、`/web/*`；凭证记成 `token=<redacted>`；`/geo` 命中 0 | 出现绝对路径；URL 带 token 或 person id；页面请求 `/geo` |

### 8.4 必做的 DOM 断言（可复制）

```js
// 正文里不得出现盘符路径或用户目录形态（双反斜杠与单反斜杠都算）
/[A-Za-z]:\\|\\\\\?\\|\/Users\//.test(document.body.innerText);   // 期望 false

// 窄视口下不得横向溢出
document.documentElement.scrollWidth <= document.documentElement.clientWidth;   // 期望 true

// URL 不得带凭证，也不得带人物 id
location.href === location.origin + "/search";                                   // 期望 true

// 每个选项都有显式 label，且文本里带稳定 #person_id
Array.from(document.querySelectorAll("#person-options input")).every((c) => !!document.querySelector('label[for="' + c.id + '"]'));
```

### 8.5 已知限制

- 内置浏览器的 `viewport` 覆盖在本机不改变实际窗口尺寸（P2-03C §6.3、P3-06 §7.5 的同一事实），
  因此窄宽证据是「真实内置浏览器视口（本次约 395 CSS px，窄于 640）+ 无横向溢出」，不是设备级模拟。
- 没有图像编码器时上传查询只能观察到结构化拒绝；「上传请求不带人物字段」由该拒绝码 +
  `tests/unit/test_api_frontend.py` 的静态断言 + `test_p2_search_image.py` 的未知字段 422 共同判定。
- 人物页的改名步骤复用 `/persons` 的「保存名字」表单（需要勾选「我知道同名时会执行合并」）。

## 9. 拍摄时间范围与 `capture_desc`（P4-00B）

页面：`/`（`index.html` + `app.js`）与 `/search`（`search.html` + `search.js`）。拍摄时间是
ADR-0011 冻结的新维度：清单只命中「拍摄时间」（EXIF 原始/数字化时间），范围左闭右开，
`capture_desc` 把没有拍摄时间的资产放到尾部并按 `asset_id` 倒序。可自动化的部分见
`tests/unit/test_p4_taken_time.py`、`tests/unit/test_api_frontend.py` 的 P4-00B 段与
`tests/integration/test_p4_taken_time_{api,extract,faults}.py`；本节记录必须人工看一眼的那一遍。

### 9.1 生成夹具

```text
uv run --no-sync python tools/diagnostics/taken_time_smoke_fixture.py --reset
```

- 先调用 `search_smoke_fixture` 生成可搜索的合成图库（8 张 JPEG、1 个已激活 `SearchProfile`、
  1 个已人工归类的地点），再把其中 4 张写成带 EXIF 时间的照片（`_write_jpeg` 被替换为写 EXIF 的
  变体），然后用生产 `taken_time` 服务抽取拍摄时间（`enqueue_targets` + `run_queue`）。
- 期望（脚本用生产 `asset_page`/`search_request` 打印，浏览器观察要与之逐条一致）：
  `capture_range_2022=[1,8]`、`capture_desc_order=[8,1,2,3,7,6,5,4]`、
  `capture_desc_tail=[7,6,5,4]`（文件时间尾桶，`asset_id` 倒序）、`file_time_assets=[3,4,5,6,7]`、
  `empty_range_assets=[]`、`search_hits_2022=[1,8]`；`#8` 有偏移 `+08:00`、`#1` 无偏移；
  `#3` 只有 EXIF 根时间（`label=file_time`）、`#4..#7` 只有文件时间。
- 想观察「尚未抽取/不可信」的诚实状态时，用 `--rules-version 99` 再跑一次同一工作目录（不加
  `--reset`）：抽取已经完成、行仍在库里，只是规则版本失配 → 任何时间条件都命中 0 行且状态为
  `PENDING`；再加 `--rules-version 1` 跑一次即可恢复。
- 不下载模型、不读真实图库；`--reset` 只删除脚本自己的目录。

### 9.2 启动真实端口的服务

```text
$env:PHOTO_SEARCH_DATA_ROOT = "<脚本打印的 data_root>"
.\.venv\Scripts\python.exe -m photo_search --port <free-port>
```

### 9.3 核对步骤（逐条记录结果）

| # | 操作 | 期望观察 | 不满足即不得 PASS 的判据 |
|---|---|---|---|
| 1 | `/` 选「按拍摄时间」+ 范围 `2022-01-01`~`2023-01-01`，翻页浏览 | 回显为规范化定宽串 `2022-01-01T00:00:00.000` / `2023-01-01T00:00:00.000`；结果恰好 `#1`、`#8`（起点包含、终点不含）；状态块显示「拍摄时间已抽取完成」且计数 8/8、其中 3 | 边界日多出/少掉一行；回显是原样输入或带时区；状态块缺失 |
| 2 | `/` 只选排序「按拍摄时间（无拍摄时间的排最后）」，不填范围 | 顺序为 `8,1,2,3,7,6,5,4`；尾部的 `#7..#4` 每张卡的拍摄时间行显示「文件时间」；`#3` 也显示「文件时间」 | 无拍摄时间的行混在中间；尾部行被显示成「拍摄时间」 |
| 3 | 打开 `#8` 与 `#1` 的详情 | `#8` 显示 `2022-07-05T18:31:07.000（+08:00）` 与相机 `SmokeMake SmokeModel`；`#1` 显示同一墙上时间但**没有**任何时区、也不显示 UTC；两者的排序键相同 | 无偏移的行显示 `+00:00`/UTC；把偏移算进排序键 |
| 4 | 范围改成 `2000-01-01`~`2001-01-01`（再按 §9.1 用 `--rules-version 99` 重建一次） | 第一次：空结果 + 「拍摄时间已抽取完成（…确实没有命中）」；第二次：空结果 + `state=PENDING` 的「拍摄时间尚未抽取…不代表没有这些照片」 | 两种空结果写法完全相同（把「还没抽取」伪装成「没有命中」） |
| 5 | 第二个标签页在 `/places` 把任意一张照片改到另一个地点；回到第一个标签页点「加载下一页」 | 结构化 `[CATALOG_FILTER_REVISION_STALE]` + 提示「请点“刷新”从第一页重新开始」；筛选条件保留 | 静默续页；条件被清空；降级成空网格 |
| 6 | `/search` 用范围 `2022-01-01`~`2023-01-01` 搜索；再用「上传搜图」提交一张合成图 | 普通搜索回显 `capture_from`/`capture_to_exclusive` 的规范化串，命中 `#1`、`#8`；上传请求**不带**任何 capture 字段（服务端 multipart 闭集里没有它们） | 上传请求带 capture 字段或返回「未知字段」是页面自己发的 |
| 7 | 键盘 `Tab`/`Space`/`Enter` 走一遍筛选与排序控件；缩窄窗口 | 全部可达可用；窄视口无横向溢出 | 控件不可键盘到达；出现横向滚动条 |
| 8 | 页面文本、URL、访问日志、DOM 扫描（见 §9.4） | 无 `taken_at_raw`、无 `2022:07:05 18:31:07`、无盘符路径、无坐标；请求全在 `/api/v1/*`、`/`、`/search`、`/places`、`/web/*` | 出现原始 EXIF 文本、绝对路径或坐标 |

### 9.4 必做的 DOM 断言（可复制）

```js
// 正文里不得出现原始 EXIF 时间文本、盘符路径或用户目录
/2022:07:05 18:31:07|[A-Za-z]:\\|\\\\\?\\|\/Users\//.test(document.body.innerText);   // 期望 false

// 「文件时间」必须真的出现在尾部卡片上（步骤 2）
Array.from(document.querySelectorAll("#asset-grid .tile-time"))
  .filter((node) => node.textContent.includes("文件时间")).length > 0;                 // 期望 true

// 无偏移的照片不得显示时区
(() => { const t = document.querySelectorAll("#asset-grid .tile-time");
         return !Array.from(t).some((n) => n.textContent.includes("+00:00")); })();   // 期望 true

// 窄视口下不得横向溢出
document.documentElement.scrollWidth <= document.documentElement.clientWidth;         // 期望 true

// URL 不得带凭证或筛选以外的内容
location.href.indexOf("token=") < 0;                                                  // 期望 true
```

### 9.5 已知限制

- 列表响应按 ADR-0011 §8.1 只带**条件性**的状态块，不带逐行时间投影；因此每张卡的
  「拍摄时间/文件时间」一行来自该资产**详情接口**的闭集对象（页面按可见卡片懒加载）。
  浏览器 smoke 因此同时覆盖列表状态块与详情对象两条路径。
- 规则版本失配时，列表的**筛选**为空、详情为 `null`（不可信的行不发布），但 `capture_desc`
  的**排序**仍按已存储的键给出确定性顺序（ADR-0011 §2 只排除「状态与过滤」；报告 §6 记录了该裁定）。
- 内置浏览器 `viewport` 覆盖不改变实际窗口尺寸，窄宽证据同上（§8.5）。
