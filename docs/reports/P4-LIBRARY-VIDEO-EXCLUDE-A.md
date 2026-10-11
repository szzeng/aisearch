# P4-LIBRARY-VIDEO-EXCLUDE-A 执行报告

日期：2026-10-11。结论：**SCOPED PASS / 任务卡合成定向验收通过**。

## 结果

- 在扫描枚举层按扩展名过滤常见视频；大小写后缀共用一套政策。目录仍照常枚举，视频文件不会进入文件观察、hash、位置登记或 AI 处理；fast/full 和首次建库继续复用同一生产枚举器。
- 历史位置在来源完整成功的扫描末尾经既有原子对账变为 `MISSING`。中断夹具确认在过期 lease 接管前仍为 `PRESENT`；lease 到期后的成功 fast 刷新才将其置为 `MISSING`。第二次成功 fast 刷新保持该状态。
- 新增共享 SQL 照片资格，在列表 chunk 与 SearchScope 权威候选条件中、分页/计数/Top-K 前要求存在非视频路径；带来源筛选时该图片路径必须属于指定来源。无位置资产保留原语义，`availability=any|missing` 下真正缺失照片仍可见，`present` 条件保持原行为。
- 人物相册/合集照片入口沿用 asset page 资格，人物/合集照片数与排序、地点相册候选也排除视频位置。图片与视频跨来源共享 asset 时，图片来源保留，视频来源不命中。
- 新增只读 SQLite 诊断脚本，按来源输出视频位置数及 PRESENT/MISSING、视频专属资产和共享资产数及少量相对路径示例。仅访问传入的 catalog 数据库，不遍历来源、不读视频或向量。
- 未改 Schema、原视频内容、资产、向量或人工归属；没有执行物理删除。测试中的合成视频文件内容、大小和 mtime 前后相同。

## 验证

- 最终定向 JUnit `.pytest-tmp/P4-LIBRARY-VIDEO-EXCLUDE-A-final.xml`：**25 passed / 0 failed / 0 errors / 0 skipped**。命令包含本卡两项合成闭环、人物相册/计数、合集分页、地点相册原有分页/状态用例；覆盖大写视频后缀跳过、旧登记位置与成功 fast 对账、重复刷新幂等、中断不误标缺失、默认 any 与 missing 查询资格、共享跨来源图片、连续 SQL 分页、原文件不变及诊断统计。
- 对全部本任务改动 Python 文件运行 `uv run --locked ruff check ...`：通过。
- 对全部本任务改动 Python 文件运行 `python -m py_compile ...`：通过。
- `git diff --check`：通过。

## 未验证与边界

- 未访问真实图库、真实 `PhotoSearchData`、模型/GPU 或向量库；未运行全仓 pytest、浏览器、30 万规模、打包或真实数据库升级。
- 资格按文件扩展名判断；不做 MIME/魔数检测。对有路径但全为视频路径的资产隐藏普通照片资格，不删除保留数据。
- 本结论限于任务卡所列合成闭环，不代表全仓或生产运行 PASS；未做独立复核（按用户要求）。
- 开工前已有的 `src/photo_search/geo/dataset.py` 用户改动保持不变；本任务未暂存或提交。
