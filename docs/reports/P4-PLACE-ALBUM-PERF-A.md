# P4-PLACE-ALBUM-PERF-A — 执行报告

日期：2026-10-11。状态：**PARTIAL — 地点列表与封面预生成已实现并定向验证；真实冷首屏收益待用户重启实测**。

## 基线与改动

- 起始 HEAD：`f05464c`；开始时工作区干净。
- `_album_rows` 保留原有效成员 SQL、人工优先、自动候选限制和排序。相册按城市批量读取父级名称及活动别名，并用集合去重；封面仍取有序成员的第一张。
- 非 CN `album_assets` 改为一次成员行集遍历同时取得选中相册成员，维持既有 offset cursor、总数、48 张默认页和 catalog/suggestion/package revision 拒绝契约。
- CN 分组和分页继续委托 `cn_facets`，未更改 CN 归属规则。
- 地点相册脚本先执行，首个相册请求先于 `places.js` 的能力请求。目录、地名包状态、操作历史改为进入视口或锚点直达后读取；支持 IntersectionObserver 不可用时的加载回退，成功加载的区块不会因重复进入而自动重读。显式刷新和既有写入后的刷新仍工作。
- 首四张相册封面使用 eager/high 优先级，其余 lazy。静态脚本版本已更新。深链接复用相册列表请求。

## 合成测量

同一合成场景为 20 张成员的非 CN 城市相册；时间为单次本机采样，仅作定位数据，不是性能门槛。缓存冷读通过清空进程内 `_INDEX_CACHE` 触发包校验，热读复用该缓存。

| 路径 | 当前测量 | SQL 观察 |
|---|---:|---|
| 相册列表，索引冷缓存 | 3.20 ms / 26 SQL | 父级名称 1 次，别名 1 次 |
| 相册列表，索引热缓存 | 1.31 ms / 26 SQL | 父级名称 1 次，别名 1 次 |
| 地名包状态投影 | 1.50 ms / 4 SQL | 包状态包含磁盘有效性验证 |
| 非 CN 照片页，limit=7 | 1.18 ms / 16 SQL | 成员主查询 1 次 |

优化前未在本次执行前运行对照计时；从旧实现可精确读出结构基线：20 行同城成员会执行 40 次逐行父级/别名 SELECT，且照片页先构造完整相册、再重复执行一次成员查询。现在父级/别名各 1 次，照片页成员主查询 1 次。总 SQL 数含版本读取等固定工作，不能视为同口径总查询降幅。

启动请求通过 Node DOM 桩确认相册列表首先发起；管理区延迟逻辑用实际 `setupDeferredPanels` 函数测试了未进入视口时不请求、进入视口只加载一次、锚点触发加载及无 IntersectionObserver 回退。中国更新能力请求在相册列表返回后启动。

## 后续反馈与扩大定位

用户先反馈“正在整理地点相册…”和地图库加载提示等待约 **3–5 秒**；确认重启后地点列表变快，但卡片出来后封面仍需几秒，灰底显示在图像完成前。已核对封面 `GET /assets/{id}/thumbnail` 未命中缓存时需要读取、解码原图并生成缩略图；真实浏览器 Network 中各封面请求耗时及首次生成/缓存命中状态尚未取得。用户确认封面首次加载慢且与地名包状态刷新同时发生；临时将地名包状态改为显式加载后速度基本不变，故该状态请求并非主要瓶颈，已恢复“进入视口时加载”的原行为。

用户认为地点目录多余并询问能否关闭。该目录原先滚动进入视口时自动请求一页 `/places`；已改为手动读取：普通滚动不触发，点击“查询目录”或从“目录”锚点直达才加载，原目录/手工地点管理功能保留。地名包状态仍在进入视口时自动读取；手动延迟后用户实测无图片速度改善，因此恢复自动按需读取。

用户补充要求：地点相册按照片数（匹配数）降序；相同数量时按最近加入图库时间降序。普通地点使用 `assets.created_at` 聚合相册最新加入时间；CN 行政区的共同成员 SQL 同样投影该时间。封面首四张的 `fetchPriority=high` 现在在设置 `src` 前赋值。

为处理冷封面生成阻塞，将缩略图/预览缓存未命中时的媒体生成移到线程池，使用独立的线程拥有只读 SQLite 连接；同时限制最多两个媒体生成同时运行，避免首屏四张封面全部串行阻塞 API 事件循环。新增 API 响应性用例通过人为延迟生成验证事件循环仍能处理其他请求；它证明并发调度行为，不代表真实图片端到端时间已改善。

本轮定向 JUnit：`test-results/P4-PLACE-ALBUM-PERF-A-followup.xml`，**10 passed / 0 failures / 0 errors / 0 skipped**，覆盖冷缩略图 worker/API 响应性、普通/CN 相册、50 张分页及目录加载策略与暂停/继续 UI。Ruff、两个 JS `node --check` 和 `git diff --check` 通过。地点目录仍为显式加载；地名包状态恢复进入视口时加载。页面脚本版本已更新为 `p4-place-album-perf-a-2`。真实图库浏览器耗时仍待用户复测。

为检查数量级差异，另以临时合成 catalog 创建 10,000 个有效手工地点成员，未使用图片文件或 PhotoSearchData。地点列表 HTTP 首次 85 ms，热读 53 ms；地名包状态 HTTP 4.7 ms。该 probe 已移除，不属于产品测试门禁。它不能代表用户图库规模、实际地点包体积或磁盘性能，也未能解释其 3–5 秒等待。

最新改动已在应用启动及成功扫描后预热每类地点首屏封面。用户重启后应留出后台预热时间，再实测第一次打开与热刷新；若仍慢，需用浏览器 Network 对比各 `/thumbnail` 请求的 TTFB/总耗时，并结合预热完成日志逐图定位。不能绕开受控原图校验及失败记录。真实 `PhotoSearchData` 未访问。

## 验收

- JUnit：`test-results/P4-PLACE-ALBUM-PERF-A.xml`，**6 passed / 0 failures / 0 errors / 0 skipped**。
- 非 CN 50 张手工归属：48+1 分页无重无漏；增加成员后旧游标返回 409。
- CN 50 张合成成员：48+2 分页无重无漏。CN SQL/归属实现未改。
- 原有地点自动流测试保留别名筛选、人工指定/取消、GPS 边界、跨来源重复 asset 和结果对照；现有 CN UI 桩保留更新暂停/继续测试，并检查首四张封面优先级及快速切换旧响应保护。
- Ruff、`node --check`（`places.js`、`places-albums.js`）及 `git diff --check` 通过。

## 未验证与限制

- 真实浏览器冷/热速度、网络缓存/图片真实优先级和真实图库首屏体感：**NOT MEASURED**。
- 未访问 `PhotoSearchData` 或真实照片库；未运行全仓测试、30 万性能、GPU、打包验证。
- 列表仍在服务端构造完整地点成员行集；数据量极大时的整体 SQL/内存成本未测。未做虚拟滚动或大规模地点归属重写。
- 地名包完整性验证仍执行；本次单次合成状态投影约 1.50 ms，没有发现应削弱或重写校验的依据。

## 改动文件

- `src/photo_search/places/albums.py`
- `src/photo_search/api/context.py`、`routes.py`、`runner.py`、`media_generation.py`、`place_cover_warmer.py`
- `src/photo_search/app.py`
- `src/photo_search/web/places.js`
- `src/photo_search/web/places-albums.js`
- `src/photo_search/web/places.html`
- `tests/integration/test_p4_place_auto_flow.py`
- `tests/integration/test_p4_place_cn.py`、`test_api_files.py`
- `tests/unit/test_p4_place_cn_ui.py`、`test_place_cover_warmer.py`、`test_api_scan_runner_prewarm.py`、`test_media_generation_singleflight.py`

未提交；无 schema、migration、依赖或真实数据改动。

## 2026-10-11 最新用户复测

用户反馈当前首次进入时仅一张封面立即显示，其余仍需数秒；同一批封面显示过一次后再次刷新明显更快。结合 `/thumbnail` 的缓存契约和现有请求路径，这支持“已命中缩略图缓存的资产立即返回，冷资产首次请求同步等待其缩略图生成”的判断。先前线程池改动只解除事件循环阻塞并允许最多两个冷生成并发，不缩短单张图片读取/解码/缩放时间，故本次体验仍未解决。真实浏览器 Network 计时和逐资产缓存状态仍未取得，不能据此断言具体耗时来自磁盘、图像格式或解码器。

要让首次访问也立即有图，后续需要在用户打开地点相册页之前预生成/预热封面缩略图。把生成接入扫描、导入或后台队列会扩大原卡范围并可能改变扫描期间的 CPU/磁盘占用；在没有接受该代价前不擅自扩大。当前仍为 PARTIAL，实际冷图首屏 NOT MEASURED/未解决。

用户先选择保持现状，随后明确要求增加预生成；后续授权覆盖前一选择。


## 用户授权的范围扩展

用户在选择暂保持后，随后明确要求增加预生成。实现范围限定为应用启动及成功扫描队列排空后的后台暖缓存：城市、省份、区县、国家各取排序前 4 张封面，最多 16 个不同 asset；调用原有 `/thumbnail` 同一管线与同一 content-addressed 缩略图缓存，已发布 artifact 直接复用。使用单个后台 warmer 协调线程和最多两个并行解码 worker；扫描 worker 不做图片解码。关闭时清空未开始任务并有界等待当前任务。实际首屏收益待用户重启实测。


## 预生成实现与定向验证

`PlaceCoverWarmer` 在应用 lifespan 启动以及成功扫描请求队列排空后排队执行。每次只检查城市、省份、区县、国家各自按匹配数/最近加入时间排序的前 4 张封面，最多 16 个 asset；复用既有 `<data_root>/thumbnails/catalog-thumbnail@1/.../<sha256>.jpg` 内容寻址缓存，已有完整 artifact 由媒体管线直接复用。任务在单独 daemon worker 里运行，不阻塞扫描线程或 HTTP event loop；重复触发最多留一个后续 pass，关闭时清空未开始请求并有界等待。前台媒体请求与预热共享两路 semaphore 和 per-asset single-flight，避免相同资产重复解码。预热控制线程并行提交最多两个候选，缩短首屏城市封面排队时间。扫描仍只登记原图及 catalog 元数据；应用缩略图不是扫描阶段自动存在，本功能负责在启动/扫描后补齐首屏缓存。

新增/补充测试覆盖暖缓存类别和数量上限、重复触发合并、扫描队列排空后才触发、预热写入原缓存并重复执行不重写。最新定向 JUnit `test-results/P4-PLACE-ALBUM-PERF-A-prefill.xml`：**15 passed / 0 failures / 0 errors / 0 skipped**。Ruff、`py_compile`、`node --check`（两个地点脚本）、`git diff --check` 均通过。

本次实现还新增 `src/photo_search/api/media_generation.py`、`src/photo_search/api/place_cover_warmer.py`，并修改应用 lifespan、ApiContext 和 ScanRunner 以触发与回收 warmer；测试覆盖在 `tests/unit/test_place_cover_warmer.py`、`tests/unit/test_api_scan_runner_prewarm.py`、`tests/unit/test_media_generation_singleflight.py` 及 `tests/integration/test_api_files.py`。

本次只使用 synthetic fixtures，未读取 PhotoSearchData/真实图库。实际真实首屏冷启动收益、预热完成前用户立即打开页面的体验、后台 CPU/磁盘占用尚未测；用户需重启后验证。故任务保持 PARTIAL。
