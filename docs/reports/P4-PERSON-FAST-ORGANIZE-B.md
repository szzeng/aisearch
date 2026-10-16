# P4-PERSON-FAST-ORGANIZE-B — 验收闭环报告

- 日期：2026-10-16
- 状态：**部分验收完成（SOURCE_CROP_PASS、SEARCH_TIMELINE_PASS、FACE_ADD_AND_RENDER_PASS；其余浏览器路径仍 PENDING）**。
- 关联任务：[任务卡](../tasks/P4-PERSON-FAST-ORGANIZE-B.md)；A 实施报告：[P4-PERSON-FAST-ORGANIZE-A](P4-PERSON-FAST-ORGANIZE-A.md)。
- Git 基线：`53e4e3d`（工作区原本干净；仅新增/修改本报告点名的测试和交接文件；未提交、未推送）。

## 已完成

### Source-crop 来源 A→B

新增 `tests/integration/test_p4_person_match_b.py::test_source_crop_worker_publishes_indexes_and_reconciles_into_dynamic_collection`。A 中的合成人脸以手工 `CONFIRMED` 关系归入人物 P，P 加入动态合集；B 是隔离图库中的另一张真实合成 JPEG。测试沿当前 source-detail worker 边界实际读取并校验原图、使用 `preserve_source_detail=True` 解码原图、执行真实 `warp_aligned_source_crop()`，再生成新版 FaceDocument、写入 ArtifactStore 并调用 `persist_face_document()`。

随后使用 `FaceIndexCoordinator` 发布新版候选并运行 `reconcile_asset()`。结果确认 B 的新版脸归到 P，动态合集照片数从 1 扩展到 2；只静态收录 A 脸的另一合集仍为 1。测试显式检查 source-detail 解码和源图裁脸函数各执行一次，裁脸输入尺寸为 320×240。

检测输出与 512 维向量由确定性合成替身提供；本机未加载或下载真实人脸模型，也未运行真实模型/GPU 推理。因此本证据证明当前 source-crop 图片解码、处理、持久化、索引、reconcile 和合集扩展链路，不代表真实模型识别精度或硬件验收。已有同文件用例同时复核 `TEMPORARY` 可作为参考、`EXCLUDED` 不作为普通候选、仅静态 face member 不扩展 B。

### 合集搜索与时间轴

扩展 `tests/integration/test_p4_search_collection.py::test_collection_scope_is_applied_by_fts_and_vector_candidate_walk`：同一合集含一个动态人物成员与一个没有人物关联、仅静态收录的人脸。集合限定把两张照片都传给向量候选入口和语义搜索 service，合集时间轴返回相同的去重资产集合。语义测试使用 `FakeEncoder` 与 `ServiceRecordingIndex`，证明的是 collection scope plumbing，不验证真实语义相关性或排序；原有 FTS 断言继续验证动态成员过滤。本次没有改动产品查询实现。

## 浏览器状态

- 使用新建的 `.tmp/P4-PERSON-FAST-ORGANIZE-A/browser/fixture3` 合成库、本机 `127.0.0.1:18932` 服务和新的一次性会话链接；未访问真实图库。链接仅打开一次，报告不保存链接或 credential。
- 会话交换返回 303，首页及认证后的 People 页面/API 均返回 200。浏览器中看到了 2 张待确认脸的候选组，展开后明确选择本组并确认选择提示；选择器中选中了既有测试合集。
- 点击“加入所选人脸到合集”时，内置浏览器被 JavaScript 确认框中断；随后读取状态重现 `Emulation.setFocusEmulationEnabled` 超时。按任务卡停止浏览器操作，没有重开链接或重试。只读检查隔离数据库确认 `person_collection_face_members` 行数为 0、合集 revision 仍为 1；**浏览器写入和成功回显未发生**。
- 因超时，未完成合集内单脸区查看/折叠、打开原图和对应 crop、移出后计数与忽略状态核对；未完成浏览器内合集搜索/时间轴路由。没有可导出的真实浏览器截图。

### 浏览器追加诊断（2026-10-16）

用户报告加入两张脸后无“人脸 2/2”成功提示，并遇到已收录人脸加载失败。按用户授权，使用全新的 `.tmp/P4-PERSON-FAST-ORGANIZE-A/browser/fixture4` 合成库、`127.0.0.1:18933` 本地服务和新的一次性会话链接复现；没有访问真实图库。浏览器完成登录、People 页面加载、候选组展开、明确选择 2 张脸和选择测试合集；点击加入后 CUA/CDP 再次超时，按任务卡停止所有后续浏览器交互，没有重试或重新打开链接。

本地服务日志记录合集写入请求 `POST /api/v1/persons/collections/1/<token>/faces` 返回 **401 Unauthorized**；该会话之前的合集列表 GET 为 200。只读检查合成 catalog 确认 `person_collection_face_members` 仍为 0 行、合集 revision 为 1，写入没有发生。`SecurityMiddleware` 在会话 Cookie 缺失/无效时返回 401；Origin/CSRF 校验失败则返回 403。因此当前浏览器证据指向提交请求未携带有效会话，尚不能定位是浏览器自动化环境丢失 Cookie 还是用户环境中的运行态会话问题。服务已停止。此证据与用户看到的“其他提示”无法一一对应，因为用户没有提供提示原文或状态码。

状态分别为：浏览器交互 **PENDING**（写入请求返回 401，成功回显未发生）；截图 **PENDING/未导出**；视觉 **PENDING**；API/合成链路见上文 **PASS**。合集内单脸区、原图/crop、移出计数与忽略状态仍未验证；B 和 Fast Organize 整体均不标 PASS。

### 前端错误反馈修复（2026-10-16）

为避免无结构错误响应继续显示成无法诊断的通用提示，人物页 API helper 仍调用浏览器原生 JSON 解析，并捕获解析失败；对旧服务/缺少该路由的无代码 404 显示接口不可用和 HTTP 状态，对非 JSON 响应显示 HTTP 状态，不展示原始响应正文；网络失败也会显示服务连接提示。401 明确提示重启并使用启动器新生成的一次性链接，403 保留拒绝码，不再误报为会话过期。已有 `X-Request-ID` 会随错误提示展示。合集说明文案更新后，`persons-albums.js` 资源查询串升至 `p4-person-fast-organize-a-5`；未分类的页面处理异常会显示 `CLIENT_ERROR`，避免继续给出没有诊断信息的同一提示。

定向 pytest **3 passed**：合集 UI 错误响应处理、合集 UI 现有计数/改名行为和静态人脸收录/列表 API 回归，JUnit 见 [face-display-fix.xml](artifacts/P4-PERSON-FAST-ORGANIZE-B-face-display-fix.xml)。Luna High 限定复核无 finding。`node --check`、Ruff、`git diff --check` 通过。该修复提升错误诊断和会话恢复指引；它没有绕过 401，也不能代替重启更新中的服务。浏览器写入、合集单脸浏览和后续操作仍 **PENDING**，未重复 CUA/CDP 操作。

## 验收命令与结果

定向 JUnit：`docs/reports/artifacts/P4-PERSON-FAST-ORGANIZE-B-targeted.xml`，**4 passed、0 failed、0 errors、0 skipped**。包含 source A→B worker、静态收录不扩展及 TEMPORARY/EXCLUDED 边界、合集限定搜索和时间轴成员范围。

```powershell
$env:PYTHONPATH='.;src;tests'; $env:PYTHONUTF8='1'
.venv\Scripts\python.exe -m pytest -q `
  tests/integration/test_p4_person_match_b.py::test_collection_person_match_extends_across_synthetic_sources_but_static_face_does_not `
  tests/integration/test_p4_person_match_b.py::test_source_crop_worker_publishes_indexes_and_reconciles_into_dynamic_collection `
  tests/integration/test_p4_search_collection.py::test_collection_scope_is_applied_by_fts_and_vector_candidate_walk `
  tests/unit/test_p4_ui_timeline_core.py::test_person_and_collection_scopes_use_effective_unique_photo_members `
  --junitxml=docs/reports/artifacts/P4-PERSON-FAST-ORGANIZE-B-targeted.xml `
  --basetemp=.pytest-tmp/P4-PERSON-FAST-ORGANIZE-B-final2
.venv\Scripts\python.exe -m ruff check tests/integration/test_p4_person_match_b.py tests/integration/test_p4_search_collection.py
git diff --check
```

Pytest、Ruff 和 diff-check 最终均通过。没有运行全仓、GPU、容量、性能、打包或发行验证；没有迁移真实 catalog。

## 独立复核与后续

按用户指定由 GPT-6 Luna / high 子代理独立复核新增测试范围，**无 finding**。复核确认 source-crop 用例确实经过真实解码、源图裁脸、ArtifactStore、文档持久化、候选索引和 reconcile；检测输出/embedding 是合成替身。合集范围覆盖动态和静态成员，搜索测试只证明 scope plumbing，不证明语义相关性或排序。该结果记录为 **LUNA High review**，不是 DS-H review。

本闭环尚未满足整卡 PASS。仅当浏览器内最终写入/回显及卡片要求的合集查看、移出、计数和忽略状态路径有新证据后，才能关闭 B；不要重用本次一次性链接。A 与 B 全部闭环前，Fast Organize 整体保持未通过状态。

### 错误反馈修复验证命令

```powershell
$env:PYTHONPATH='.;src;tests'; $env:PYTHONUTF8='1'; $env:UV_CACHE_DIR=(Resolve-Path .uv-cache).Path
.venv\Scripts\python.exe -m pytest -q `
  tests/unit/test_p4_person_collection_count_ui.py `
  tests/unit/test_p4_person_collection_ui.py `
  tests/integration/test_p4_person_collections.py::test_face_members_are_static_soft_anchors_and_ignore_state_is_independent `
  --junitxml=docs/reports/artifacts/P4-PERSON-FAST-ORGANIZE-B-face-display-fix.xml `
  --basetemp=.pytest-tmp/P4-PERSON-FAST-ORGANIZE-B-face-display-fix
node --check src/photo_search/web/persons-albums.js
.venv\Scripts\python.exe -m ruff check tests/unit/test_p4_person_collection_count_ui.py tests/integration/test_p4_person_collections.py
git diff --check
```

结果：pytest **3 passed**；Node 语法检查、Ruff 与 diff-check 均通过。集成用例明确断言：只有人物成员时脸级 API 返回空列表，单独提交脸后才出现在脸级列表。JUnit 记录见 [face-display-fix.xml](artifacts/P4-PERSON-FAST-ORGANIZE-B-face-display-fix.xml)。

### 用户问题根因核对（2026-10-16）

合集成员分为两种独立记录：加入人物写入 `person_collection_members`，显示在合集“人物”页；“单独收录的人脸”由 `person_collection_face_members` 提供，只会显示单独提交的 `face_id`，不会自动复制人物头像。该区分避免把人物动态照片误算成单脸静态收录。页面合集说明已明确这一区别。

用户此前描述的是把待整理的人脸加入合集。隔离浏览器对此脸级写入实际记录到 `POST .../faces` 返回 401，数据库核对为 0 条脸级成员且 revision 未变。因此这次头像缺失的直接原因是脸级加入请求未通过会话鉴权、没有形成成员记录；不是读取接口把已成功写入的脸过滤掉。人物页 API 错误提示现在将 401 明确呈现为会话失效，并指引完全重启本地图库、使用启动器新生成的一次性链接后再操作。此身份修复需要用户运行中的图库创建新会话；没有绕过安全中间件。浏览器重试仍受前述 CUA/CDP 超时限制，线上用户数据未操作，浏览器验收保持 PENDING。

### 用户报告 CLIENT_ERROR 的浏览器复现与修复（2026-10-16）

用户授权再做一次内置浏览器验证。使用全新 `.tmp/P4-PERSON-FAST-ORGANIZE-A/browser/fixture5` 合成库、`127.0.0.1:18934` 和新的一次性会话。待整理页选中候选组两张脸后，`POST /api/v1/persons/collections/1/<token>/faces` 返回 **200**；隔离 catalog 只读核对为 2 条 face members、revision 2。用户看到的 `CLIENT_ERROR` 在打开合集“人物 → 单独收录的人脸”后稳定复现；对应 `GET .../faces?limit=40` 返回 **200**，说明数据已写入且读取接口成功。

前端 `loadCollectionFaces` 对候选脸创建 `{ group, members }` 映射，随后却把映射对象当作 DOM `<details>` 直接访问 `.members`，引发 `TypeError`，被通用捕获显示成 `CLIENT_ERROR`。修复为将 DOM 元素与成员容器保存在同一分组记录中并向成员容器追加脸卡，脚本缓存版本升至 a-6。重新加载同一隔离页后，合集显示 2 张脸和 2 张去重照片；展开候选组后浏览器 DOM 有 2 张脸卡，2 个头像缩略图均加载成功。提交按钮自动化点击调用曾超时，但服务日志与提交后页面/counter 均确认写入完成；没有再次提交。真实浏览器全页截图见 [candidate-face-render-browser.png](artifacts/P4-PERSON-FAST-ORGANIZE-B-candidate-face-render-browser.png)。该修复的定向 UI 回归 **1 passed**，JUnit：[candidate-face-render.xml](artifacts/P4-PERSON-FAST-ORGANIZE-B-candidate-face-render.xml)，Node、Ruff 与 diff-check 通过。

本次关闭的是“待整理脸写入 → 单独收录人脸列表加载/候选分组头像渲染”缺陷。合集人脸移出、折叠后重开、原图/crop 与忽略状态、搜索/时间轴的剩余浏览器交互仍 **PENDING**；B 与 Fast Organize 整体不标 PASS。早前隔离服务的一次 401 为另一会话诊断结果；本次新会话明确复现了当前 `CLIENT_ERROR` 的独立前端根因。

### 用户复测：跨合集旧脸残留与默认展示（2026-10-16）

根因在合集切换时的页面状态：旧人脸卡片一直留在 DOM，且全局 `collectionFacesLoad` busy 标记会挡住新合集的人脸请求。旧请求虽然会被 token 检查丢弃，但旧卡片没有同步清除，于是页面仍显示前一个合集的头像。后端 `face_members_page()` 已按 `collection_id` 过滤；新增集成用例验证两个合集分别只返回各自收录的 `face_id`。

前端切换合集时现在递增人脸请求序号、解除旧请求的 busy 标记、清空旧分组/脸卡/分页状态；旧响应不能覆盖新合集状态。候选标题从可折叠 `<details>` 改为常显分组标题。用户指出仍要打开“单独收录的人脸”外层区域，因此进入合集“人物”页时现自动展开并加载人脸。脚本 cache-bust 更新为 `p4-person-fast-organize-a-9`。

使用 `.tmp/P4-PERSON-FAST-ORGANIZE-A/browser/fixture7` 隔离合成数据和本机 `127.0.0.1:18935` 内置浏览器验证：合集甲显示“甲候选”及脸 A，合集乙显示“乙候选”及脸 B；两者头像 URL 的 face ID 不同，图片均成功加载。人脸区在进入“人物”页后 `open=true`，无需打开候选分组即可看到头像。浏览器截图：[collection-switch-face-default-browser.png](artifacts/P4-PERSON-FAST-ORGANIZE-B-collection-switch-face-default-browser.png)。

定向 JUnit **3 passed、0 failed、0 errors、0 skipped**：[collection-switch-and-face-layout.xml](artifacts/P4-PERSON-FAST-ORGANIZE-B-collection-switch-and-face-layout.xml)。包含 UI 竞态/常显分组回归、现有静态收录边界，以及新增双合集隔离断言。`node --check`、Ruff 和 `git diff --check` 通过。以上关闭用户报告的跨合集串脸与多层手动展开问题；B 任务卡中其余移出、原图/crop、忽略状态以及搜索/时间轴浏览器路径仍 **PENDING**，B 及 Fast Organize 整体保持验收部分完成。

执行命令：

```powershell
.venv\Scripts\python.exe -m pytest -q `
  tests/unit/test_p4_person_collection_count_ui.py `
  tests/integration/test_p4_person_collections.py::test_face_members_are_static_soft_anchors_and_ignore_state_is_independent `
  tests/integration/test_p4_person_collections.py::test_face_members_page_never_mixes_collections `
  --junitxml=docs/reports/artifacts/P4-PERSON-FAST-ORGANIZE-B-collection-switch-and-face-layout.xml `
  --basetemp=.pytest-tmp/P4-PERSON-FAST-ORGANIZE-B-collection-switch
node --check src/photo_search/web/persons-albums.js
.venv\Scripts\python.exe -m ruff check tests/unit/test_p4_person_collection_count_ui.py tests/integration/test_p4_person_collections.py
git diff --check
```

浏览器路线为 `/persons` →“查看合集”→ 分别打开合集甲/合集乙 →“人物”。两次页面均默认展开脸区，face card 分组标题直接可见且头像 `naturalWidth=154`；两个合集标题与 face thumbnail URL 不同。独立合成库在 `.tmp/P4-PERSON-FAST-ORGANIZE-A/browser/fixture7`，隔离服务已停止。

### 用户反馈：单独收录人脸卡片文案精简（2026-10-16）

“单独收录的人脸”中的每张脸都是合集独立收录的 `face_id`，但“建议归入”只适用于有候选人的脸；也可能是已归属人物，或没有候选/归属。现在建议归属名称只在候选分组标题显示一次，并移除重复的“仅当前收录脸”“只包含这张脸所在的照片”卡片文案。真实的“已归属 {name}”标签和非当前脸的重新关联/来源/内容状态提示仍保留；卡片最小高度同时收紧。

用隔离 fixture7 的内置浏览器打开 `/persons` → 合集甲 →“人物”复核：分组标题显示一次“建议归入 甲候选”，头像卡片无重复标题及照片范围提示，图片加载成功（naturalWidth=154），卡片计算高度 145px、min-height 0。截图：[face-label-declutter-browser.png](artifacts/P4-PERSON-FAST-ORGANIZE-B-face-label-declutter-browser.png)。UI JUnit **1 passed**：[face-label-declutter.xml](artifacts/P4-PERSON-FAST-ORGANIZE-B-face-label-declutter.xml)；Node、Ruff、`git diff --check` 通过。

用户随后复测仍看到旧通用文案。只读请求当前运行服务的无数据 `/persons` 页面和版本化脚本（127.0.0.1:8000）均为 HTTP 200，页面引用 `a-4`，脚本含新 401 文案和成员说明；未访问任何人物/照片 API 或图库数据。因此当前打开的页面可能仍持有旧脚本，或触发了无 API 状态码的前端异常。未分类的页面处理异常已改为显示 `CLIENT_ERROR`，资源版本升至 `a-5`。需完全重启本地图库并在同一个浏览器用新的单次链接打开、强制刷新；若仍复现，应采集新文案附带的错误码/请求编号。未重试超时浏览器自动化。

### 用户反馈：单独收录人脸竖向堆叠（2026-10-16）

根因是候选脸此前按 `candidate.person_id` 建全宽分组。多个不同人物都显示为“未命名的人”，虽然候选 ID 不同、标签相同，却各自占一整行；因此合集里会出现一张脸一行的竖向堆叠。并非所有脸都属于同一候选，列表也可能有已归属或无候选的脸。

已取消候选分组容器，让所有单独收录的脸卡直接进入同一个响应式网格；缩小头像和卡片留白。候选名只保留在卡片悬停提示及辅助标签中，页面说明仅出现一次，明确建议不会自动更改归属。

使用 `.tmp/P4-PERSON-FAST-ORGANIZE-A/browser/fixture8` 的 8 张合成脸、`127.0.0.1:18937` 新会话在内置浏览器打开 `/persons` → 合集“多脸布局检查”→“人物”。页面显示 8 张卡片在同一个脸网格中（当前视口 7+1 排列），未按候选 ID 分成整行区块；截图：[face-gallery-layout-browser.png](artifacts/P4-PERSON-FAST-ORGANIZE-B-face-gallery-layout-browser.png)。浏览器交互与视觉检查通过。

定向 UI JUnit **1 passed**：[face-gallery-layout.xml](artifacts/P4-PERSON-FAST-ORGANIZE-B-face-gallery-layout.xml)。`node --check`、Ruff、`git diff --check` 通过。其余原卡中的人脸移出、原图/crop、忽略状态和合集搜索/时间轴路径仍为 **PENDING**，本次不改变 B 的部分验收状态。


### 用户反馈：人脸头像悬停提示可点击（2026-10-16）

“单独收录的人脸”卡片由点击打开原照片或进行管理选择，但原来是 article 元素，未设置鼠标指针样式，悬停时看起来不像可点击目标。现为脸卡设置手型鼠标，并在悬停时改变边框和背景；CSS 资源版本升至 a-3。定向 UI JUnit 增加悬停样式断言。


### 用户补充：只保留手型鼠标并隐藏人物页的人脸菜单（2026-10-16）

按用户要求移除脸卡悬停边框/背景高亮，仅保留 `cursor: pointer`，CSS 资源版本升至 a-4。继续检查合集成员导航发现 `openAlbum()` 未隐藏 `collection-face-section`，导致从合集进入人物相册时沿用合集页的人脸下拉区；现进入人物相册时明确隐藏该合集专属区域；返回合集时再恢复并展开。UI 回归覆盖往返状态。

在隔离 fixture8 内置浏览器重新打开合集“人物”页，合集人脸区域仍显示且 8 张脸正常加载；服务日志确认新版 CSS 请求 HTTP 200。该夹具合集没有人物成员（0 人物），因此具体人物相册的页面切换由定向 UI harness 覆盖，断言进入后隐藏、返回合集后恢复并展开。定向 JUnit **1 passed**：[face-navigation-hide.xml](artifacts/P4-PERSON-FAST-ORGANIZE-B-face-navigation-hide.xml)；Node、Ruff、`git diff --check` 通过。
