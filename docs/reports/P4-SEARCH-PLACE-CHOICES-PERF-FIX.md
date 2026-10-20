# P4-SEARCH-PLACE-CHOICES-PERF-FIX 执行报告

**状态：IMPLEMENTED / ACCEPTANCE PARTIAL**
**基线：** `890e362af3ef6ba742bf045535c7f50f884e0263`，分支 `master`（开始时较 `origin/master` ahead 3）
**交付边界：** 本地工作树修改；未提交、未推送，未访问真实照片库。

## 实施内容

- **FIX-1：** `cn_legacy_links` 处理建立 `place_id -> 首个 GeoNames 行` 映射，保留首次重复 ID、原行引用、候选顺序、继承别名去重顺序和 `linked_local` 排除规则；不引入跨请求缓存。
- **FIX-2：** 有文本条件时先对轻量行和别名做匹配，只为命中的 GeoNames 行构造完整候选。链接继承别名在过滤前合并。GeoNames 名称和别名读取存储的 `normalized_name` / `normalized`（导入时已执行 NFKC、casefold 和空白折叠），匹配时去掉折叠空格以保持 `_compact()` 语义；OSM 拼写仍按请求正规化。`text=None` 保持完整目录接口。
- **诊断：** 仅当调用方提供 `perf_metrics` 时记录地点链接合并、OSM 正规化、已存正规化键准备、名称匹配、候选构造和残余耗时；JSON allowlist 与原 `phase_tree.preparation` 增加这些数值字段和正规化复用/对象计数。子阶段作为嵌套明细，不加入顶层非重叠加总。包含 SQL/索引耗时的候选数据准备计时不导出为独立 Python 阶段。
- **FIX-3：** 未修改 SQL 查询数或引入新的查询策略。已有 400 项批量查询保留；合成对照仍为 126 条 SQL（114 别名、8 映射），当前单次观测 SQL 耗时 670.611 ms。现有数据不足以证明进一步 SQL 修改有净收益。

## 性能与正确性证据

隔离合成目录包含 3,013 条 OSM、45,217 条 GeoNames、274 条旧地点关联，共 47,956 个完整候选。没有访问用户图库。保存的原始对照位于忽略目录 `.tmp/P4-SEARCH-PLACE-CHOICES-PERF-FIX/comparison.json`。

| 对照 | 运行时间 | 地点计时 | SQL | 候选/结果 |
|---|---:|---:|---:|---:|
| FIX-1 前（1 次测量） | 1,159.912 ms | 2,862.325 ms | 126 | 47,956 / 47,956 |
| 仅 FIX-1 后（1 次测量） | 854.046 ms | 860.954 ms | 126 | 47,956 / 47,956 |
| FIX-2 前、FIX-1 基线（无命中，1 次） | 938.228 ms | 1,035.174 ms | 126 | 47,956 / 0 |
| FIX-2 后（无命中，1 次） | 1,042.953 ms | 1,105.402 ms | 126 | 47,956 / 0 |

这些是一轮本地合成计时，SQL fixture 和环境噪声显著，不能作为稳定提速结论或延迟保证。FIX-1 单样本显示关联查找有下降趋势，但需要实测复验。FIX-2 后无命中路径构造 **0** 个 GeoNames 候选对象；诊断计数显示正规化拼写 3,561 个（主要为 OSM），复用了 89,886 个已存正规化拼写。它仍需读取和轻量检查目录行与别名；该样本的端到端时延未证明提升。

当前无命中样本的地点细项：SQL 670.611 ms、关联合并 11.865 ms、OSM 正规化 4.520 ms、已存键准备 48.020 ms、匹配 18.019 ms、候选构造 5.370 ms、Python 残余 346.997 ms。`place_choices_ms` 包含 SQL 等子项，不可与其直接相加。

结果签名检查显示完整候选数 47,956 且结果相同；无命中查询结果相同；定向测试覆盖继承别名、NFKC/大小写/空白等价、同名候选、来源与 selection ref、完整目录顺序及诊断导出。没有发现身份或顺序变化。

## 验证

- `tests/unit/test_search_pipeline_perf.py tests/unit/test_p4_search_intent_unified.py tests/unit/test_search_perf_diagnostics.py`：**80 passed, 0 failed, 0 skipped**。JUnit：`test-results/P4-SEARCH-PLACE-CHOICES-PERF-FIX/affected-final.xml`。
- Ruff：通过；`py_compile`：通过；`git diff --check`：通过。
- `tests/integration/test_p4_place_unified.py` 与 `tests/integration/test_p4_place_search_flow.py` 的初始定向运行有 7 项在进入受测代码前失败：旧地点 manifest 的 catalog schema 兼容范围不匹配（`PLACE_DATASET_INVALID`）。保留原始 JUnit `test-results/P4-SEARCH-PLACE-CHOICES-PERF-FIX/focused-20261020-135311-967.xml`，不改写共享 fixture；此阻塞不作为产品回归结论。
- 独立 Luna High 跟进复核确认先前指出的计时重叠与全量 GeoNames 名称重新正规化问题已关闭；未发现新的代码阻塞项。复核未运行测试。

## 尚未完成与复测

真实图库 Qwen EXIF v2 的 1 次初始观察、2 次预热、20 次热搜索仍 **PENDING**。用户可在同一图库、相同查询词和综合搜索模式、`top_k=5000`、每页 20 张的条件下分别收集前后样本，再比较 `search_execution_ms`、`place_choices_ms` 和 `phase_tree` 的 P50/P95。当前真实基线为卡片提供的 20 个有效热样本（搜索 P50 1,201.9 ms、地点 P50 1,026.0 ms、检索 P50 165.0 ms）；本次没有重跑真实数据。

整体性能收益和真实图库验收未完成，故保持 **IMPLEMENTED / ACCEPTANCE PARTIAL**。未执行 30 万照片认证、浏览器测试、模型/GPU运行或全量回归。
