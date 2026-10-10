# P4-PLACE-UNIFIED-A-FIX1 — 自然语言地点搜索 OSM 优先修复

日期：2026-10-10。状态：**DRAFT — 待执行（仅任务卡）**。立卡基线：master `07c192ef0bf50e1a086a3e90b0d4d6bbb09a1016`。
性质：P4-PLACE-UNIFIED-A 的小型行为修复；**只处理自然语言搜索地点候选优先级**，不改变地点身份、人工归属或地理数据。
推荐执行：GPT-6 Luna / Medium；同一会话完成最小修复与定向测试。

## 1. 问题与预期

用户在搜索“深圳”时被要求选择：
- 深圳市 · 广东省（城市 · OpenStreetMap）
- 深圳 · 台湾（城市 · GeoNames）

现有 `places/choices.py` 仅通过当前活动包的 `cn_legacy_links` 将**已经证明是同一现实地点**的 GeoNames 城市折叠入 OSM；对不同现实地点，保留独立候选是正确的数据身份设计。

但 `places/natural_search.py::parse_place_query()` 对剩余同名候选直接返回 `ambiguous`，未实现用户希望的**无明确地区限定时 OSM 来源优先**。本卡只修复自然语言搜索的**默认候选选择**，不得在目录层强行合并不同地点。

| 输入 | 期望 |
|---|---|
| `深圳` | 自动选择广东省深圳市的 OSM 地点 key，无重复选项 |
| `深圳的海边` | OSM 深圳作为地点范围，内容检索词仍为“海边” |
| `台湾的深圳` | 不得错误选广东深圳；父级可可靠验证则选择台湾 GeoNames，否则保留歧义/明确提示，**不猜测** |
| `东京` | 无匹配 OSM 地点时选 GeoNames |
| 两个不同 OSM 地点同名 | 仍要求用户选择，不按出现顺序猜测 |

已识别地点但无照片，应保留该地点返回零结果，不能把零结果当作 GeoNames 兜底条件。

## 2. 最小实现范围

按 `AGENTS.md` → `docs/development-status.md` → 本卡核对最新 HEAD、现有改动与 `P4-PLACE-UNIFIED-A` / `P4-SEARCH-PLACE-NL-A` 的直接实现；优先只改：
- `src/photo_search/places/natural_search.py`
- `tests/unit/test_search_place_nl_ui.py` 中的直接解析测试。

在 `parse_place_query()` 完成现有句式合法性、否定/多地点保护、名称匹配和最长匹配片段筛选后，针对**同一查询地点片段的候选**增加：
1. 尊重用户明确写出的且可由现有地点目录父链验证的区域限定；不要因 `source=OSM` 就覆盖明确地区要求。
2. 未限定具体上级地区时，对同名片段的有效候选，存在 OSM 则优先考虑 OSM；不存在 OSM 才考虑 GeoNames。
3. 来源优先后若唯一候选，返回原有 `recognized` 结构及该候选自己的 `content_query`；仍有多个独立 OSM 候选，返回原有 `ambiguous` 及每候选内容词。
4. 不同匹配片段、否定、多地点和非地点表达式继续沿用原有安全判断；不得通过全局 OSM 排序改变最长名称/子句识别。
5. 解析器仍仅消费调用方传入的**当前有效目录候选**；不自行查库、不联网、不查用户照片数量。若现有父级识别不足以正确处理“台湾的深圳”，以小范围修正或显式不确定为限，不扩展任意复杂自然语言解析。

**禁止改动**：`places/choices.py` 中的来源身份证明与同一实体折叠规则、`cn_legacy_links`、OSM/GeoNames 数据包、SQL 成员规则、`asset_place_assignments` / 撤销、数据库 migration、搜索 FTS/向量/RRF 算法、GPS 解析或地图 UI。不以名称相同为由合并不同现实地点，也不删除 GeoNames 台湾深圳候选。

如发现必须越过上述范围才能实现，请报告具体阻塞，不自行扩大为地点系统重构。

## 3. 最小验证

仅使用内存/合成地点候选，新增或调整少量直接测试：
1. `深圳`：广东 OSM 与台湾 GeoNames 同时存在，自动选 OSM 的正确 `key`。
2. `深圳的海边`：同一 OSM 范围且 `content_query="海边"`。
3. `台湾的深圳`：不误选广东 OSM；按已证明的父级解析，或返回歧义/未确认。
4. `东京`：只有 GeoNames 时正常回退。
5. 两个不同 OSM 地点同名仍返回 `ambiguous`。

视必要性仅补一条 `/search/place-aware` 的合成 API 回归，确认选择的 `place_kind/place_key` 与原地点照片成员逻辑相接；避免旧调用链出现回归。

只运行受影响 pytest（输出 JUnit XML）、Ruff、`git diff --check`；只在修改 JS 时增加 `node --check`。不运行全量 pytest、30 万规模测试、真实模型、真实相册、浏览器矩阵或安装/重新计算地名包。

## 4. 完成和停止

通过上述最小用例后，在已有实现报告（优先 `docs/reports/P4-SEARCH-PLACE-NL-A-implementation.md`）末尾记录问题原因、具体修复、测试命令/结果和未验证事项；按仓库现有流程更新开发状态。无需新增复杂报告或重复全仓审查。

**本卡不授权访问或修改真实 `PhotoSearchData`，不自动提交功能实现，也不启动其他任务。** 实现与定向验证完成即停止。
