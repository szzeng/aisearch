# P4-HOME-UX-A — 首页进入搜索页等待修复

日期：2026-10-03。范围：用户反馈“首页已快，但点击搜索图片仍需等很久”。本次只修页面进入链路，保持个人项目的定向、小样本验收；按用户要求本地提交，未推送。

## 原因与修改

首页原先在来源入口显示后继续调用完整 `/api/v1/capabilities`。该async路由同步计算scheduler/indexing/OCR/face/search覆盖统计；`_search_capabilities → ApiContext.query_encoder → encoder_factory → build_worker_query_encoder` 还可能启动AI worker并等待模型加载握手。服务事件循环因此可能在首页显示后被占用，连 `/search` 静态页请求也等待。这里证明的是代码调用链和受控复现，未测量用户真实数据根/模型的等待时长。

搜索页原先又调用完整capabilities、完整sources，并在能力请求结束后自动调用覆盖面板的四个接口，重复执行来源进度/全库统计。来源筛选及恢复搜索现场也随能力读取等待。

修复：

- 新增受原会话保护的 `GET /api/v1/capabilities/page`，只返回版本、schema状态和受控读取事实；使用自身只读连接核验schema，不解析模型或统计覆盖率。sync路由在线程池运行，不跨线程使用 `context.service()` 的SQLite连接。
- 首页和搜索页初始化改用该轻量接口；搜索来源筛选改用已有 `/sources/summary`。人物入口仍按相同schema门槛显示，不根据模型是否加载隐藏。
- 删除进入搜索页时自动读取覆盖率的调用；用户点击“读取覆盖率”后才请求完整能力、地点覆盖和统计，来源状态仍复用轻量摘要。
- 搜索表单不把尚未探测的模型误标成“没有编码器”，配置由实际搜索接口确认；已保存的搜索现场继续在轻量信息返回后恢复。更新脚本URL版本，避免沿用旧缓存。

原完整capabilities响应、模型资格/初始化验证、真实搜索/排序/分页及人工知识均未改变。其他并行人物、匹配阈值、搜索返回现场改动已保留。

## 定向验证

命令：

~~~powershell
$env:UV_CACHE_DIR = Join-Path $PWD '.uv-cache'
uv run --no-sync pytest -q tests/unit/test_api_frontend.py tests/integration/test_api_sources_assets.py tests/integration/test_api_access.py --junitxml=test-results/p4-search-entry-a.xml --basetemp=.pytest-tmp/p4-search-entry-a
uv run --no-sync ruff check src/photo_search/api/models.py src/photo_search/api/routes.py tests/integration/test_api_sources_assets.py tests/integration/test_api_access.py tests/unit/test_api_frontend.py
node --check src/photo_search/web/app.js
node --check src/photo_search/web/search.js
~~~

并行搜索返回修改落地后再次运行上述3文件，仍退出0。JUnit：**116 tests / 0 failures / 0 errors / 0 skipped**；pytest、Ruff、JS语法检查退出0。首次沙箱测试遇到已知 `.pytest-tmp` WinError5，同一命令走正常权限路线后通过，未修改ACL。新增回归以会话拒绝及模型/统计调用守卫证明轻量接口没有重工作；完整能力接口旧测试仍通过。

## 本地提交检查（2026-10-03）

只暂存页面进入修复，不含并行人物、Qwen门限、地名包恢复或搜索返回现场改动。隔离暂存快照的3份定向测试：JUnit **115/0/0/0**，退出0，`test-results/p4-search-entry-a-commit.xml`。相比工作区116项，排除了另一任务的搜索返回测试。首次快照测试114通过/1错误响应失败：仓库既有`models/`忽略规则排除了5个embedding Python源文件，导致旧完整能力接口缺少registry；仅从本机现有源码补入这5个运行前提后重跑通过，未添加模型文件或扩大本次提交范围。该快照检查仍依赖这些既有本机Python源文件。

命令：设置`PYTHONPATH=.tmp/p4-search-entry-commit/snapshot/src`、项目UV缓存后，`uv run --no-sync pytest -q .tmp/p4-search-entry-commit/snapshot/tests/unit/test_api_frontend.py .tmp/p4-search-entry-commit/snapshot/tests/integration/test_api_sources_assets.py .tmp/p4-search-entry-commit/snapshot/tests/integration/test_api_access.py --junitxml=test-results/p4-search-entry-a-commit.xml --basetemp=.pytest-tmp/p4-search-entry-a-commit-final`。本地提交，未push，原任务待核状态不变。

## 小型受控计时与浏览器

只使用项目 `.tmp/p4-search-entry-a` 的3个空合成图库，模型工厂用固定1秒延迟返回None，未读取真实照片、PhotoSearchData或模型。每次受控请求重新置为未解析状态，模拟模型冷准备：

| 样本 | 页面能力接口 ms | 完整能力接口 ms（含模拟1秒模型准备） |
|---|---:|---:|
| 1 | 17.18 | 1031.15 |
| 2 | 14.52 | 1028.66 |
| 3 | 22.44 | 1037.57 |

轻量接口工厂调用0次；完整接口3次。原始结果：[原始JSON](assets/p4-search-entry-a-timing.json)。这验证成本隔离，不代表真实模型/个人图库的耗时或倍率。

实际浏览器走完合成首页→点击“搜索图片”→表单与来源选项可用。服务端记录 `/search` 返回1.62ms、页面能力请求15.36ms、来源摘要0.42ms；这是HTTP处理时间，不能当作完整点击到可见耗时。初始化期间模型工厂调用0次，完整capabilities/sources/stats/places覆盖请求0次。随后显式点击“读取覆盖率”，四项覆盖卡显示成功，工厂才调用1次。请求日志：[原始JSON](assets/p4-search-entry-a-browser.json)。截图：[合成搜索页](assets/p4-search-entry-a.png)。隔离服务与临时标签在验收后停止/关闭。

## 结论与限制

**页面进入链路修复通过定向测试和合成浏览器验证**。本机应用需重启加载新路由，刷新首页/搜索页加载新脚本。若模型尚未准备，首次实际搜索仍会等待模型初始化；此次没有优化模型冷启动，也没有声称搜索推理变快。用户真实会话/真实图库搜索及P4-HOME-UX-A原有未验证项保持待核，不把总任务改成PASS。

## 暂存区整理与遗漏修正（2026-10-03）

整理基线 `cab19db`。暂存区曾删除 `b68165c` 已提交的搜索返回代码及实现报告，工作区已将它们恢复；此次仅取消这些旧暂存，不提交功能回退。`app.js` 和搜索返回实现报告与 HEAD 相同；前端测试唯一差异是函数位置移动，已核对内容完全相同并恢复原位置。

最终代码修正：删除搜索页初始化中的 `capabilitiesPromise.then(loadCoverage)`，让完整覆盖率请求只由显式点击触发，保留轻量能力/来源加载与 `restoreSearchSnapshot`。现有 `test_page_startup_avoids_model_probes_and_automatic_coverage` 已覆盖该约束；搜索返回的保存/恢复契约仍通过。另将高度匹配注释更新为按表征空间决定可用性与阈值，不改变统计、结果或排序。

状态文档保留地名包恢复、Qwen高度匹配及人物反馈的既有交接；修正行尾空白，文本使用 LF。本次不重新验证这些任务，也不更改它们的待审/待验收状态。未跟踪的 `.tmp` 和临时清理审计文件不纳入提交。

本次实际检查（全部退出 0）：

```powershell
$env:UV_CACHE_DIR = Join-Path $PWD '.uv-cache'
uv run --no-sync pytest -q tests/unit/test_api_frontend.py --junitxml=test-results/git-reconcile-search-entry-2026-10-03.xml --basetemp=.pytest-tmp/git-reconcile-search-entry-2026-10-03
uv run --no-sync ruff check tests/unit/test_api_frontend.py
node --check src/photo_search/web/search.js
git diff --check
```

JUnit **95 tests / 0 failures / 0 errors / 0 skipped**。此次只检查前端小修和 Git 整理；未运行浏览器验收、后端/全量测试、模型或真实图库操作。搜索返回浏览器验收与 HOME-UX-A 真实搜索待验证状态保留；本地提交，不 push。
