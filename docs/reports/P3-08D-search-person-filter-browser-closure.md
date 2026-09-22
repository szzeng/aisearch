# P3-08D — 搜索页人物筛选与 BROW-002 真实浏览器闭环

- 状态：**PASS（B 级 + 一次记录的真实浏览器 smoke）**
- 日期：2026-09-22
- 基线：`c7fa68b` + P3-08B/P3-08C 的全部未提交改动（开始前的 `git status --short` = 25 modified +
  18 untracked，全部保留）
- 依赖：P3-07A/B/C、P3-08A/B/C 均 PASS；`schema_version = 14`，migration `0001`～`0014` 连续；95 包
- 路由：本卡是纯前端接线 + 记录式浏览器验证，未新增高风险语义，故 `SOL_NOT_REQUIRED`（见 §7）；
  未尝试、也未声称任何模型切换

## 1. 范围与总评

本卡把 P3-07B 已实现、但 `/search` 页面尚未接入的 `person_ids` / `person_match` 条件接到页面上，并完成
P3-08B 矩阵 `BROW-002` 尚未执行的闭环：人物 any/all × 地点 AND → 人物改名使旧会话 stale → 按当前条件
重搜。

没有改 ADR-0009/0010、companion、API model/routes/search service、人物服务、migration/schema、依赖、
`uv.lock`、`PhotoSearch.spec` 或其它页面。改动面严格落在任务卡授权范围，另加本卡直接需要的浏览器夹具。

## 2. 逐文件改动

| 文件 | 改动 |
|---|---|
| `src/photo_search/web/search.html` | 查询面板新增 `person-filter` fieldset（`query-person-match`、`person-clear`、`person-reload`、`person-selection`、`person-options`、`person-more`、`person-echo`、`person-status`）；上传面板补「人物筛选只作用于普通搜索：上传请求不携带 `person_ids`/`person_match`」 |
| `src/photo_search/web/search.css` | `person-filter`/`person-controls`/`person-options`/`person-option` 样式：`min-width:0`、单列友好 `repeat(auto-fill, minmax(min(100%, 18rem), 1fr))`、`overflow-wrap:anywhere`、`:focus-within` 焦点环；窄宽媒体查询把人物控件收成 100% |
| `src/photo_search/web/search.js` | 新增人物选择模块（`PERSON_LIMIT = 25`、`state.person`、`selectedPersonIds`/`updatePersonSelection`/`syncPersonControls`/`disablePersonFilter`/`renderPersonOptions`/`togglePerson`/`clearPersonSelection`/`loadPersons`/`renderPersonEcho`/`conditionFingerprint`）；`collectFilters` 增加 `personIds`/`personMatch`（未选人强制 any）；`sendTextSearch` 仅在真选择时发两个人物字段；`sendSearch` 增加条件指纹续页闸门；`renderPage` 渲染 canonical 回显；`loadCapabilities` 增加人物读层门控；补 `INVALID_PERSON_FILTER` 等失败文案；把 `CATALOG_FILTER_REVISION_STALE` 文案改为同时点名人物 |
| `tests/unit/test_api_frontend.py` | 端点白名单加 `/persons`；新增 9 个 P3-08D 静态契约用例（分页读取、按 id 保留选择且不合并同名、只在真选择时发送、上传请求无人字段、canonical 回显、失败面结构化、条件变化不得续页、无读层即禁用、标签/键盘/窄宽、URL 与 HTML sink 闭集） |
| `docs/diagnostics/frontend-smoke-checklist.md` | 新增 §8「搜索页的人物筛选（P3-08D）」：夹具、启动、13 步核对表、DOM 断言、已知限制 |
| `tools/diagnostics/search_person_smoke_fixture.py` | 新增夹具：调用 `search_smoke_fixture` 建可搜索图库与已激活 profile，再 seed 30 个 active canonical 人物，并用生产 `search_request` 打印权威期望集合 |

## 3. 实现对照（任务卡要求 → 证据）

### 3.1 人物选择必须来自权威读 API

- 只调用受会话保护的 `GET /api/v1/persons?kind=all&limit=25[&cursor=…]`；`PERSON_LIMIT = 25` 一页一读，
  翻页沿用服务端 `next_cursor`，不一次物化全部人物，也不读数据库、不走 `merged_into`
  （静态断言 `merged_into`/`name_key` 不在脚本中）。
- 每个选项文本为「名称或 `未命名人物` + `#person_id` + 照片/脸计数」；选项是带显式 `label[for]` 的
  checkbox，`#id` 来自稳定主键而不是名字。
- 选择状态存在 `state.person.selected`（以 `person_id` 为键），重绘/翻页只按该表回填 `checked`，
  因此翻页与重新载入都不丢已选 id；`清空人物选择` 只清选择与勾选。
- schema `< 13`、`schema_status != ok`、能力接口或人物列表读取失败时控件 `disabled` 并写明原因
  （含「这不代表图库里没有人物」）；只有真的 0 行才说「空列表，不是读取失败」。

### 3.2 any / all 与请求接线

- 未选人时 `全部（all）` 选项 `disabled`，`person_match` 强制回 `any`；两个人物字段只在
  `filters.personIds.length` 为真时写入 JSON body，升序、去重、正整数。
- 与 `place_ids`/`place_state`/`include_descendants`/来源/可用性同请求发送，页面不做本地命中或二次过滤
  （静态断言 `hits.filter(` 不存在）。
- 续页绑定：非续页请求记录 `conditionFingerprint(filters)`（文本、来源、可用性、地点、后代、地点状态、
  人物集合与匹配方式、每页数量）；续页前指纹不同则不发请求，提示「旧会话的游标只能沿用首次查询绑定的
  条件，不能续页」，并显示「按当前条件重搜」。
- `/search/image` 请求体不含任何人物字段（静态断言按 `sendImageSearch` 函数体切片）。

### 3.3 回显、错误与隐私

- 成功后 `renderPersonEcho(body.filters, state.search.mode)` 显示服务端 `filters.person_ids`/
  `filters.person_match`；服务端把提交 id 折叠成 canonical 集合时，页面额外说明「服务端把提交的 id
  解析并折叠成了上面的 canonical 人物」。
- `INVALID_PERSON_FILTER`、`PERSON_NOT_FOUND`、`PERSON_REDIRECT_CYCLE`、
  `CATALOG_FILTER_REVISION_STALE`、`SEARCH_SESSION_EXPIRED`、`INVALID_CURSOR` 都给结构化失败 +
  「按当前条件重搜」，不降级成空结果，条件保持不丢。
- 无外链、`textContent` 构造、同源 credential、CSRF 规则与 `PATH_LIKE` 脱敏沿用；人物 id 只在内存状态，
  不写 URL、不写存储、不进 DOM 属性。

## 4. 验收等级与 JUnit 证据

等级：**B + 记录式真实浏览器 smoke**（任务卡规定）。

```powershell
$env:UV_CACHE_DIR=(Resolve-Path '.uv-cache').Path
uv run --no-sync python -m pytest -q -p no:cacheprovider `
  tests/unit/test_api_frontend.py `
  tests/integration/test_p3_person_search_filter.py `
  tests/integration/test_p3_person_place_interleave.py `
  --basetemp=.pytest-basetemp-p308d/focused `
  --junitxml=test-results/p3-08d-focused.xml
```

| 证据 | 结果 |
|---|---|
| `test-results/p3-08d-focused.xml` | **131 tests / 0 failures / 0 errors / 0 skipped** |
| `test-results/p3-08d-full.xml`（`tests/unit tests/integration`） | **1461 tests / 0 failures / 0 errors / 48 skipped**（221.9 s；48 项均为未安装 pinned 权重的环境跳过） |
| `uv run --no-sync ruff check .` | exit 0，`All checks passed!` |
| `git diff --check` | exit 0，clean |
| `uv lock --check` | exit 0，95 packages |

Ruff 另打印一行既有的目录访问警告（受限令牌无法枚举被忽略的临时目录，`os error 5`），随后仍为
`All checks passed!`、exit 0；该警告与 §7 第 1 条是同一环境事实。

环境事实：受限令牌下 pytest 连自己的 basetemp 都无法枚举（既有 `PermissionError [WinError 5]`），
因此上述测试全部经**正常 Windows 权限路由**执行，basetemp 为项目内全新子目录；**未修改任何 ACL**。

## 5. 页面请求形状的自动化证据

- 静态契约（`tests/unit/test_api_frontend.py` P3-08D 段）覆盖：分页读取形状、按 id 保留选择、同名不
  合并、只在真选择时发送、上传无人字段、canonical 回显、失败面、条件指纹、无读层禁用、标签/键盘/窄宽
  样式、URL 与 HTML sink 闭集；
- 页面实际发出的请求由浏览器访问日志确认：`GET /api/v1/persons?kind=all&limit=25`、第二页
  `&cursor=cHAxOjI1`（服务端游标，页面不构造）、`POST /api/v1/search`（人物条件在 body）、
  `POST /api/v1/search/image`（无人物字段，见 §6 第 11 步）。

## 6. 记录的真实浏览器闭环（BROW-002）

夹具：

```text
uv run --no-sync python tools/diagnostics/search_person_smoke_fixture.py `
  --root <项目内临时目录> --reset
```

夹具报告 `status=ok`，`persons={dad:1, uncle:2, same_label:[3,4], total:30}`、
`place={place_id:1, asset_id:6}`、
`expected={dad:[1,6], any_dad_uncle:[1,6], all_dad_uncle:[1], dad_and_place:[6],
no_person_condition:[1..6]}`（由生产 `search_request` 实测）。

服务：`.venv\Scripts\python.exe -m photo_search --port 8821`（`PHOTO_SEARCH_DATA_ROOT` 传中文/空格数据根）。
浏览器：**Codex 内置浏览器（iab）**，逐条执行；这不是 Playwright/Selenium 测试套件的 PASS，只是本构建
内置浏览器上的记录式 smoke。页面报告 `版本 0.1.0 · 图库 schema v14（ok）`。

| # | 操作 | 实际观察 | 结果 |
|---|---|---|---|
| 1 | 打开一次性链接（`next=/search`） | 303 落到 `/search`；人物面板「人物筛选（只作用于普通搜索）」；状态「本页 25 个人物（active canonical） · 还有更多」；选项如 `爸爸 #1（照片 2 · 脸 2）`、`叔叔 #2（照片 1 · 脸 1）` | 通过 |
| 2 | 未选人时看匹配方式 | `全部（all）` disabled、`清空人物选择` disabled | 通过 |
| 3 | 勾 `爸爸 #1` → 搜索（any） | 回显 `后端回显（canonical）：person_ids=[1]、person_match=any。`；「已显示 2 行 · 候选共 2 个」，行为资产 `#1`、`#6` | 通过（= `dad:[1,6]`） |
| 4 | 再勾 `叔叔 #2` → 选 `全部（all）` → 搜索 | 回显 `person_ids=[1, 2]、person_match=all`；「候选共 1 个」，只剩资产 `#1` | 通过（= `all:[1]`） |
| 5 | 回 `任一（any）` + 地点 `place_id=1` → 搜索 | 「候选共 1 个」，只剩资产 `#6`（人物 × 地点 AND） | 通过（= `dad_and_place:[6]`） |
| 6 | 每页数量 1 → 搜索 | 「已显示 1 行 · 候选共 2 个 · 还有下一页」 | 通过 |
| 7 | 第二标签页 `/persons` 把 `#1` 改名（勾选「我知道同名时会执行合并」后保存） | `#1 的姓名已保存为「爸爸改」（revision 2）`、`changed=true`；刷新后列表显示 `爸爸改 #1 revision 2` | 通过 |
| 8 | 回搜索页点「加载下一页」 | 结构化 `[CATALOG_FILTER_REVISION_STALE]`（含请求编号）+ 「人物、地点或可见性在翻页期间发生了变化（它们共用同一个权威筛选 revision）；旧会话不再有效，请重新搜索。」；人物选择与回显保留、「按当前条件重搜」出现；结果区没有混入第二版 | 通过 |
| 9 | 「重新载入人物」→「按当前条件重搜」 | 选项标签更新为 `爸爸改 #1（照片 2 · 脸 2）` 且已选 `#1` 仍在；重搜成功「候选共 2 个 · 还有下一页」，回显 `person_ids=[1]` | 通过 |
| 10 | 「清空人物选择」→ 搜索 | 回显 `person_ids=[]、person_match=any（本次查询没有人物条件）`；「已显示 6 行 · 候选共 6 个」 | 通过（= `no_person_condition`） |
| 11 | 选 `爸爸 #1` 后上传一张合成 JPEG 并提交 | 结构化 `RUNTIME_CAPABILITY_UNAVAILABLE / IMAGE_ENCODER_UNAVAILABLE`（服务端原文 + 请求编号），**不是** 422 multipart 未知字段 → 上传请求没有携带人物字段 | 通过 |
| 12 | 「加载更多人物（沿用当前游标）」 | 25 → 30 个选项，状态「本页 5 个人物（active canonical） · 已到末尾」，最后一项 `未命名人物 #30（照片 0 · 脸 0）`；已选 `#1` 仍勾选 | 通过 |
| 13 | 同标签区分 | `#3`、`#4` 都显示 `未命名人物`，各自带 `#id` 与计数 | 通过 |
| 14 | 键盘 `Tab` / `Space` / `Enter` / 方向键 | `Tab` 依次到 `人物匹配方式 → 清空人物选择 → 重新载入人物 → 人物选项`；`Space` 只勾选被聚焦的 `#3`（`#4` 未勾）；方向键把匹配方式从 `any` 切到 `all`；`Enter` 在提交按钮上发起搜索 | 通过 |
| 15 | 窄宽 + DOM 断言 | 内置浏览器实际视口 `clientWidth=380`、`scrollWidth=380`、`innerWidth=395`；`scrollWidth <= clientWidth`；内部横向溢出元素 0 个；人物面板宽 323、选项宽 287（单列换行） | 通过（见 §7 第 2 条） |
| 16 | 页面文本 / URL / 图片源 | 正文无盘符/UNC/用户目录；`location.href == http://127.0.0.1:8821/search`（无 `token=`、无 person id）；无 `http(s)://` 外链；无 `img`；唯一出现的 `embedding` 字样来自服务端英文错误原文（`the bound embedding space`），不含向量/分数/坐标 | 通过 |
| 17 | 服务端访问日志 | 请求全在 `/api/v1/*`、`/search`、`/persons`、`/web/*`；`/geo` 命中 0；`token=` 仅两处，访问日志为 `token=<redacted>`，未脱敏处是启动器自己的引导横幅 | 通过 |

访问日志路径分布（首个服务实例）：`/api/v1/search` 8 次、`/api/v1/persons` 6 次（含第二页
`&cursor=cHAxOjI1`）、`/api/v1/search/image` 1 次、`/api/v1/session` 1 次（303，`token=<redacted>`）、
`/api/v1/persons/1/name` 1 次（改名），其余为 capabilities/sources/stats/coverage/thumbnail 与 `/web/*`；
`/geo` 0 次。

## 7. 环境事实、未验证项与范围裁定

1. **环境事实（不得当产品失败）**：受限令牌下服务进程无法在系统 `%TEMP%` 建上传暂存目录
   （`search/upload.stage_upload` → `PermissionError` → `/api/v1/search/image` 500 `INTERNAL_ERROR`）。
   同一原因也让 pytest 无法使用自己的 basetemp。两处都按仓库既有做法经**正常 Windows 权限路由**
   （并把 `TEMP`/`TMP` 指向项目内临时目录）后得到 §6 的真实观察；未改 ACL，未把环境 setup failure
   记成产品缺陷，修复前的那次 500 不作为验收结果。
2. **窄宽**：本次证据是内置浏览器的真实视口（约 395 CSS px，窄于 640）；`viewport` 覆盖在本机不改变
   实际窗口尺寸（与 P2-03C §6.3、P3-06 §7.5 同一事实），没有操作系统级 200% 缩放或真机证据。
3. **上传路径**：本进程没有图像编码器，只观察到结构化 409；「上传请求不带人物字段」由该 409 +
   静态断言 + `tests/integration/test_p2_search_image.py` 的未知字段 422 共同判定，未观察到上传成功后的
   `person_ids: []` 回显。
4. **仍不在本卡范围**：30 万容量下的人物条件延迟、多进程/多实例会话、`/search` 自然语言解析器
   （仓库仍无）、C 级打包（本卡未改 `PhotoSearch.spec` 或打包输入集合；等级不含 C）。
5. **BROW-002 形态**：人物纠错按任务卡用**改名**完成；merge/split/映射确认的浏览器路径由 P3-08B 的
   记录式 smoke 覆盖，本卡不重复。
6. **范围裁定**：本卡只复用 ADR-0009 已冻结的请求/回显/错误面，未改 canonical 解析、revision 语义、
   会话摘要、API 错误码/信封或人物 authority，因此按任务卡写 `SOL_NOT_REQUIRED`，未启动独立协议/工程
   审查会话，也未升级模型档位。若需对本卡前端 diff 再做独立工程复核，应单独出卡，不在本卡夹带。

## 8. 隐私与不变量小结

- 人物条件仍是权威判定：页面只发 `person_ids`/`person_match`，命中集、顺序与 canonical 回显全部来自
  服务端；页面不做本地过滤，也不自行解析 `merged_into`；
- 没有第二套 revision：改名只让共用的 `catalog_filter_revision` 前进一步，旧会话续页得到既有
  `CATALOG_FILTER_REVISION_STALE`，页面保留条件并要求重搜；
- 人物 id 只在内存选择集合中，不写 URL、不写浏览器存储、不进 DOM 属性；
- DOM/URL/错误文案/访问日志中没有 CSRF/session 值、本地路径、bbox/landmark、向量、相似度、幂等
  payload/hash 或内部快照。

## 9. 交接

```text
任务 ID / 状态：P3-08D / PASS（B + 记录式真实浏览器 smoke；SOL_NOT_REQUIRED）
改动面：src/photo_search/web/search.{html,css,js}、tests/unit/test_api_frontend.py、
        docs/diagnostics/frontend-smoke-checklist.md、tools/diagnostics/search_person_smoke_fixture.py、
        本报告、docs/development-status.md、docs/tasks/P4-00A.md
未改动：ADR-0009/0010、companion、api model/routes/search service、persons/**、migration/schema、
        依赖、uv.lock、PhotoSearch.spec、其它页面
证据：test-results/p3-08d-focused.xml（131/0/0/0）、test-results/p3-08d-full.xml、
      Ruff / git diff --check / uv lock --check clean
下一任务：P4-00A（拍摄时间/时区/回退来源/新 order 契约），C2 / GPT-5.6 Sol / high 聚焦终审冻结；
      模型切换由用户在 Codex 中手动完成
```

下一任务卡：`docs/tasks/P4-00A.md`。
