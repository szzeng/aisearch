# P4-PERSON-FAST-ORGANIZE-B — 人物快捷整理验收闭环

- 日期：2026-10-15；状态：**部分验收完成（SOURCE_CROP_PASS、SEARCH_TIMELINE_PASS、BROWSER_PENDING；2026-10-16）**。
- 前置核对结果：开工时工作区干净，A 的实现已包含在 `b389f8d`，Schema v32；未假设未提交改动仍存在。浏览器使用隔离合成库；最终写入确认处复现 CUA/CDP 超时，按下方规则停止重试。[验收报告](../reports/P4-PERSON-FAST-ORGANIZE-B.md)
- 推荐：GPT-6 High；任务卡执行期间保持同一模型。需要独立复核时按当前用户指定由子代理使用 Luna High；不得把 LUNA 复核称作 DS-H PASS。
- 目的：关闭 A 的浏览器和新版 source-crop 验收缺口，并补齐共享合集过滤在搜索/时间轴上的定向证据。不重做 A 已通过的迁移和合集 API 套件。

## 1. 读取范围

按 AGENTS.md → docs/development-status.md → A 报告与本卡 → 受影响接口/代码/测试的顺序读取。再读取 V3.0/V3.1 与人脸重检测 ADR 中 A 报告和调用链点名的相关章节；不因新开 B 会话重读两份架构全文。

重点代码：

- persons/collections.py、persons/collection_sql.py、persons-albums.js、API routes 与已有 migration 0032。
- tests/integration/test_p4_person_collections.py、test_p4_person_match_b.py 和对应 UI/API 测试。
- source-crop worker 与当前实际 source-detail/source-crop 处理调用链（执行时先检查实现和 fixture）。

## 2. 必须完成

### 浏览器（Codex 内置浏览器）

- 只用本仓库 .tmp 下隔离合成数据、127.0.0.1 本地服务和新鲜的一次性会话链接；不访问 PhotoSearchData，不用外部浏览器/driver。
- 一次完成候选组展开 → 明确选择当前组 face_id → 确认加入已有测试合集 → 浏览器确认接口结果。
- 打开合集并验证静态脸出现在“单独收录的人脸”区域、卡片打开原照片并显示对应 face crop；折叠/再展开，移出脸后确认集合脸数变化且忽略状态不变。
- 验证合集照片去重、照片计数；经过合集限定搜索和时间轴时，静态收录照片与动态 person member 结果口径一致。限定检查受影响路由，不做视觉/性能全面认证。
- 若 Codex CUA/CDP 重现此前 Emulation.setFocusEmulationEnabled 超时，只记录错误、当前路线和写入状态并停止重试；确保没有“看起来提交”但后端未写的误报。截图仅在浏览器支持真实导出时保存。

### 来源 A→B

- 使用现有小型合成夹具走当前支持的 source-crop 图片解码/人脸文档写入、候选发布、索引和 reconcile 调用链，验证 A 的手工 CONFIRMED 人物 P 与 person member 在合集 C 中，B 的合格无歧义新脸归 P 且 C 的照片数扩展。
- 负向覆盖 TEMPORARY 可作参考、EXCLUDED 不作普通候选，以及仅静态 face member 不扩展 B。
- 不下载大模型、不启动全库识别。若当前环境缺模型/硬件而无法真实执行 worker，应明确标记 SOURCE_CROP_PENDING，并记录被执行的准确处理边界；合成 FaceDocument + 直接 reconcile 不等于该 worker 验收。

### 重检测展示边界

- 保留 A 中可信唯一 HIGH + CONFIRMED 映射、OPEN/AMBIGUOUS 和多条确认候选的单测。
- 若浏览器/新 source-crop 发现新问题，只修本闭环直接相关的最小差异并补一个定向用例；不要重构 mapping protocol。

## 3. 验收与报告

- 运行只覆盖新增/受影响测试的 pytest，必须写 JUnit XML 到 docs/reports/artifacts/。
- 运行受影响 JS 的 node --check、受影响 Python 的 Ruff 和 git diff --check。不得运行全仓、30 万规模、GPU、性能、打包或发行验证。
- 报告分别给出 API/浏览器交互/截图/视觉/source-crop 状态。任何未执行项保持 PENDING；不得把静态或 synthetic FaceDocument 路径写成完整 source-crop PASS。
- 完成后更新 A 报告、docs/development-status.md 和本卡状态；A 与 B 都完全闭环后才可将 Fast Organize 整体标为 PASS。

## 4. 安全边界

- 不读、不扫描、不改、不移动、不删除真实照片或 PhotoSearchData。
- 不迁移真实 catalog；不提交或推送，除非用户在后续明确授权。
- 不新增依赖、模型、向量后端或后台聚类。
- 失败时先定位环境或产品原因，不通过 ACL 修改或浏览器鉴权绕过来继续。

## 5. 本轮结果与未完成项

- 定向 JUnit 为 4 passed、0 failed、0 errors、0 skipped；Ruff 与 `git diff --check` 通过。结果及命令见 [B 验收报告](../reports/P4-PERSON-FAST-ORGANIZE-B.md)。
- Source-crop 新版解码/裁脸、FaceDocument 持久化、候选索引、reconcile 及动态/静态合集边界已用合成夹具通过；模型检测和 embedding 使用确定性替身。
- 合集限定搜索与时间轴已验证动态 person member 和静态 face member 的统一、去重资产范围。
- 浏览器认证、明确选择两张当前组 face_id 和合集选择已观察；加入请求返回 401（此前合集 GET 为 200），合成数据库确认无写入（0 face members，revision 1）。随后 CUA/CDP 超时，按规则停止交互并关闭服务；未重试。原因待区分浏览器自动化环境丢失 Cookie 与用户运行态会话问题。浏览器成功回显、单脸区域展开/再折叠、原图/crop、移出计数与忽略状态仍 **PENDING**；见验收报告追加诊断。
- 已修正人物页 API 错误展示：网络失败、无结构 404、非 JSON 响应显示可读原因/状态；401 指引重启服务并使用新一次性链接，403 保留拒绝码；展示可用的请求编号。合集说明文案区分人物页与单独收录的人脸，未知页面处理异常显示 `CLIENT_ERROR`，脚本 URL cache-bust 升至 `p4-person-fast-organize-a-5`。定向 UI/API pytest 3 passed，Luna High 限定复核无 finding，JUnit、Node/Ruff/diff 证据见报告。该改动未绕过鉴权；浏览器写入闭环仍 **PENDING**。
- 根因核对：合集人物是动态 `person_id` 成员，显示于“人物”页；“单独收录的人脸”仅读独立 `face_id` 归档。待整理人脸的隔离浏览器 POST 返回 401，face-member 行为 0，故没有保存记录可展示；错误提示现指引恢复新鲜本地会话。页面已标明两种内容的展示位置，浏览器复核保持 **PENDING**。
- 用户复测仍见旧通用提示；当前运行服务的无数据页面与 a-4 脚本只读核验均为 200，提示当前页面可能仍持有旧脚本或出现无 API 状态的前端异常。后续应完全重启应用，在同一浏览器打开新单次链接并强制刷新；自动化浏览器仍受超时限制，不重试。
- 本卡不标 PASS；Fast Organize 整体也不标 PASS。关闭本卡仍需在允许的后续浏览器会话中完成上述浏览器验收。

### 用户授权后续浏览器复测（2026-10-16）

- 使用新 `.tmp` 合成库和一次性会话。待整理候选组选择 2 张脸后，POST 返回 200；catalog 为 2 个 face members、revision 2。合集单独收录人脸 GET 也返回 200，页面复现 `CLIENT_ERROR`。
- 根因是候选分组 map 存 `{group, members}`，渲染时却访问 DOM 节点 `.members`。修复后升脚本缓存版本至 a-6；同一浏览器刷新，显示两张脸卡，展开候选组后两张头像均已加载。按钮调用曾超时，但服务端写入与页面计数已确认，不重试提交。
- 新增候选人脸分组渲染回归，定向 JUnit **1 passed**；Node、Ruff、diff-check 通过。真实浏览器截图、证据和根因详见 B 报告追加小节。脸卡移出、原图/crop、忽略状态和合集搜索/时间轴交互仍待浏览器验收，因此 B 及 Fast Organize 整体仍不标 PASS。

### 用户复测：合集串脸与人脸默认展开（2026-10-16）

- 根因是切换合集时旧脸卡仍留在 DOM，同时全局脸列表 busy 状态会挡住新合集读取；旧响应虽被 token 检查丢弃，已显示的旧脸却未清除。后端接口按 collection_id 查询，新增双合集集成断言确认 face_id 不串合集。
- 切换时现作废旧请求序号并重置加载/分页/分组状态；候选分组标题常显，合集“人物”页激活时自动展开“单独收录的人脸”并加载头像。脚本资源版本 `p4-person-fast-organize-a-9`。
- 内置浏览器使用 `.tmp` 合成数据验证合集甲/乙分别显示不同候选和 face_id；外层人脸区打开状态为 true、头像加载成功。报告、截图和 JUnit 见 [B 验收报告](../reports/P4-PERSON-FAST-ORGANIZE-B.md)。本轮定向 JUnit **3 passed**，Node、Ruff、diff-check 通过。上述用户问题已关闭；原卡其余浏览器验收项仍 PENDING，B 与 Fast Organize 整体不标 PASS。

### 用户反馈：人脸卡片重复文案（2026-10-16）

- 候选名现在仅保留在候选分组标题；移除每张当前人脸卡上的重复建议标题和“只包含这张脸所在的照片”。保留实际“已归属 {name}”标记及非当前脸状态提示，并缩短卡片最小高度。
- 回答数据语义：这里所有脸均为合集单独收录；不保证都有建议候选。分别可能有候选建议、已归属人物，或两者皆无。
- 隔离浏览器确认分组名只出现一次、头像卡无重复标题/提示且图像加载成功。UI JUnit **1 passed**，Node、Ruff、diff-check 通过。详见 B 报告及本次截图。

### 后续 UI 修正：单独收录人脸画廊（2026-10-16）

- 原因：候选分组按 person_id 各占整行；多个不同的未命名人物拥有相同显示名，造成一脸一行的视觉效果。
- 已改为统一响应式网格，卡片直接加入网格；建议名保留在辅助标签/悬停提示，说明文案只出现一次。
- 内置浏览器使用 8 张合成脸确认显示为同一画廊（7+1 响应式排列），截图与定向 JUnit 见 [B 验收报告](../reports/P4-PERSON-FAST-ORGANIZE-B.md)。UI JUnit **1 passed**，Node、Ruff、diff-check 通过。
- 该显示问题已关闭；原卡其他浏览器路径保持 PENDING，B 整体仍为部分验收完成。


### 后续 UI 修正：人脸卡点击反馈（2026-10-16）

- 脸卡点击原照片或进入管理选择时，悬停显示手型指针，并通过边框和背景强调可点击状态。CSS cache-bust 升至 a-3。
- 定向 UI JUnit、浏览器加载检查记录见 [B 验收报告](../reports/P4-PERSON-FAST-ORGANIZE-B.md)。


### 用户补充：悬停效果和合集人物页面状态（2026-10-16）

- 按要求保留手型指针、移除悬停底框/背景；CSS cache-bust 升至 a-4。
- 修复 `openAlbum()` 未隐藏合集专属“单独收录的人脸”区域的问题；从合集成员打开人物相册时隐藏该下拉区，返回合集后恢复并展开。UI 回归覆盖往返状态。
