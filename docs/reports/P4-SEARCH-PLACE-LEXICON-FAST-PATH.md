# P4-SEARCH-PLACE-LEXICON-FAST-PATH 执行报告

日期：2026-10-20
状态：**IMPLEMENTED / ACCEPTANCE PARTIAL**

## 目标与边界

文本地点查询先在 SQLite 中按当前活动 GeoNames 包筛选规范化主名/别名，再为命中地点构造完整候选。`text=None` 与空白查询仍返回完整目录。OSM、已验证 `cn_legacy_links` 兼容和 INDEX 历史回退继续按活动来源身份验证；不新增迁移、长期缓存或索引，不访问真实图库。

## 实现

- `choices.py` 新增参数化 GeoNames 主名/别名预筛 SQL；当前活动包命中优先，已验证且未被当前记录覆盖的历史 INDEX 记录仅作为 fallback。
- 关联地点只对已验证的链接地点读取需要的历史候选/别名；普通文本查询不再把完整 GeoNames 与别名目录全部返回 Python。
- 诊断开关共用同一候选算法，不再创建全目录规范化临时列表。诊断的 `place_full_candidates` 统计 OSM 与当前包目录候选，不扫描 provider-wide 历史引用；历史回退仍参与实际结果及匹配数。它避免为计数而重新处理 45k 来源 ID。
- 空文本完整目录、来源顺序、候选身份和数据更新可见性保持原有语义；未引入 schema 或持久化状态。

## 验证

定向 JUnit：86 passed，0 failed，0 errors，0 skipped（`test_search_pipeline_perf.py`、`test_search_perf_diagnostics.py`、`test_p4_search_intent_unified.py`）。Ruff、`py_compile` 与 `git diff --check` 通过。结果文件保存在忽略目录 `test-results/P4-SEARCH-PLACE-LEXICON-FAST-PATH/unit-final.xml`。

新增合成基准脚本 `tools/benchmarks/p4_place_lexicon_fast_path.py`，以提交 `814ffa1` 的实现为 oracle，在内存 SQLite 中生成 3,013 OSM、45,217 GeoNames、26,520 aliases、274 links，并向两版提供包含全部 45,217 个来源 ID 的有效 INDEX。生产调用的 `data_root` 分支、`place_external_refs` 主键/地点索引及回退验证均纳入测量。no-hit、one-hit、many-hits、linked-alias 四种场景的完整结果签名一致，命中数分别为 0、1、100、1。表中与完整 JSON 数据来自 `test-results/P4-SEARCH-PLACE-LEXICON-FAST-PATH/benchmark-5-samples.json`。

| 场景 | 基线诊断关闭 P50 | 新版诊断关闭 P50 | 新版诊断开启 P50 | 基线返回 Python 行数 | 新版返回 Python 行数 |
|---|---:|---:|---:|---:|---:|
| no-hit | 262.37 ms | 83.39 ms | 94.38 ms | 123,255 | 6,848 |
| one-hit | 302.35 ms | 83.74 ms | 95.92 ms | 123,255 | 6,850 |
| 100-hit | 260.26 ms | 86.90 ms | 105.07 ms | 123,255 | 7,048 |
| linked-alias | 274.45 ms | 87.62 ms | 100.28 ms | 123,255 | 6,850 |

每个实现/场景先单独记录一次 seeded in-memory Catalog 上的 first-call，再预热并测 5 次热调用；上表为热 P50。样本 P95 按本机小样本的最大值计算，只作为合成微基准观察，不代表正式分位数或真实图库 SLA。诊断开启比关闭多 10.99–18.17 ms；每次多一条全目录标量计数读取和一行返回。`place_full_candidates` 统计当前包与 OSM 的完整目录规模，不遍历 provider-wide 历史引用；历史回退候选仍参与结果签名及实际 matched 候选。返回行数是 Python 实际收到的 SQL 行数，不是 SQLite 内部扫描行数。

### SQL 执行计划与限制

EXPLAIN 显示 SQLite 通过 `(dataset_id, provider)` 复合主键限定当前包范围，按 place 主键读取地点，并通过 `idx_aliases_place(place_id, lifecycle)` 检查活动别名；排序使用临时 B-tree。`instr()` 子串谓词不是索引 seek，SQLite 仍会扫描该活动包的引用范围。因此结果表中的行数指 Python 收到的返回行数，不是 SQLite 内部扫描行数。合成数据证明减少了 Python 返回和构造工作，但不能据此宣称消除了 SQLite 内部扫描。

## 未验证项与后续

- 用户真实 EXIF v2 图库的 1+2+20 搜索复测尚未执行；没有实机端到端性能结论。
- 未运行浏览器、真实模型/GPU、打包或全量测试；本任务范围是后端地点候选路径。
- Luna High 初审提出的三项 finding 已处理：补齐有效 INDEX 基准；匹配回退先 SQL 筛选再按 INDEX 验证集合过滤；修正重复来源引用计数，并将诊断全目录计数限制为当前包+OSM以避免扫历史引用。跟进复核确认 finding 已关闭、无新增代码 blocker；文档已指向最新五样本 JSON。真实 EXIF v2 1+2+20 仍待用户复测。

## 复现

```powershell
$env:PYTHONPATH="$PWD\src;$PWD"
.\.venv\Scripts\python.exe -m pytest -q tests/unit/test_search_pipeline_perf.py tests/unit/test_search_perf_diagnostics.py tests/unit/test_p4_search_intent_unified.py --junitxml="test-results/P4-SEARCH-PLACE-LEXICON-FAST-PATH/unit-final.xml" --basetemp=".pytest-tmp-lexicon-final"
.\.venv\Scripts\python.exe tools/benchmarks/p4_place_lexicon_fast_path.py --samples 5
```

未提交、未推送。
