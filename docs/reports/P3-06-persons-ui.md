# P3-06 — 人物页、同名提示、待归类、脸框、最近操作和撤销 UI

- 日期：2026-09-20
- 执行模型：DS-L / DeepSeek V4.1 Flash / low（模型切换由用户在 Codex 中手动完成）
- 起始基线：`84876b8`（P3-05C 审查收尾，工作树干净）
- 数据库：`schema_version = 13`，migration 连续 `0001`～`0013`，**本任务未新增/修改任何 migration**
- 结论：**PASS（B + C 级 + 一次真实浏览器 smoke + D 级写路径拒绝/回滚回归）**

## 1. 范围与总评

本任务只交付人物整理的**只读接口 + 本地网页闭环**，不改变 P3-03～P3-05C 已冻结的人物、单脸控制、
拆分、幂等与撤销语义。交付面：

1. `GET /api/v1/persons`：active canonical 人物的稳定 keyset 分页列表（`kind=all|named|unnamed`）；
2. `GET /api/v1/faces`：两个互斥视图（`person_id=` 的有效成员、`assignment_state=manual_unassigned`）；
3. `GET /api/v1/faces/{face_id}/thumbnail`：服务端按**权威人脸框**从既有安全预览裁剪的有界头像；
4. `GET /api/v1/person-operations`：最近人物操作摘要（倒序 keyset），只给 undo 需要的 id/revision；
5. `/persons` 页面（`persons.html`/`persons.js`/`persons.css`）+ 三个既有页面的导航入口（由
   `/capabilities` 证明人物只读层真实存在时才显示）；
6. 诊断夹具 `tools/diagnostics/persons_page_smoke_fixture.py`（合成库 + 直接 seed 的权威人物事实）。

设计上刻意收窄：

- 人物/控制/操作仍**只有 SQLite 一个权威**；页面不直读数据库、不建第二份状态；
- 列表与页面只拿到页面画得出的字段，**不返回** `name_key`、alias 原文、内部快照、路径、bbox、
  landmark、向量、相似度或 confidence；
- `face_id` 仍是**最小人工单位**：勾选哪几张脸就只有这几张脸被改动，同图 sibling 不联动；
- 同名是否合并、revision、`request_id` 幂等、拆分资格与撤销可否补偿**全部由服务器判定**，页面不预测；
- face 头像**不接受客户端路径或 bbox**：端点只接受 `face_id`，几何来自 `faces`，像素来自已发布的
  P1-03 预览，发布到可重建缓存。

## 2. 实现

### 2.1 只读模型（新增/加性）

| 文件 | 变更 |
|---|---|
| `src/photo_search/persons/members.py` | **新增**：`MEMBER_PREDICATE` / `MEMBER_SQL` / `member_counts()` —— 「有效成员脸」的唯一 SQL 定义 |
| `src/photo_search/persons/service.py` | 改从 `members.py` 取同一个定义（`_MEMBER_SQL` 别名，行为逐字不变，未改任何写路径） |
| `src/photo_search/persons/read.py` | **新增**：三个 keyset 列表的读取投影、三套独立 cursor token 空间、`clamp_page_limit` |
| `src/photo_search/persons/operations.py` | **加性**：`item_projection()` 公开别名（复用 `_item_projection`，让页面与重放共用一份 `changed` 语义） |

三个列表的 cursor 前缀互不通用（`pp1`/`pf1`/`po1`），跨列表使用一律 `INVALID_CURSOR`；人物列表只列
`merged_into IS NULL`，被合并的旧 id 不会重复出现；人物/人脸视图的计数沿用 ADR-0006 §5 的有效成员
规则（不含 `MANUAL_UNASSIGNED` 与活动排除），`GET /persons/{id}` 与本页因此不可能对同一个人给出
两个不同的脸数。

### 2.2 人脸头像（新增）

| 文件 | 变更 |
|---|---|
| `src/photo_search/media/contract.py` | **加性**：`FACE_CROP_PROCESSOR_ID="face-crop@1"`、`FACE_CROP_LONG_EDGE=256`、`FACE_CROP_JPEG_QUALITY=85`、`FACE_CROP_MARGIN=0.35`（`SPECS`/`/capabilities` 的 media 契约未改） |
| `src/photo_search/media/decode.py` | `read_orientation()` 公开（`_read_orientation` 仍是同一实现的别名） |
| `src/photo_search/media/cache.py` | **加性**：可选 `face_crops_root`（默认 `<data_root>/cache/face-crops`）+ `face_crop_path(key)` |
| `src/photo_search/media/face_crop.py` | **新增**：bbox 校验、方向归一尺寸探测、帧映射与裁剪、`crop_key()`、`render_crop()` |
| `src/photo_search/api/routes.py` | `GET`/`HEAD /api/v1/faces/{face_id}/thumbnail` + `_ensure_preview()`（复用 `/assets/{id}/preview` 的状态机） |

帧映射写成可复核的一行：`faces.bbox_json` 在**方向归一化原图**像素系（P3-02 用 `working_scale`
折算回原图），预览是同一帧按长边 ≤1600 缩放，因此 `factor = preview_width / 原图归一化宽度`；原图
宽度只读头部（不解码像素），并且只对 EXIF 5/6/7/8 交换宽高——与 `load_normalized` 同一规则。
裁剪为 256 px 有界、**从不放大**，落在 `<data_root>/cache/face-crops/face-crop@1/<ab>/<cd>/<key>.jpg`，
文件名为 `(processor, face_id, 舍入后 bbox, 内容 sha256)` 的摘要，因此重新检测导致框移动时不会复用
旧像素。

### 2.3 前端

| 文件 | 变更 |
|---|---|
| `src/photo_search/api/web.py` | `/persons` 文档路由（`PERSONS_NAME`，`no-cache`），`__all__` 同步 |
| `src/photo_search/web/persons.html/.css/.js` | **新增**：三个视图、人脸卡（face_id 粒度勾选）、名字表单（同名前置提示）、显式批量合并、单/多脸 assign/unassign/restore_auto、拆分、最近操作与撤销 |
| `src/photo_search/web/index.html`、`places.html`、`search.html` | 加 `id="nav-persons"` 入口，**默认 `hidden`** |
| `src/photo_search/web/app.js`、`places.js`、`search.js` | `revealPersonsNav(body)`：仅当 `schema_version >= 13 && schema_status == "ok"` 时显示入口（与模型权重无关） |

页面完全沿用既有 dependency-free 风格（无框架、无构建链、无 CDN、无 `innerHTML`），CSRF 只走
`ps_csrf` cookie + `X-CSRF-Token` 头，所有写入都带新 `request_id` 与读到的 revision，冲突时展示
服务器 `code`/`reason` 并刷新相关列表。开发期在浏览器里实际观察到两处真实缺陷并已修复：

* `AUTO_ALLOWED` 的人脸标签写成「无有效归属」，与同一行的「有效归属 #N」自相矛盾 → 改为
  「自动（没有人工锁）」，有效归属仍单独一行；
* 撤销成功后操作日志不刷新，刚被补偿的操作仍显示「已提交」→ 现在撤销成功即重新加载日志。
* 另有一处窄窗口真实溢出（`#name-result` 里的 64 位 operation id 撑开 528 px > 466 px）→ 给
  `.hint/.state-block/.error/.card-head/.place-row/.log li/.face-card` 加 `overflow-wrap: anywhere`。

## 3. 隐私面与不变量

- 四个只读端点 + 头像端点的响应模型全部 `extra="forbid"`（OpenAPI `additionalProperties: false`），
  任何多余字段都会在响应校验处失败而不是被静默丢弃；
- 响应负向扫描（集成测试）覆盖 `name_key`、`normalized_alias`、`bbox`、`landmarks`、`embedding`、
  `vector`、`similarity`、`confidence`、`det_score`、`blur_variance`、`before/after_value_json`、
  `payload_sha256`、`request_id`、`root_path`、`absolute_path`、`relative_path`，并断言响应文本里
  没有 `C:\`/`D:\`；
- 操作日志投影只给 `operation_id/type/revision/status/undo_of/undone_by/target/changed/item_count/
  person_ids/face_ids/catalog_filter_revision/created_at/committed_at`；**不给** `request_id` 与
  payload 摘要，也**不给**任何「能不能撤销」的预测；
- 头像端点不接受路径/bbox/尺寸参数；源离线返回 `SOURCE_FILE_MISSING`，坏媒体返回其记录中的
  `DECODE_FAILED`/`FORMAT_UNSUPPORTED`，人脸不存在返回 `FACE_NOT_FOUND`，几何非法返回
  `FACE_STATE_CONFLICT`，页面侧一律退化为「脸图不可用」占位；
- 人工操作仍只触发既有 `PERSON_CHANGED` + face repair 边界：本任务的写入测试断言图片 outbox 与
  vector/space/generation 没有任何新写入（未新增任何写路径）。

## 4. 自动验证

| 检查 | 结果 |
|---|---|
| `uv run --no-sync python -m pytest -q -p no:cacheprovider tests/unit tests/integration --basetemp=.pytest-tmp/P3-06-full --junitxml=test-results/p3-06-full.xml` | exit 0；JUnit `tests=1230 failures=0 errors=0 skipped=48`（216.8 s）；48 项均为缺模型权重/artifact 或未启用 GPU 的显式环境 skip |
| 本任务新增用例 | **45 例**：`tests/unit/test_p3_persons_read.py` 16、`tests/integration/test_p3_persons_read_api.py` 8、`tests/integration/test_p3_face_thumbnail.py` 8、`tests/integration/test_p3_persons_page.py` 5、`tests/unit/test_api_frontend.py` 新增 P3-06 段 8（基线 1185 → 1230） |
| `uv run --no-sync ruff check .` / `git diff --check` / `uv run --no-sync uv lock --check` | clean / clean / exit 0（95 packages，依赖未变） |
| 排序与分页无重无漏 | 人物/人脸/操作三个列表各 2 页拼回完整集合，无重复；跨列表 cursor 一律 `INVALID_CURSOR`；`kind`/`assignment_state`/`limit` 非法值 422 |
| canonical / redirect | 同名合并后旧 id 不再出现在列表，`GET /faces?person_id=<旧 id>` 返回 `redirected=true` + canonical 的成员；`GET /persons/{旧 id}` 仍可用 |
| OpenAPI 闭集 | 四个新响应模型 `additionalProperties: false`；查询参数集合恰为 `{kind,limit,cursor}` / `{person_id,assignment_state,limit,cursor}` / `{limit,cursor}`；`assignment_state` 为单值闭集 |
| 头像正常路径 | 双色合成图：左侧脸的裁剪偏红、右侧脸偏蓝（证明取的是**该脸的框**），两次返回不同字节；ETag/`If-None-Match` 304、HEAD 无 body、缓存目录出现 2 个 `face-crop@1` 文件 |
| 头像有界与不放大 | 120×90 图上的 20 px 脸 → 裁剪 34 px（=20×1.7 边距），不放大到 256 |
| 头像旋转图 | EXIF orientation 6 的 200×100 存储图（显示 100×200）：`probe_original_frame` 返回交换后的尺寸，左右两张脸的裁剪颜色与 Pillow `exif_transpose` 参考帧一致 |
| 头像拒绝路径 | 源离线 `SOURCE_FILE_MISSING`、坏媒体 `DECODE_FAILED`、`404 FACE_NOT_FOUND`、坏几何 `FACE_STATE_CONFLICT(reason=bbox_arity)`；四种情形都不产生裁剪文件、响应无本机路径 |
| 写路径 D 级回归 | 过期 revision `FACE_ASSIGNMENT_CONFLICT(current_revision)`、同 `request_id` 逐字重放 `replayed=true` 且不写入、同 key 不同 payload `FACE_IDEMPOTENCY_CONFLICT`、501 项 `FACE_BATCH_TOO_LARGE`、被后续人工编辑改变的操作 `FACE_OPERATION_NOT_UNDOABLE`、撤销重放返回已记录的 UNDO |
| 会话/CSRF/Origin/Host | 四个只读端点与头像匿名一律 401 `SESSION_REQUIRED`；写请求缺 CSRF → 403 `CSRF_INVALID`，缺/跨 Origin → 403 `ORIGIN_NOT_ALLOWED`，非本机 Host → 403 `HOST_NOT_ALLOWED`，且四种拒绝后权威状态逐字未变 |
| 重启恢复 | 关闭进程后用同一 data root 重建应用：人物列表、操作日志（同一 `operation_id`）、成员脸与头像裁剪都从权威事实重新得到（头像由新进程重新生成） |

## 5. 真实浏览器 smoke（人工步骤与结果）

夹具：`uv run --no-sync python tools/diagnostics/persons_page_smoke_fixture.py --reset`
（`%TEMP%\PhotoSearchPersonsSmoke`；17 张合成 JPEG、23 张直接 seed 的权威脸、15 个人物、1 张
`MANUAL_UNASSIGNED` 脸；不下载模型、不读真实照片库）。
服务：`.venv\Scripts\python.exe -m photo_search --port 8791` + `PHOTO_SEARCH_DATA_ROOT`（中文/空格路径）。
浏览器：Codex 内置浏览器，逐条人工核对（**不是** DOM 自动化覆盖）：

| # | 操作 | 实际观察 | 结果 |
|---|---|---|---|
| 1 | 打开启动器一次性链接（`next=/persons`） | 303 落到 `/persons`；标题「人物整理」；`app-meta` 显示 `schema v13（ok） · 人脸事实资产 17`；导航含 返回/搜索/地点/三个页内锚点 | 通过 |
| 2 | 已命名人物视图 | 「本页 2 条 · 已命名人物 · 已到末尾」；爸爸 #1、妈妈 #2 各有真实裁剪头像与「有效成员：脸 2 · 照片 2」 | 通过 |
| 3 | 切「未命名分组」 | 13 条，1 条无头像时显示「暂无头像」占位 | 通过 |
| 4 | 每页条数改 10 → 加载更多 | 第一页「本页 10 条 · 还有更多」，点「加载更多（沿用当前游标）」后「本页 3 条 · 已到末尾」，合计 13 行且 `#id` 去重后仍 13 个 | 通过 |
| 5 | 切「待手动归类」 | 「本页 1 张待手动归类脸 · 已到末尾」；行显示 `人工取消归类（带锁）`、`控制 revision 1`、`有效归属 无`、真实裁剪头像 | 通过 |
| 6 | 载入某人的脸 | `#1 · 本页 2 张脸 · 人物 revision 1 · 已到末尾`，两张脸各一张头像 | 通过 |
| 7 | 删除夹具里「合影 005.jpg」后重新载入妈妈的脸 | 一张真实头像 + 一条「脸图不可用」占位（服务端 409，页面不显示任何路径、不显示破图） | 通过 |
| 8 | 保存名字 | 首次 `PUT /persons/3/name` 200 → 列表出现「小舅 #3 revision 2」；**用旧 revision 重放** → 页面显示 `[PERSON_REVISION_CONFLICT] ...（人物 revision 已过期，需重新加载后再提交）（请求编号 …）` | 通过 |
| 9 | 同名保存 | 把 #3 名字存成「妈妈」→ 服务器执行同名合并，页面显示「#3 已同名合并到 #2（后续都会显示合并后的人物）」并刷新列表 | 通过 |
| 10 | 最近操作与撤销 | 日志 2 条（同名合并 revision 2、保存名字 revision 1，含时间/影响项数/人物 id）；点「撤销此操作」→ 撤销结果给出新操作与 `changed=true`，刷新后 #3 作为独立人物回来（revision 4） | 通过 |
| 11 | 窄/宽窗口 | 481 px：`scrollWidth 466 ≤ clientWidth 466`；640 px：`625 ≤ 625`；1280 px：`1265 ≤ 1265`（截图 `test-results/p3-06-persons-narrow.png`、`p3-06-persons-wide.png`） | 通过 |
| 12 | 键盘 | Tab 依次可达「撤销此操作」按钮 → body → 跳到主要内容 → 导航链接；每个 `input/select/textarea` 都有 `label[for]`，每张 `img` 都有 `alt` | 通过 |
| 13 | 页面内 DOM 断言 | 正文无 `[A-Za-z]:\`/UNC/`/Users/`；无 `http(s)://` 外链；所有 `img src` 都是相对 `/api/v1/faces/{id}/thumbnail` | 通过 |
| 14 | 服务端访问日志 | 全部请求在 `/api/v1/*`、`/persons`、`/`；一次性凭证记成 `token=<redacted>`；**`/geo` 命中 0 次**（页面从不请求坐标） | 通过 |

窄视口用的是浏览器 viewport 覆盖（不是操作系统级缩放）；`localStorage/sessionStorage` 在只读求值
作用域里不可用，因此该条按清单以「脚本静态契约 + 无存储写入代码」判定，而不是本次运行时读数。

## 6. C 级打包证据

| 检查 | 结果 |
|---|---|
| `uv run --no-sync pyinstaller --noconfirm PhotoSearch.spec` | exit 0，`Build complete!`（最终源码修订后重建） |
| 全新目录 `packaged_api_smoke.py --work-dir "D:\PhotoSearchSmoke\P3-06 中文 空格"` | exit 0、`status=ok`、`problems=[]`；报告 `test-results/p3-06-packaged-smoke.json` |
| `/persons` 与静态资源 | `persons_page_status=200`、`Cache-Control: no-cache`、无外链、无本机路径、`persons_script_status=200`、`persons_style_status=200`、`nav_offers_persons=true` |
| 四个新只读面 | `persons_list_shape={kind:all, persons:0, has_more:false}`；`manual_faces_status=200 view=manual_unassigned rows=0`；`person_operations_shape={operations:0,...}`；无人物时 `GET /faces?person_id=1` 诚实返回 `404 PERSON_NOT_FOUND`；`face_crop` 未知 face → `404 FACE_NOT_FOUND` |
| 会话边界 | 四个新面匿名访问全部 401；`person_read_has_no_private_fact=true` |
| schema 与 migration | 打包 data root 实测 `schema_version=13`，CHECK 含 `SPLIT`/`SPLIT_GROUP`/`FACE_ASSIGN`/`FACE_CONTROL`；bundle 内 `0001`～`0013` 连续 13 个文件，与源码逐个 SHA-256 一致，`0013 = 9DEA2D67726A61A958AF57CCE7DFBEA6A433DC7679C406320F2E5DD2E1CCBDE4` |
| 打包前端内容 | `_internal/photo_search/web/` 含 `persons.html/js/css`，且打包后的 `persons.js` 含最终的两处修复、`persons.css` 含 `overflow-wrap: anywhere` |

## 7. 未验证项（不得当作已验证）

- **真实家庭照片库规模**：所有夹具都是合成图；未在真实照片库或 300k 规模上评估页面/列表延迟；
- **真实人脸检测/识别链路**：本机没有 buffalo_l 权重，`persons_page_smoke_fixture.py` 直接 seed 权威
  脸行（与 P3-03/P3-04 测试同一手法）。真实检测→分组→页面显示的整链仍沿用 P3-02/P3-03 已记录边界；
- **旧 schema 的入口隐藏**：只观察了「schema 13 → 入口显示」这一侧；`schema_version < 13` /
  `schema_status != ok` 时入口保持隐藏只有静态断言与代码路径，没有在真实旧库上跑过；
- **页面 DOM 自动化**：仓库仍不引入 Node/npm/无头浏览器依赖，本次仍是人工步骤；窄视口用 viewport
  覆盖而不是操作系统级 200% 缩放；
- **`localStorage/sessionStorage`**：内置浏览器只读求值作用域没有这两个对象，本次无法运行时读数，
  改为「脚本静态保证 + 无写入代码」；
- **多进程并发**：同一 data root 的双进程并发读写未测；
- **拆分/批量的大规模延迟**：未在接近 500 face 的真实规模上测拆分与批量的延迟；
- **头像质量**：0.35 边距与 256 px 上界是产品取舍，未做人脸主观质量评测；JPEG 预览整数像素导致的
  帧映射误差（<1 预览像素）已在模块文档记录，未做逐像素对齐验证；
- **`cache/face-crops` 清理策略**：与既有 `thumbnails/previews` 一样是可重建目录，但没有专门的
  配额/回收工具，本次未新增；
- **`.pytest-tmp` ACL**：本机受限令牌无法在 `.pytest-tmp` 下创建 basetemp，全部 pytest 仍走已授权的
  正常 Windows 权限路由执行（沿用 P3-05B 记录的环境事实，未改任何 ACL）。

## 8. 交接

- 报告：本文件；浏览器清单：`docs/diagnostics/frontend-smoke-checklist.md` §7（P3-06 段）；
- 夹具：`tools/diagnostics/persons_page_smoke_fixture.py`；
- 下一任务：`docs/tasks/P3-07.md`（人物 all/any AND 地点搜索、canonical ID、权威补查、缓存失效），
  推荐 **DS-H / DeepSeek V4.1 Flash / 高推理**；模型由用户手动切换。
