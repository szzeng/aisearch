# P4-PHOTO-RANDOM-BROWSE-TIME — 随机浏览时间来源修正

状态：**PLANNED / NOT IMPLEMENTED**。基线：`master` / `806cec1`（2026-11-25）。本卡只定义实施范围；尚未修改业务代码、运行测试或访问真实图库。

## 1. 目标与当前问题

用户在 Windows 发布包中使用「随机浏览」时，缩略图下方显示的是「加入时间」。当前原因已定位：

- `web/app.js::buildTile()` 的随机模式直接格式化 `asset.created_at`。
- `assets.created_at` 是资产加入目录/数据库的时间，不是照片拍摄时间，也不是文件系统创建时间。
- 随机摘要接口 `POST /photo-random-summaries` 经 `api/queries.py::random_asset_summaries()` 返回最多 50 张的有界摘要。
- 项目已有 P4-00B 拍摄时间事实：`taken_time.read.asset_view()`；其 `capture` 标签对应 `EXIF_ORIGINAL` / `EXIF_DIGITIZED`。`EXIF_ROOT` / `FILE_MTIME` 是文件时间，不是本需求所说的拍摄时间或文件创建时间。
- `file_locations` 目前记录 `mtime_ns`，没有单独持久化文件创建时间。

目标：随机浏览每张缩略图按以下顺序显示时间，绝不再把入库时间冒充照片时间：

1. **拍摄时间**：优先使用当前规则版本可信的现有拍摄时间事实（沿用 P4-00B 契约）。
2. **创建时间**：没有可用拍摄时间时，读取注册文件的真实文件系统创建时间；Windows 文件属性中的「创建日期」是本卡的目标语义。
3. **时间未知**：拍摄时间不可用且文件系统也无法提供创建时间时，明确显示「时间未知」。

## 2. 时间语义（固定）

- 仅当 `taken_time` 规则版本可信、`label == "capture"` 且 `taken_at` 非空时，才显示「拍摄时间」。遵循现有契约，`EXIF_ORIGINAL` 与 `EXIF_DIGITIZED` 都属于该标签；不在本卡重新定义 EXIF 优先级或时区规则。
- `EXIF_ROOT`、`FILE_MTIME` 即使已有提取结果，也不能替代本需求的文件创建时间；它们分别代表 EXIF 根 IFD 文件时间和文件修改时间。
- 文件创建时间优先读取 `stat_result.st_birthtime_ns`。Windows / Python 3.12.10 环境若该属性不可用，可仅在 `os.name == "nt"` 时回退到 `st_ctime_ns`（Windows 的创建时间语义）。
- **不得**在 POSIX 平台把 `st_ctime_ns` 当作创建时间；它表示 metadata change time。平台没有真实 birth/creation time 时返回未知。
- **不得**回退到 `st_mtime_ns`、`file_locations.mtime_ns`、`EXIF_ROOT`、`FILE_MTIME` 或 `assets.created_at`。
- 同一资产有多个文件位置时，按现有受控位置顺序（`location_id` 升序）选择第一个仍通过校验且能提供创建时间的 PRESENT 文件；无法使用的路径继续尝试下一处。没有可用位置则显示未知。
- EXIF 时间是文件声明的墙上时间，不能擅自转 UTC；文件创建时间是文件系统时间，按本机本地时区显示。保留二者来源标签，不能混成一个无来源时间值。

## 3. 最小实施范围

优先只修改随机摘要和随机模式展示：

- `src/photo_search/api/queries.py`：在 `random_asset_summaries()` 中为本页资产生成一个专用的随机浏览时间投影；不要把字段加进所有普通资产列表摘要。
- `src/photo_search/taken_time/read.py`：如需避免逐资产读取，可增加有界批量读取入口；规则版本只检查一次，拍摄时间事实按本页 asset IDs 批量读取。
- `src/photo_search/api/files.py`：复用现有受控路径解析（`resolve_present_locations()` / `_resolve_row()` 的边界校验）。若逐资产调用会产生明显 N+1 查询，可增加批量包装，但必须委托现有校验逻辑，不得复制或绕过路径安全规则。
- `src/photo_search/web/app.js`：随机模式改为展示「拍摄时间 YYYY-MM-DD HH:mm:ss」或「创建时间 YYYY-MM-DD HH:mm:ss」；两者都没有时显示「时间未知」。可保留现有位置数量信息。
- 随机模式不得再显示「加入时间」。普通列表模式原有「加入时间」语义不变。

接口只增加随机摘要专用的显示时间字段；不向响应暴露绝对路径、原始 EXIF 文本或其他文件系统隐私信息。前端不得为每张照片额外请求 `GET /assets/{id}` 取时间。

### 明确不做

- 不改 `assets.created_at` 的定义或历史值。
- 不改 P4-00B `asset_metadata` 表、时间来源契约、`capture_desc`、时间轴或搜索筛选语义。
- 不新增数据库 migration，不要求重建图库，不做全库回填。
- 不重新读取原图内容、不重新提取 EXIF、不运行模型、不触发建库/刷新任务。
- 不改普通列表、时间轴、搜索结果、地图相册、地点相册、人物/合集页面。
- 不为本需求顺手重构随机抽样、分页、近期去重或查看器返回逻辑。

预期通过运行时对本次随机页做有界文件元数据读取即可完成；若实施者发现必须新增 schema / 全库回填才能安全实现，先停止并说明原因，不得自行扩大范围。

## 4. 性能与安全设计

- **链路与风险**：随机浏览每个摘要页最多 50 张；风险是为每张照片增加详情请求、重复 SQL 或不受控文件访问。
- **做法与边界**：拍摄时间按当前页批量读取；仅对缺少可信拍摄时间的当前页资产读取文件创建时间。复用注册路径和现有受控解析，文件系统只做 metadata stat，不打开、不解码原图。不得扫描整个图库。
- **验证与停止**：证明没有逐图 HTTP 详情请求、没有整库查询/扫描，文件 stat 仅限本页 fallback 候选；验证随机首屏、续页及返回流程。不新增 30 万容量或固定延迟承诺；没有实际规模风险证据不做 30 万压测。

## 5. 验收标准

### 时间来源与优先级

1. 同时有 EXIF 拍摄时间和不同的文件创建时间时，显示拍摄时间。
2. `EXIF_ORIGINAL` 与 `EXIF_DIGITIZED` 按现有契约均显示为拍摄时间。
3. 无可信拍摄时间、但文件创建时间可读时，显示文件创建时间。
4. 仅有 `EXIF_ROOT` / `FILE_MTIME` 时，仍回退到真实文件创建时间，不把前两者显示为拍摄时间或创建时间。
5. 拍摄时间尚未抽取、规则版本不匹配、文件创建时间不可用或文件不可安全访问时，不伪造时间，显示「时间未知」。
6. Windows 的 birthtime / ctime 兼容分支正确；POSIX 的 ctime 不会被误认作创建时间。
7. 多文件位置按固定顺序选择有效创建时间；失效、越界、reparse point 或观察值变化的路径被安全跳过。

### UI 与兼容

8. 随机浏览卡片显示来源正确的时间标签，不再显示「加入时间」。
9. 普通列表仍显示原有「加入时间」，其它浏览模式不变。
10. 时间未知时布局稳定，不出现 `Invalid Date`、空白伪时间或控制台错误。
11. 随机摘要仍按原有顺序返回有效资产，分页、换批、避重、收藏、viewer 与返回恢复不变。
12. API 不泄漏绝对路径、`taken_at_raw` 或其他原始文件信息。

### 测试与浏览器

- 定向测试：`tests/unit/test_p4_random_browse.py`、`tests/integration/test_p4_random_browse_api.py`、`tests/unit/test_p4_ui_photos.py)；如增加文件路径批量 helper，补其边界/观察值回归。
- 覆盖拍摄时间优先、文件创建时间回退、时间未知、Windows / POSIX 时间戳语义、多位置、路径失效及规则版本不可信。
- 用隔离合成图库做实际浏览器验收：至少检查桌面宽视口（约 1280×900）与窄屏（约 450px），覆盖随机首批、下一批、滚动续页、切换普通列表、查看照片及返回。
- 完成受影响 Python 测试、Ruff、Python 编译、`node --check src/photo_search/web/app.js` 和 `git diff --check`；记录真实退出码。全仓回归仅在实际影响面扩大或测试策略明确要求时执行。
- 不读取、扫描、修改用户真实照片、真实 `PhotoSearchData` 或正式发布包数据。

## 6. 独立复核与交付

- 推荐执行模型：**GPT-6 / High**。模型由用户在 Codex 中手动选择，不声称自动切换。
- 完成后进行独立、受影响面完整的只读复核，重点检查：P4-00B 时间语义未变、Windows / POSIX 创建时间区别、文件路径安全边界、随机摘要有界性、没有 N+1/逐图 HTTP 请求、普通列表无回归。若修改共享路径解析行为，复核必须覆盖该 helper 的全部调用者。
- 更新执行报告：`docs/reports/P4-PHOTO-RANDOM-BROWSE-TIME.md)，记录改动、测试/浏览器证据、性能边界和未验项。
- 实现完成后同步 `docs/development-status.md`，准确标注状态；未完成的必测项保持 PENDING/BLOCKED。
- 只完成本卡范围；不自动提交/推送代码，不构建或发布 Windows 安装包。
