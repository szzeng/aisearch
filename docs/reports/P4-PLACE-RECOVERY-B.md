# P4-PLACE-RECOVERY-B — 单复合地名包清库后自动恢复

日期：2026-10-08。状态：**IMPLEMENTED / MANUAL TEST PENDING**。

## 实现

修改 `src/photo_search/places/datasets.py`：自动恢复发现唯一健康包的 `previous_dataset_id` 在新 catalog 中缺失时，只把新写入 registry 行的该字段置为 `NULL`。manifest 仍参与完整包校验且不改写；文件校验、目录安全、Schema、唯一包门槛及一个事务内注册/激活/地点物化保持不变。恢复日志说明忽略不可用的历史回退目标。

投影读取 catalog 行的回退值，因而显示 `null`；`_verify_stored` 和磁盘状态投影只要求包身份及内容校验，不要求数据库回退字段与原 manifest 相等，因此恢复后的包可正常重验、查看和重新激活。通用导入仍按 manifest 写入前驱字段；没有放宽其外键约束。

更新 ADR-0015，仅记录“空 registry、唯一健康包、历史前驱缺失”这一恢复例外。

## 验证与未验证

- 已按任务卡检查当前代码及既有工作区改动；仅修改恢复实现、ADR、状态和本报告。
- `uv run --no-sync ruff check src/photo_search/places/datasets.py`：通过。
- `git diff --check`：通过。
- 按任务卡约定，本轮不运行 pytest、浏览器自动化或全量测试。运行时行为及真实数据根手动验收待用户完成；当前不标 PASS。
- 未访问或修改 `PhotoSearchData`、个人照片或地名包。

## 用户手动验收

操作前关闭 PhotoSearch 并备份旧库。用户自行将原 GeoNames 包移出 `places/datasets`，确认其中仅剩完整中国复合包；随后在备份保护下重建空 catalog/照片库，运行 `tools/dev/start_personal_trial.ps1` 并打开地点页触发恢复。页面或在 API 已触发恢复后运行 `manage_dataset.py --data-root ... status`，确认 `state=active`、`resolver_available=true`、活动包 ID 为复合包；日志不再出现 `needs a predecessor; automatic recovery skipped`。关闭并重启后再次确认 ACTIVE，并抽查一个中国区县和一处国外城市。旧包备份是否删除由用户自行决定。
