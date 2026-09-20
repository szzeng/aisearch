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
