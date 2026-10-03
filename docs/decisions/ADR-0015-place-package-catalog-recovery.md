# ADR-0015 — catalog 重建后的唯一地名包自动恢复

日期：2026-10-03。状态：**已按用户要求实现；独立 DS-H 审查待做，尚未正式冻结**。

## 问题与用户要求

用户保留 `places/datasets/geonames-asia-cities500-v1`，删除 catalog/vectors 后，地点页仍显示包未安装。上一轮修复仅支持显式重新导入，未实现用户要求的自动可用。地名参考包本身应不依赖图文向量或历史照片 catalog 的存续。

## 最小决策

- catalog 仍是正常运行时的活动包状态权威，沿用 ADR-0005 的状态、激活事务、地点物化和 revision 语义。
- 在应用服务打开 catalog 后、业务读取或后台处理开始前恢复。`create_app` 本身保持无 I/O，GET 地点投影本身保持只读。
- 当 registry 完全为空、`places/datasets` 中只有一个安全直接子目录时，以现存只读包为输入，完整校验 manifest/文件大小/hash/格式/层级/兼容 schema，以及目录名和 dataset_id 一致性。
- 校验成功后在同一 catalog 事务插入 STAGED 行、转 VALIDATED、激活并物化地点；失败整体回滚。不复制或重写包文件，不下载，不读取照片，不操作向量目录。
- 已有任何 registry 行则保持原有活动或未激活选择；并发打开在事务内重新检查。多包无法从文件准确判断用户此前的活动选择，必须显式选择；坏包、缺件、不兼容、错目录和符号链接/目录联接不自动激活，不妨碍浏览 catalog。若 manifest 指向缺失的 previous_dataset_id，空 registry 无法满足原 FK；跳过自动恢复而不删除原回退关系或阻止启动。

## 边界与证据

仅恢复地名参考包登记及可重建的地名目录；不能还原已删除 catalog 中的人工归属、照片历史或建议。不新增第二个业务数据库、持久化活动指针镜像、migration 或 companion 契约变化。

证据以 [P4-PLACE-PACKAGE-RECOVERY 报告](../reports/P4-PLACE-PACKAGE-RECOVERY.md) 为准：实际删除合成 catalog/vectors 后经默认 API lazy service factory 自动恢复，包字节/mtime 不变；另覆盖缺件/篡改/不兼容/错目录、多包、未激活选择、事务故障及重启幂等。按 V3.1 §11 留给独立 DS-H 全受影响面工程主审；需正式冻结该恢复决策时，再由用户手动选择 Sol high 聚焦审查。
