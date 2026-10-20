# P4-RELEASE-050 — 0.5.0 发布执行报告

日期：2026-10-20
状态：**PUBLISHED — PERSONAL SOURCE BETA**（GitHub Pre-release）
基线：`5bf53eb5377e62a1d3c2bbc075080b102a0861a9`

依据就绪报告补齐当前 schema 数据安全、retention 浏览器操作和智能合集独立审查，再冻结并发布个人源码测试版。GitHub 已通过有权限账号核对：已有 0.4.0 正式 Release，尚无 0.5.0 tag/release。原始测试、审查和浏览器材料存入忽略的 `test-results/P4-RELEASE-050/`。

## 目标与实际改动

按用户授权发布 **Photo Search 0.5.0 — Personal Beta**，目标是 Windows 本机源码个人测试版。用户接受 Luna High 完整独立审查替代原 DS-H 路由，并明确个人项目允许说明轻微问题后放行。API v1、Schema 42、第三方依赖和原图处理规则没有调整。

- 应用、API/OpenAPI、pyproject、uv.lock 本地项目版本统一为 0.5.0，更新 README 与 CHANGELOG。
- 补充当前 Schema 的备份恢复回归及智能合集人工排除保护检查；提供两份开发浏览器合成 harness。智能 harness 复用本机已有隔离 synthetic 基线，首次 seed 需要其原始 fixture，不能声称该浏览器材料可直接在干净克隆复现。
- 发布回归发现真实地点兼容缺陷：兼容桥已审上限仍为 39。核对 0040–0042 均只修改 retention 数据后，将显式上限推进到 42；不修改 migration 或旧包内容，未来 43、过低上限、错误最低版本和损坏包继续拒绝。
- 其他回归修复限于测试：补 fake browser 的事件/SVG API 和 fake job 的 subject_type、迁移新增表/小型 outbox fixture；更新已过时的资源 token、文案、函数切片和含 favorites 的 digest oracle。保留原行为/数量/身份断言，未添加 skip/xfail 或降低生产校验。

## 数据安全与关键浏览器结果

1. **当前 Schema 恢复通过**：生产扫描的合成 schema 41 catalog 经 0042 迁移，schema 42 备份后恢复到空根。完整人物/人工 face link、地点归属、收藏、普通合集两成员、智能规则/候选/排除、artifact/outbox/index 行精确比较；真实小型 Lance 搜索 hits 值一致，源 JPG SHA-256 不变。篡改恢复副本导致校验拒绝，目标空且 staging 清理；不匹配 Schema 拒绝，不触发 migration。新增智能排除照片的清理入口拒绝并保留资产/人工排除，无 barrier/outbox 写入。
2. **Retention 关键操作通过，取消仍 PENDING**：127.0.0.1:18770，项目内隔离合成库，正常新一次性 session。真实页面启动后跨批显示已检查 20；首轮 65 项最终清理 63、保护收藏 1、注入失败 1。刷新和离开再返回可读终态；移除故障后再次点击产生新 run，检查 2、清理 1、保护 1、失败 0。最终只剩收藏资产 65 与 PRESENT 资产 66，64 个 COMPLETE barrier、0 ACTIVE、outbox 空，收藏与源文件 hash 不变。点击原生确认框时工具会超时但请求实际已执行；未观察到取消，不将其写为通过。confirmed=false API 零写入有回归覆盖；按用户个人测试版范围将该工具限制作为非阻断项接受。
3. **TEXT/COMBINED 交互通过（合成模型边界）**：127.0.0.1:18771，真实产品 UI/API 与生产刷新 runner/lease/CAS/publish；只在模型 wiring 注入确定性 3D fake encoder。TEXT 页面刷新后显示 4 个候选；COMBINED 注入一次 query 编码失败，页面明确失败并保留 4 张旧照片，DB build 未变；手动重试发布新 build、仍 4 张。页面如实提示“索引处理中”，不称真实模型/GPU 或 FTS 追赶验收。
4. **搜索即刻保存通过**：同一隔离服务搜索 photo，保存本次 4 条排名候选为普通合集，直接进入 4 张详情；打开并关闭共享查看器后返回同一合集。页面显示版本 0.5.0 / Schema 42。

原始核对：`retention-{running,before-retry,final-utf8,verification}.json`、`smart-{before-failure,after-failure,after-retry,verification}.json`；截图位于 `test-results/screenshots/release-050-{retention-final,smart-refresh-final}.jpg`。启动日志含一次性凭据，只留在忽略目录，不纳入 Git 或发布说明。

## 回归与复现

Python 3.12.10，既有 .venv；UV_CACHE_DIR=.uv-cache，TEMP/TMP 和数据根均隔离到项目内，pytest 唯一 basetemp/JUnit。完整原始基线为 `full-baseline.xml`：**3330 passed / 76 failed / 52 skipped，exit 1，688.233 秒**。基线失败没有删除或覆盖；其后按影响补测，不冒称最终版本单次全仓全绿。

| 验证 | 最终证据 | 结果 |
| --- | --- | --- |
| 版本/API、当前恢复、retention/storage | release-incremental-r2.xml | 59 passed，exit 0 |
| adapter/digest/返回/迁移与 3 个小型向量状态用例 | remaining-fixture-focused.xml | 56 passed，exit 0 |
| 受影响 Node/页面/共享查看器测试 | frontend-fixture-r3.xml | 81 passed，exit 0 |
| 人脸 fake job 全部受影响消费者 | fixture-integration-face-r1/junit.xml | 37 passed，exit 0 |
| 地点兼容、工具、恢复及搜索消费者 | fixture-integration-places-r2/junit.xml | 216 passed，exit 0 |
| 最后新增的 Schema 42 旧包不变检查及完整兼容矩阵 | fixture-integration-schema17-r3/junit.xml | 66 passed，exit 0 |

逐 node 对照保存在 `evidence-reconciliation.json`：原始 76 项失败中的 **75 项由通过的真实复测关闭**，仅 `test_cn_300k_counts_and_keyset_pages` 在同一兼容缺陷修复后未重跑，仍为本版范围外的容量 PENDING；52 项原始 skip 也未计入通过。这是同环境、未变实现的基线加受影响增量证据，既不称单次全仓 PASS，也不把范围外容量节点的通过从小夹具推出来。中间 fixture 迭代失败、原始 XML 与真实退出码全部保留。

最终 Ruff、Python 源码编译、共享 Node fixture 语法、`uv lock --check`（104 packages，无第三方依赖变更）与 `git diff --check` 通过。独立复核确认关键数据安全证据、版本和 39→42 产品兼容修复没有未决 blocker；本卡按用户明确范围放行个人源码测试版。

可复现命令：

```powershell
$env:UV_CACHE_DIR = "$PWD\.uv-cache"
$env:PYTHONUTF8 = "1"
$env:PYTHONPATH = "$PWD;$PWD\src"
# TEMP/TMP/PHOTO_SEARCH_DATA_ROOT 设为本任务项目内隔离目录，见 docs/testing.md。
.venv\Scripts\python.exe -m pytest -q tests/unit tests/integration --junitxml=test-results/P4-RELEASE-050/full-baseline.xml --basetemp=.pytest-tmp/release-050-full-baseline --durations=20
.venv\Scripts\python.exe -m pytest -q tests/integration/test_api_access.py tests/integration/test_release_050_data_safety.py tests/integration/test_p4_library_retention.py tests/integration/test_p4_storage_ui.py --junitxml=test-results/P4-RELEASE-050/release-incremental-r2.xml --basetemp=.pytest-tmp/release-050-incremental-r2
uv --cache-dir .uv-cache lock --check
```

上述文件名记录实际本轮命令；复跑时使用新 JUnit/basetemp 名称保留旧证据。未设置真实模型/GPU 实验开关。修复后的 30 万地点场景不在本版范围，不重复运行，也不从相同兼容错误的其他通过用例推断容量 PASS。

## 独立审查与已知限制

Luna High 完整智能合集主审覆盖所有直接变更、调用链、schema、source token、lease/CAS、队列/恢复、规则替换、排除/静态保存、UI 身份及原始证据；无已确认代码 blocker。另一次独立 Luna High 复核确认 39→42 兼容桥与迁移内容相符，包内容和人工事实校验未弱化。审查原始记录：`smart-full-review.md` 及同目录后续复核材料；不称 DS-H。

- 轻微已知限制：VACUUM 维护诊断未前后列出智能排除/缓存表计数；未发现丢行，后置处理。原生确认“取消”的浏览器验收受工具限制；不记 PASS。
- 仍未认证：完整多进程高压替换、真实 Qwen/EXIF v2 性能、完整 CR2 人脸/OCR/语义建库、30 万 SLA、GTX 1660、原生 200% 缩放、PyInstaller 与干净 Windows 离线安装。既有未验边缘项不追认为已通过。
- 本次没有读取、迁移或清理真实图库，没有下载模型或运行真实 GPU 实验。内置备份不含原图和模型权重；升级前独立备份，不能靠检出旧源码降级已升级数据库。

## 发布记录

已发布：[Photo Search 0.5.0 — Personal Beta](https://github.com/szzeng/aisearch/releases/tag/0.5.0)。GitHub 回读确认 `isDraft=false`、`isPrerelease=true`，发布时间为 2026-10-20 20:04（Asia/Shanghai）。提供 GitHub 自动源码归档，无 Windows 安装包附件。

- 冻结提交：`a669340c89df63d136a827f61f7b6ac9f6bc9556`，已推送 origin/master。
- Annotated Tag：`0.5.0`；Tag 对象 `3789f6f87f2321a8adfa57669034f335488b05f6`；远端 peeled SHA 与冻结提交相同。
- 既有 0.4.0 Tag/Release 未覆盖。此发布回执以单独文档提交跟在冻结提交之后，Tag 保持准确候选，不移动到回执提交。
- 后续进入个人试用与问题反馈；没有自动启动容量、最低设备、打包或新优化任务。
