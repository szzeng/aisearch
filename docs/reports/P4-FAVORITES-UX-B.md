# P4-FAVORITES-UX-B 执行报告

日期：2026-10-19
状态：PASS（受影响面验收；不代表全仓回归或容量验收）

## 2026-10-19 缩略图收藏图标视觉跟进

- 将原来仅用空心/实心字符区分的缩略图星标改为高对比状态：未收藏显示深色半透明圆底、白色描边星；已收藏显示琥珀色圆底、深色实心星及绿色勾选角标。按钮仍通过原 `aria-pressed`、`aria-label` 和 `title` 暴露状态与操作；键盘焦点、禁用态和减少动态效果均有样式处理。共享 helper 覆盖首页/收藏页/搜索/人物/地点/地图及时间轴，不改 viewer 独立收藏控件和写入流程。
- 五个直接加载该 helper 的照片页面同步刷新 JS/CSS 资源版本。单测校验页面资源版本、两态 SVG/CSS 与 aria 状态；Node 行为探针对可见收藏/未收藏按钮逐项核验图标 markup 与 `aria-pressed`。
- 验证：`pytest -q tests/unit/test_p4_favorites_ux_b_ui.py --junitxml=test-results/P4-FAVORITES-UX-B-icon-20261019.xml` 为 **4 passed**；`node --check`（helper/探针）、行为探针及 `git diff --check` 通过。JUnit 位于忽略目录。
- 内置浏览器未能完成视觉检查。既有隔离 synthetic fixture 的浏览器服务启动时报告 `SCHEMA_INCONSISTENT`（fixture schema_version 36 与当前迁移状态不一致）。启动器更新了该 fixture 下的合成会话文件 `.tmp/P4-FAVORITES-UX-B/PhotoSearchData/runtime/session.json`；Catalog 未能通过 schema 检查，浏览器流程未继续。未访问真实图库或重建 fixture；页面截图/真实缩略图叠放观感仍 PENDING。

## 交付

- 新增共享 `favorites-ui.js`，对当前根节点的照片星标批量读取状态，每批最多 100 个 ID；支持单张写入、同页状态更新、失效重读及新建缩略图沿用有效状态。异步响应只缓存仍连接在原页面根节点中的照片；DOM 回收时清除 revision 与值缓存。
- 首页列表、收藏页、搜索结果加入照片选择模式，支持单选、Shift 连选、当前项全选/反选/清空、500 张硬上限及显式目标态的加入/取消收藏。批处理失败保留选择；写入时锁定重复提交。普通时间轴、收藏列表、搜索、人物、地点和地图照片缩略图增加独立星标；时间轴使用合法兄弟控件结构。
- 收藏 API 增加原子批写与 `capture_desc` 有界分页。整批资格先校验，失败零部分写入；有效变化仅推进一次 favorites revision，全 no-op 不推进。拍摄排序仅信任 EXIF 原始/数字化时间，未知时间置尾，并按 asset_id 稳定排序。游标绑定排序与 revision；短形状及超出 SQLite int64 范围的游标返回 `INVALID_CURSOR` 422。

## 验证

- 最终受影响组合 JUnit：40 passed、0 failures、0 errors、0 skipped；文件：`test-results/P4-FAVORITES-UX-B-affected-20261019-final6.xml`。范围包括 B UI/API、收藏核心、照片页回归、地点深链与 viewer 相关检查。覆盖批次非法资格、500/501、幂等、revision 故障回滚、可信时间/未知时间、并列排序、成员变化 409、越界游标 422、索引查询计划和每页地点读取调用数。此为受影响组合验证，不是全仓回归。
- 行为探针通过：500 个显式选中 ID 在 DOM 回收后仍完整保留，尝试新增第 501 个被拒绝且旧选择不变；迟到的星标状态响应遇原节点回收后不写缓存，新节点会重新读取状态；201 个可见 ID 按 100/100/1 三批请求，分区 ID 逐项相等且无重无漏，全部 201 个按钮状态逐项正确。Luna High 独立复核确认上述探针对应真实行为断言且 Node exit 0。
- `.venv\Scripts\ruff.exe check` 对本任务 Python 源码/测试通过；Node `--check` 对 `favorites-ui.js`、`app.js`、`search.js`、`photo-timeline.js`、`map.js` 和行为探针通过；相关 `py_compile` 与 `git diff --check` 通过。
- Luna High 独立只读复核最终源码、API、完整受影响 UI/测试及资源版本：无剩余代码 finding。
- 内置浏览器使用 `.tmp/P4-FAVORITES-UX-B/PhotoSearchData` 隔离合成数据，端口 18849，确认新的一次性会话与首页 API。最终资源版本下实测：首页选择模式单选、Shift 连选、全选、反选、清空、批量加入/取消；查看器单张收藏变化返回后正确更新首页缩略图。切换到文字搜索后，实测搜索结果全选、批量加入及 viewer 单张变更回写结果星标。收藏页实测最近收藏与“按可信拍摄时间排列，未知时间置尾”切换。
- 最终 helper B3 通过 `.tmp/P4-FAVORITES-UX-B/map-browser-data` 隔离地图库在端口 18850 实测：地图显示 2 张带位置照片，单张星标从未收藏变为已收藏。另将仓库已有合成人物/地点夹具复制到 `.tmp/P4-FAVORITES-UX-B/people-place-browser-data`，端口 18851 实测人物照片页与地点相册各 1 张照片的星标状态更新。
- 同一隔离人物/地点夹具注入一次性 revision 更新故障，浏览器批量收藏如实显示失败并保留 1 张选中项；Escape 退出选择模式并清空选择，打开 viewer 时 Escape 关闭 viewer。故障 trigger 已移除，asset 2 收藏行数为 0。浏览器服务已停止。
- 浏览器截图导出能力不可用，未保存截图。页面流程以实际浏览器交互为证；500/501、回收竞态与大于单批状态查询的行为以 Node DOM 探针为证。

## 边界

未访问真实 `PhotoSearchData`，未运行模型/GPU、全仓回归或 30 万图片性能测试，也不据小型合成库推断延迟或容量保证。并行 CR2 与 viewer 工作树改动保留，未纳入本报告审查或提交。

## 2026-10-19 专项复核：收藏拍摄时间分页性能

### 瓶颈与方案

复核确认 `list_favorites()` 的原 `capture_desc` 查询在 `asset_favorites` 上强制 `INDEXED BY idx_asset_favorites_recent`，但最终排序依赖 `asset_metadata.taken_at_sort`。F=50,000 的合成计划显示强制该索引时扫描收藏覆盖索引、按主键探测资产和元数据，再执行 `USE TEMP B-TREE FOR ORDER BY`；去掉 `INDEXED BY` 后 SQLite 仍使用收藏表扫描与临时排序。该 hint 把原查询限制在收藏驱动计划，但移除 hint 本身没有消除排序成本。

采用低复杂度分段读取：EXIF_ORIGINAL 与 EXIF_DIGITIZED 各自使用已有 `idx_asset_metadata_capture(time_source,taken_at_sort,asset_id)` 和 tuple keyset 读取至多 P+1 行，再有界归并；不足一页时按 `asset_id DESC` 从收藏表补未知时间末尾。未知包含缺少 metadata、EXIF_ROOT、FILE_MTIME 等，仍不信任其排序时间。游标形状、收藏 revision、capture 查询的 catalog filter revision 检查均未改。每次只在 SQL 内有界读取/归并，不先 LIMIT 收藏候选后在 Python 重排，也不引入 OFFSET。

临时建立了部分索引候选 `asset_metadata(taken_at_sort DESC,asset_id DESC) WHERE time_source IN (EXIF_ORIGINAL,EXIF_DIGITIZED) AND taken_at_sort IS NOT NULL` 作对照，但没有迁移或保留它：50k 收藏 / 50k 资产时建索引 10.03ms、分配 199 个 4KiB 数据库页，首页中位数 0.120ms；50k 收藏 / 500k 资产时建索引 132.73ms、分配 2,189 页（约 8.55MiB），首页 0.145ms。对应不加该索引的分段查询首页约 0.246/0.329ms。它的边际收益不足以承担全体可信 metadata 的额外存储、写入维护、迁移与备份影响；因此不新增 schema 结构。

### 隔离测量

使用迁移后独立合成 Catalog，F=2k/10k/50k，收藏密度 F/N=100% 与 10%（对应 N=2k/10k/50k 和 N=20k/100k/500k），每页 50 张，依次量最近排序和 capture 排序第一页、第二页及按脚本中点选取的深页：2k 收藏测第 21 页，10k/50k 收藏测第 101 页；部分索引候选单独测第 1、2、51 页。拍摄时间/来源分布确定性生成：80% EXIF 原始、15% EXIF 数字化、3% EXIF_ROOT、2% FILE_MTIME；所有输出页与独立排序 oracle 逐 ID 比较。性能 Catalog 仅创建 assets、metadata、favorites，没有 file_locations，因此 photo_asset_sql 走零位置 legacy 分支；该数据形状不代表带已登记位置照片的专属计时。环境为 Python 3.12.10 / SQLite 3.49.1 / Windows 11；每个页面先执行 1 次预热，再记录 3 次 SQL 计时，并以每 1,000 VM opcode 的进度回调估算工作量；这是 SQLite 进程缓存暖读，不代表 OS 冷缓存或 P95 服务端延迟。

| F / N（密度） | 最近 first / second / deep | Capture 原 first / second / deep | Capture 新 first / second / deep |
|---|---:|---:|---:|
| 2k / 2k（100%） | 0.106 / 0.097 / 0.096ms | 0.890 / 0.968 / 0.906ms | 0.234 / 0.245 / 0.239ms |
| 2k / 20k（10%） | 0.080 / 0.081 / 0.082ms | 2.040 / 2.175 / 2.126ms | 0.308 / 0.364 / 0.350ms |
| 10k / 10k（100%） | 0.080 / 0.080 / 0.079ms | 4.016 / 4.416 / 4.184ms | 0.231 / 0.246 / 0.251ms |
| 10k / 100k（10%） | 0.109 / 0.090 / 0.089ms | 10.951 / 11.322 / 11.597ms | 0.328 / 0.362 / 0.350ms |
| 50k / 50k（100%） | 0.093 / 0.097 / 0.092ms | 25.377 / 28.058 / 26.318ms | 0.246 / 0.256 / 0.256ms |
| 50k / 500k（10%） | 0.100 / 0.098 / 0.092ms | 55.904 / 59.251 / 58.318ms | 0.329 / 0.343 / 0.346ms |

| F / N（密度） | Capture 原 VM 步数 first / second / deep | Capture 新 VM 步数 first / second / deep |
|---|---:|---:|
| 2k / 2k（100%） | 81k / 93k / 79k | 3k / 3k / 3k |
| 2k / 20k（10%） | 81k / 93k / 79k | 6k / 11k / 11k |
| 10k / 10k（100%） | 404k / 466k / 392k | 3k / 3k / 3k |
| 10k / 100k（10%） | 404k / 466k / 391k | 6k / 11k / 10k |
| 50k / 50k（100%） | 2,017k / 2,331k / 2,256k | 3k / 3k / 3k |
| 50k / 500k（10%） | 2,017k / 2,332k / 2,257k | 6k / 9k / 10k |

最近查询三种页位的 VM 估算均为约 1k 步（进度回调按 1k 步粒度累计）。

以上是本机单次运行环境下 3 样本中位数，不构成容量/延迟保证。最近排序仍执行原 favorites 索引与 keyset SQL；测得 first / second / deep 页位为 0.079–0.109ms。单张/批量写入、收藏范围搜索没有改 SQL、索引或 migration，因此不增加其写放大或额外读操作；它们的完整吞吐未作为本专项基准。

### 正确性与验证

- 新增 `test_capture_desc_pages_merge_trusted_sources_unknown_tail_and_stale_filter_cursor`，通过 API 连续翻页 173 个合成收藏，覆盖两个可信 EXIF 来源、重复时间、EXIF_ROOT、FILE_MTIME、无 metadata；结果与独立 oracle 完全一致且无重复，覆盖 known → unknown 边界和后续 unknown keyset 页。旧 cursor 形状及收藏 revision 拒绝逻辑保持原状；测试另在第一页游标生成后推进 catalog filter revision，并确认旧 capture 游标仍返回 409。
- 受影响定向测试：`tests/integration/test_p4_favorites_ux_b.py` 与 `tests/integration/test_p4_favorites_core.py`，8 passed；JUnit：`test-results/P4-FAVORITES-UX-B-capture-sort-targeted-20261019.xml`。Benchmark 六种 F/N 条件及临时部分索引页均通过 ID oracle。
- `EXPLAIN QUERY PLAN` 对旧强制/不强制查询均显示临时排序；新可信分源查询使用 `idx_asset_metadata_capture`，未知尾部扫描收藏表并按主键顺序输出，均无 `USE TEMP B-TREE FOR ORDER BY`。这些计划和暖读耗时复现脚本位于 `tests/performance/benchmark_favorites_capture_sort.py`，原始运行结果在忽略文件 `test-results/P4-FAVORITES-UX-B-capture-sort-20261019.json`。
- 验证命令：`$env:PYTHONPATH='.;src'; $env:UV_CACHE_DIR='.uv-cache'; $env:PYTHONDONTWRITEBYTECODE='1'; .\.venv\Scripts\python.exe tests/performance/benchmark_favorites_capture_sort.py --samples 3`；`pytest -q tests/integration/test_p4_favorites_ux_b.py tests/integration/test_p4_favorites_core.py --junitxml=...`。没有访问真实 `PhotoSearchData`，没有运行 30 万照片、全仓、模型/GPU 或完整服务端尾延迟测试。
