# P4-PLACE-MAP-A — 旧地名包兼容修复

2026-10-04。用户手动打开时报 `PLACE_DATASET_INVALID: the dataset is not compatible with this catalog schema`。

原因：地图迁移把 catalog 从 schema 16 升到 17，现有 `geonames-asia-cities500-v1` 包仍声明兼容 5～16，中央校验因此拒绝。0017 仅新增地图成员版本、触发器与 bbox 索引，没有改变地名表／包格式语义。

修复：`geo/dataset.py::validate_manifest` 明确允许 schema 17 使用 maximum=16 的有效旧包。更早 maximum、未来 catalog 和范围／格式／checksum 不合格包仍拒绝；manifest 和 registry 的原始兼容范围不改写。中央校验覆盖状态查询、解析、导入、激活／回退及启动恢复。规则见 [ADR-0005 §8](../decisions/ADR-0005-place-facts-assignment-and-search-contract.md)。

验证：

```text
.venv/Scripts/python.exe -m pytest -q tests/integration/test_place_dataset_schema17.py tests/unit/test_p2_dataset_manifest.py tests/integration/test_p2_dataset_lifecycle.py tests/integration/test_place_package_startup_recovery.py tests/integration/test_p2_place_dataset_api.py --basetemp=.pytest-tmp/p4-map-schema-fix --junitxml=test-results/p4-map-schema-fix-junit.xml --tb=short
64 passed, 0 failures, 0 errors, 0 skipped
.venv/Scripts/python.exe -m ruff check src/photo_search/geo/dataset.py tests/integration/test_place_dataset_schema17.py
git diff --check
```

新增 10 项回归：合成 schema-16 活动库真正迁移到 17 后，原地点／活动 revision 不变且解析与激活仍可用；唯一旧包在空 registry 中启动恢复；版本桥仅适用于 16→17；损坏包仍拒绝。夹具中包字节／mtime 保持不变。

修复前已复现旧包版本拒绝及启动恢复不可用。第一次沙箱运行遇 pytest 临时目录 `WinError 5`，属于环境失败；随后按正常权限路线复现及验证，没有改 ACL。最初旧库夹具保留了 0017 脚本导致连续性校验失败，已改为隔离的 0001～0016 迁移目录后复验。

另只读完整校验 `D:/SZ/project/PhotoSearchData/places/datasets/geonames-asia-cities500-v1`：45,217 条，声明 5～16，schema 17 下通过文件大小／哈希／层级校验，全部顶层包文件 SHA-256 和 mtime 前后相同。没有打开真实 catalog、扫描照片或修改数据包，无需重新下载。用户需退出并重新启动应用加载修复；本轮没有实际 UI 重启验收、全量套件或 30 万计时。MAP 独立 DS-H 全受影响面审查仍待做，不标整卡 PASS。
