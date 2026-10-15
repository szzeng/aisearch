# P4-PERSON-FAST-ORGANIZE-B — 人物快捷整理验收闭环

- 日期：2026-10-15；状态：**待执行**。
- 前置：P4-PERSON-FAST-ORGANIZE-A 的实现仍在工作区，尚未提交。开工先核对 Git 状态、A 报告、Schema v32 和相关测试；不要假设其他会话已保留这些改动。
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
