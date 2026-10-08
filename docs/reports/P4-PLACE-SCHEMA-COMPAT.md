# P4-PLACE-SCHEMA-COMPAT — implementation and evidence

日期：2026-10-09。状态：**IMPLEMENTED / 定向验证通过**。

## 基线与决策

任务卡立卡时记录 schema 20，但实际基线 `30cd536` 已包含 `0021_cn_manual_place_refs.sql` 且 `SUPPORTED_SCHEMA_VERSION=21`。核对 migration 后确认它只增加 OSM/GeoNames 来源身份到既有 `Place` 外键的 catalog 映射表；不改变 immutable GeoNames 包字节、CN addon 投影或 Manifest。审核上限因此收敛到当前 schema 21。本任务没有创建或修改 migration、schema 版本、Manifest、包 ID 或包内容。

## 实现

`src/photo_search/geo/dataset.py` 定义 `PLACE_LEGACY_MAX_FLOOR=16` 与 `PLACE_COMPAT_REVIEWED_THROUGH=21`。统一历史桥要求 `minimum <= 当前 schema`、`maximum >= 16`、`maximum < 当前 schema` 且当前 schema 不高于审核上限。声明区间内仍按原规则接受；maximum 15、minimum 高于当前 schema、超出审核上限均不能通过历史桥。CN addon 仍要求 schema 至少 18，现有其他 manifest、身份、路径、文件大小和 hash 校验不变。

`tests/integration/test_place_dataset_schema17.py` 扩展旧矩阵覆盖 schema 16–20 与当前 schema 21，并通过纯政策函数证明 schema 21 在模拟审核上限 20 时拒绝、上限 21 时接受；schema 22 未审核拒绝。schema16 升级用例改为在旧 schema 下登记/验证，升级后再通过当前真实激活路径，避免用 schema16 状态调用 0021 才引入的写表逻辑。新增小型合成复合包验证：激活状态和 resolver 可用；重开 CatalogService 后 Tokyo GeoNames 与济南 OSM 区县仍可加载；包含子目录的包文件字节与 mtime 快照完全一致。

## 验证

- PowerShell: `$env:UV_CACHE_DIR='.uv-cache'; uv run -- python -m pytest -q tests/integration/test_place_dataset_schema17.py --junitxml=test-results/p4-place-schema-compat.junit.xml --basetemp=.pytest-tmp/p4-place-schema-compat`：**30 tests / 0 failures / 0 errors / 0 skipped**。
- `uv run -- ruff check src/photo_search/geo/dataset.py tests/integration/test_place_dataset_schema17.py`：通过。
- `git diff --check -- src/photo_search/geo/dataset.py tests/integration/test_place_dataset_schema17.py docs/tasks/P4-PLACE-SCHEMA-COMPAT.md docs/reports/P4-PLACE-SCHEMA-COMPAT.md docs/development-status.md`：通过。

验证仅使用 pytest 临时合成目录。未访问 `PhotoSearchData`，未重制/重装包，未重算 GPS、搜索向量、OCR、人脸或缩略图；未运行全量测试、性能、GPU 或浏览器自动化。本结果只覆盖兼容规则和小型复合包加载，不代表真实数据目录或应用重启验收。
