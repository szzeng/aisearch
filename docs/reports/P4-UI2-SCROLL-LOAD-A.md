# P4-UI2-SCROLL-LOAD-A 实施与验证报告

日期：2026-10-14
结论：**ACCEPTANCE PARTIAL / LUNA HIGH REVIEW COMPLETE**。搜索结果和普通照片列表已接入自动续页与有限 DOM 回收；定向 JUnit 38/0/0/0，Node 语法检查和 diff 检查通过。最终 cache-bust 版本在隔离合成产品浏览器确认搜索 FILTER_ONLY 由 20 张滚到 40 张、普通列表由 50 张滚到 87 张并显示到底。任务卡要求的语义/图片候选、多页长滚动浏览器证据及页大小/键盘边界尚未完整验证，因此不标记 SCOPED PASS。

## 实施范围

- `src/photo_search/web/scroll-pager.js`：为页面提供轻量 IntersectionObserver/滚动续页器；同一列表只允许一个请求；首次有限补页后等待用户滚动；没有 IntersectionObserver 时保留手动继续入口；可禁用、复位和释放监听。提供按完整网格行回收旧卡片的 helper，维护高度占位并移除回收缩略图的 `src`。
- `src/photo_search/web/scroll-pager.js`、`search.js`：支持保存和恢复回收占位高度；候选时间切换时以请求序号丢弃迟到页，并恢复相关度分页按钮、sentinel 和 observer 状态。搜索快照恢复累计数量、窗口偏移和回收计数；旧格式快照缺少占位高度时保留 trim 实算值。
- `src/photo_search/web/app.js`：仅普通列表连接已有 `#grid-sentinel` 与 `loadBrowse(false)`；查看器打开、列表隐藏、时间轴模式、加载中或无游标时不续页。普通列表网格上限 500 卡片，回收时提示较早内容已释放；从详情返回超过 5 页预算时回到顶部并说明需重新定位。
- `src/photo_search/web/search.js`：搜索结果独立 sentinel，共用当前普通搜索或候选按时间分支；保留原 cursor/session、页大小及排序协议。搜索结果和候选集 DOM 上限 500，回收会同步更新卡片状态/序号并提示旧卡已回收。
- `src/photo_search/web/index.html`、`search.html`：加载 helper 并更新资源 cache-bust；搜索结果页新增续页 sentinel。
- `tests/unit/test_p4_ui2_scroll_load_a.py`：覆盖单飞、有限首屏填充、失败重试、Observer 清理、无 Observer 按钮回退、30×50 合成卡片的 500 DOM 上限/占位，以及首页和搜索接线。

没有改 Python 生产代码、API、后端游标/session 协议、时间轴组件或真实图库。

## 验证结果

```powershell
node --check src/photo_search/web/scroll-pager.js
node --check src/photo_search/web/app.js
node --check src/photo_search/web/search.js
git diff --check

uv run --no-sync pytest -q tests/unit/test_p4_ui2_scroll_load_a.py tests/unit/test_p4_ui_photos.py tests/unit/test_p4_ui_search_entry.py tests/unit/test_search_person_album_link_ui.py tests/unit/test_p4_ui_timeline_core.py --basetemp=.pytest-tmp/P4-UI2-SCROLL-LOAD-A --junitxml=test-results/P4-UI2-SCROLL-LOAD-A.xml
```

结果：三个 `node --check` 与 `git diff --check` 通过；JUnit XML 实读 **38/0/0/0**。XML 位于 `test-results/P4-UI2-SCROLL-LOAD-A.xml`。30×50 是 JS/DOM harness 证据，最终 helper 的上限为 500 卡且仅保留完整行；它不是浏览器 30 页或大图库性能证据。

## 隔离合成产品浏览器

本机 `127.0.0.1:8000`，Codex 内置浏览器，使用本任务 `.tmp/P4-UI2-SCROLL-LOAD-A` 合成图片和隔离 PhotoSearchData；扫描报告 87 张有效唯一资产。未读取或改动真实 `PhotoSearchData`。启动器新鲜单次 session link 仅在本浏览器打开并验证了认证 API；没有记录其凭据。

- `/search`：确认无词查询明确标为 `FILTER_ONLY`，不是语义检索；新 cache-bust 版本首屏为 20/87。一次向下滚动后为 40/87，证明最终滚动预算修正确实每次用户滚动只续一批。
- `/`：默认时间轴切换到普通列表，首屏 50/87；一次向下滚动后追加到 87/87，状态为“已经到底”。该合成集合只有两批，不覆盖任务卡 120 张/至少三批条件。
- LUNA High 修复后的 `scroll-load-a-4` 资源版本再次实走以上搜索与普通列表路线，结果相同；此前搜索查看器往返不作为 v4 长页详情返回证据。
- 查看器打开/关闭返回搜索结果曾在本轮早先的同一实现版本中观察；该浏览器观察不覆盖详情返回重放深页或 500 卡回收后的真实锚点恢复。
- 浏览器工具没有提供将截图导出到报告 artifact 目录的路径，故未保存伪造/转存截图。
- 浏览器 `MODEL_DIR_MISSING`，语义与图搜不能成立；未以 FILTER_ONLY 结果代替。没有测量 API 并发峰值、20–30 页浏览器请求/缩略图量、200% 缩放或暗色模式。

## 验收矩阵

| 用例 | 状态 | 证据/缺口 |
| --- | --- | --- |
| A01 普通网格续页、过滤变化后续页 | 部分 | 浏览器 50→87 一次续页；仅 87 张，未覆盖 120 张三批、切来源/排序/日期之后续页。 |
| A02 文本 / FILTER_ONLY / 固定 scope | 部分 | 产品浏览器确认无词 FILTER_ONLY 的 20→40；文本语义与固定 scope 没有有效向量模型证据。 |
| A03 图搜与候选按时间 | PENDING | 缺少图像/语义编码器，未运行候选 session 跨页。 |
| A04 连续触发、请求乱序、切查询后迟到响应 | 部分 | helper 单飞和候选按时间切回时迟到响应由定向 Node harness 验证；没有隔离浏览器网络延迟/乱序实测普通查询代际。 |
| A05 失败、过期、revision stale、无 Observer | 部分 | harness 覆盖错误重试、Observer dispose 和无 Observer 手动按钮；页面级 session 过期/revision stale 及真实重试操作未覆盖。 |
| A06 20–30 批滚动、回收与锚点 | 部分 | JS/DOM harness 使用 30×50 输入并断言不超过 500 卡；无浏览器 20–30 批和回收后锚点往返证据。 |
| A07 预览/详情返回、切时间轴/列表 | 部分 | 有限搜索查看器往返已观察；没有验证长页/回收窗口锚点返回和隐藏列表不请求的完整序列。 |
| A08 页大小 1/100、窄窗口/键盘、短结果 | PENDING | 未验证。 |

## 仍待验

1. 使用至少 120 张合成照片，在首页切来源/排序/日期后实走 3 批；搜索实走多批并加一条可用查询/固定 scope 路线。
2. 覆盖真实页面请求延迟和乱序、查询代际变化、session 过期、revision stale、手动重试，以及查看器/时间轴切换后不再后台续页。
3. 在隔离产品浏览器滚动 20–30 批，观察最大并发、DOM 节点和缩略图请求，验证 500 卡回收后上滑/返回行为；补测页大小 1/100 和键盘滚动。
4. 无模型期间语义和图片浏览器路线保持 PENDING；真实模型/GPU、真实图库、`PERF_200K`、`CHECK_300K`、打包和发布均不属于本次执行。

## LUNA High 独立复核

用户指定在需要复核时使用 LUNA High。首轮发现候选按时间切回时加载状态/分页控件未恢复，以及详情返回快照丢失窗口偏移/占位高度。复核补查还发现新查询可能继承候选请求的 disabled 按钮、普通列表 stale catch 会让 pager 误判为真实失败，以及旧格式快照缺少 spacer 高度时会清除 trim 实算占位。已修复请求序号/迟到响应、按钮和 sentinel/pager 恢复、快照 offset/count/recycled/spacer 往返、新查询按钮复位、普通列表 stale 返回语义及旧快照兼容。

LUNA High 最终只读复核确认以上 findings 均关闭，无剩余 findings。该结论是有界 LUNA High 受影响面复核，不是 DS-H。候选切换行为已加入 Node 行为测试；没有对真实网络乱序、长页预览恢复或性能门作 PASS 声明。

没有提交或 push。
