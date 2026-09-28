# P4-05 — 停机一致性备份 + 空目录恢复 + 迁移保护：实现与 D 级验证

日期：2026-09-28
状态：`IMPLEMENTATION VERIFIED / DS-H ENGINEERING REVIEW PENDING`。本报告不是个人试用或发行级 PASS。

## 实现范围

新增 `photo_search backup create|restore` 维护命令及 `photo_search.backup`：

- 仅面向已停机、SQLite/Lance 句柄已关闭的状态；不会热拷贝运行中数据。
- 集合成员固定为应用数据根的 `catalog/`、`vectors/`、`artifacts/`、`thumbnails/`、`previews/`、`cache/`、`runtime/`、`places/`。不遍历 `source_roots` 指向的照片目录。
- `create` 先精确核验 catalog schema 为本构建支持版本 16，再复制到同级 staging；对每个文件写入相对路径、大小、SHA-256，最后写 manifest 并目录原子发布。
- `restore` 仅接受空数据根；先校验 manifest/每个摘要、schema manifest 与 catalog 的精确版本，复制到 staging 并复核后才发布。它不调用迁移，schema 高或低都以结构化 `BACKUP_SCHEMA_MISMATCH` 拒绝。
- 复制/校验/发布失败会清理 staging，因此不会留下可误用的完整备份或数据根。
- 模型权重未纳入集合（可大且独立安装）；manifest 明确标记。恢复后的已生成向量可直接被已安装的同一模型运行时查询，不触发重新编码；本卡未主张模型随备份携带。

未修改 schema、迁移、outbox/claim/revision、搜索 API 信封或错误码映射；未访问真实照片库；未新增依赖。

## D 级证据

- 单元+集成定向 JUnit：`test-results/p4-05-focused.xml`，8 passed。
  - manifest 路径/大小/SHA-256；空目录恢复后 metadata 与派生文件保持；
  - 生产扫描 + FTS + Lance generation 的恢复前后文字/语义页面 asset_id 与 channels 逐项相同；
  - backup 和 restore 的中途复制失败注入均不发布目标；
  - catalog 实际 schema 15 与 17 均拒绝，目标仍不存在；
  - CLI create JSON 成功、restore JSON 结构化拒绝。
- 全量 JUnit：`test-results/p4-05-full.xml`，1795 tests / 0 failures / 0 errors / 48 skipped，246.312 s。
- `uv run --no-sync ruff check .` exit 0。
- `uv lock --check` exit 0。
- `git diff --check` exit 0。

首次受限沙箱运行 pytest 在项目规定 `.pytest-tmp` 创建时触发 `WinError 5`；按 AGENTS 规则改走正常权限路由，以上 JUnit 为实际执行结果。

## 保留边界与待审

- 本实现以操作员保证停机为前提；尚未接入服务进程的暂停/句柄关闭协调，也没有 HTTP 恢复入口。
- 不包含源路径 remap、发行级升级/回退、多机/打包、模型权重携带与真实图库演练。
- 需独立 DS-H 完整受影响面工程主审，随后按卡面压缩交接给 Sol high 一致性聚焦终审；两者完成前不得标记 PASS 或生成下一卡。
