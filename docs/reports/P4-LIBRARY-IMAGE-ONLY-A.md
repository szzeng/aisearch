# P4-LIBRARY-IMAGE-ONLY-A 执行报告

日期：2026-10-13。结论：**SCOPED PASS / 定向合成简单测试通过**。未做独立复核。

## 实现

- 新增 `catalog/image_policy.py`，以静态、大小写不敏感的扩展名白名单统一图片识别、扫描和 SQL 照片资格。包含常见位图、HEIC/HEIF/HIF、AVIF、TIFF、JXL/JP2、SVG 与常见相机 RAW；明确不含 `.r3d`、`.eip`、视频、音频、文档、压缩包和 sidecar。策略不依赖解码器，未承诺未解码格式可预览或 AI 处理。
- 扫描在 `is_file` 后、文件 stat/hash 前跳过白名单外文件；fast/full/初次建库仍走同一入口。成功扫描继续由原 reconcile 把历史非图片位置标为 `MISSING`，中断路径未改。
- 普通照片 SQL 资格现在要求至少一条登记图片路径；来源过滤要求图片路径属于该来源。`availability=any|missing` 对真正缺失图片保持原语义，零位置 legacy asset 语义保留。各既有 API、SearchScope、人物/合集/地点入口继续通过原 `photo_asset_sql` 导入点获得此资格。
- Windows 本地打开图片动作复用白名单，`.r3d` / `.eip` 不再作为图片类型开放。
- 扩展原只读 `video_asset_probe.py`：增加非图片位置的 PRESENT/MISSING、仅非图片/图片共享资产、图片位置数与 asset 数、扩展名 Top 10 及相对路径样例，同时保留旧视频统计字段。仅读取传入 SQLite，不访问原文件或向量。

## 验证

- JUnit：`tests/integration/test_p4_library_video_exclude.py`、`test_p4_person_collections.py`、`test_p4_place_search_flow.py`、`test_p4_photo_local_open.py` 定向运行，**23 passed / 0 failed / 0 errors / 0 skipped**。结果：`.pytest-tmp/P4-LIBRARY-IMAGE-ONLY-A.xml`。
- 首次收集地点测试因 pytest 未将仓库根加入 `sys.path` 而无法导入 `tools.places`；设置 `PYTHONPATH=.` 后同一组测试正常执行。未改环境 ACL。
- 对全部本卡改动 Python 文件运行 Ruff：通过；`python -m py_compile`：通过；`git diff --check`：通过。
- 混合类型测试确认 JPG/HEIC/DNG/TIFF/AVIF/SVG 登记，MOV/MP3/PDF/XMP/ZIP/无扩展名/R3D/EIP 跳过；历史 PDF 成功 fast 刷新转 MISSING，默认 any 不返回该记录而仍可返回 MISSING 图片。视频旧探针指标及非图片分类也由合成 SQLite 验证。

## 未验证与边界

- 未访问真实图库或 PhotoSearchData；未运行全仓、GPU/模型、浏览器、20 万规模性能、打包及真实数据迁移/升级。
- 未执行任何原文件/数据库历史数据永久清理；测试检查被扫描原文件字节、大小和 mtime 不变。
- 仅按扩展名分类；伪装扩展名无法识别。扩展名欺骗与 SVG 预览能力不属于本卡。SQL 快速通道继承 SearchScope 权威资格；本卡未跑大型查询基准。
- `.eip` 等封装类型列为后续明确确认其是否独立图片后再扩展的候选。
