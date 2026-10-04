# 首页新增图库建库排队小修（2026-10-03）

基线 `dde350d`。用户要求“点击创建图库后就进入队列，不需要别的图库处理完才能进行；简单修一下、简单测试”。作为 P4-HOME-UX-A 的授权补充，结论：**本次小修定向验证通过**；原首页/搜索任务其他待验收项不追认 PASS。

## 原因与修改

首页 `renderSourceDetail` 在任一刷新运行时禁用了全部来源管理按钮。后端 `LibraryRefresher.start` 同时将所有新请求当作当前任务的重复请求，返回 `accepted=false`，没有待处理队列。

- `mode=build` 且指定来源的新请求可以立即进入进程内 FIFO；当前任务结束（包括失败）后，同一个 worker 自动执行下一个建库。每轮独立打开/关闭服务，SQLite 连接仍在 worker 内创建和销毁；交接在同一锁内完成，没有对外的空闲间隙。
- 相同建库 scope 先排序去重；与活动/等待 scope 相同的点击复用已有任务。日常刷新和未指定来源的全库请求保持原来的活动任务复用行为。
- HTTP 202 状态仍描述当前活动任务，加性返回 `queued`（本次请求是否在等待）和 `pending_builds`（按顺序排列的来源 scope）。新入队返回 `accepted=true, queued=true`；重复等待请求返回 `accepted=false, queued=true`。GET 的 `queued=false`，等待列表仍可见。
- 首页其他来源的开始/继续建库按钮仍可点击；等待来源显示“已排队”，当前建库来源显示“正在建库”。删除/完整扫描/快速刷新在后台任务运行期间仍禁用。状态轮询更新已经展开的来源按钮，刷新页面后读取已有活动/排队状态。
- 轮询用 `running` 判断是否继续，避免前一轮终态与队列交接时提前停止。入队请求失败仍恢复对活动任务的轮询。首页脚本版本更新到 `p4-source-build-queue-a-1`，绕开旧脚本缓存。

范围仅为后台入口、响应 DTO、首页、直接测试；未改流水线、schema、设备/provider、持久任务提交或原图处理。未访问真实图库/真实 PhotoSearchData，未重启现有应用。

## 简单验证

Python 3.12.10，现有 uv 环境，无依赖变更。最终命令：

```powershell
$env:UV_CACHE_DIR = Join-Path $PWD '.uv-cache'
uv run --no-sync pytest -q tests/unit/test_api_refresher_queue.py tests/unit/test_api_frontend.py tests/integration/test_p4_02y_library_refresh.py tests/integration/test_p4_02y_i_source_build.py --junitxml=test-results/p4-source-build-queue.xml --basetemp=.pytest-tmp/p4-source-build-queue-final
uv run --no-sync ruff check src tests
node --check src/photo_search/web/app.js
git diff --check
```

JUnit：**139 tests / 0 failures / 0 errors / 0 skipped**，18.337 s，退出码 0。Ruff、Node 语法、diff-check 退出码均为 0。产物 `test-results/p4-source-build-queue.xml`。

新增验证覆盖：活动建库期间来源 2/3 按 FIFO 等待；重复点击去重；未释放第一个任务前其他任务不执行；所有任务同一 worker 串行执行且先关闭上一轮服务；失败不阻塞后续；关闭后清空等待且拒绝新请求。HTTP 用小型合成库证明“日常刷新进行中 → 新增图库 → 点击建库立即 202 入队 → 自动开始”。沿用直接相关建库/删除/日常刷新回归。

前端行为检查作为 pytest 用例纳入同一 JUnit：Node VM 执行实际完整 `app.js`，只抑制 boot 并暴露渲染入口，用最小 DOM 按钮替身验证“其他来源可点击 → 已排队 → 正在建库 → 完成恢复”；删除及全局刷新按钮同时保持正确禁用/恢复。未执行本轮真实浏览器或真实图库验证。

首次沙箱 pytest 因 `.pytest-tmp` 创建目录 `WinError 5` 产生 40 个环境错误；按仓库规则正常权限重跑通过。最终增加前端行为回归、更新缓存版本后再次执行上述定向测试；未改 ACL，未把收集成功或无输出当成通过。

## 使用与边界

重启应用并刷新首页使源码修复生效；无需重建已有图库。等待队列与现有刷新显示状态一样保存在进程内：关闭/重启会清空尚未执行的请求，需再次点击建库，已完成的持久进度保留。关停不会自动扫描其他排队图库。未实现跨重启的持久队列、排队取消、并行模型处理，也未运行全量/容量/GPU/打包测试。

本次反馈闭环结束，不生成新的优化卡；继续个人使用反馈。其他待审任务原状态保持。
