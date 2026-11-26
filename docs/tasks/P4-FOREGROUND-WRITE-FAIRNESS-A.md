# P4-FOREGROUND-WRITE-FAIRNESS-A — 前台写入与后台建库并发治理（工作包 B）

状态：**B1 SCOPED PASS / B2 CONDITIONAL**。个人项目简化规划。
当前规划基线：`b4f826e579b0e731bab064f7be0ee50dd0768b1c`（2026-11-27）。

父任务：[P4-CATALOG-CONCURRENCY-STABILITY](P4-CATALOG-CONCURRENCY-STABILITY.md)。
工作包 A：[P4-SOURCE-DETAIL-STABILITY](P4-SOURCE-DETAIL-STABILITY.md)。

## 问题背景

后台建库期间，前台修改人物名称曾出现长时间卡顿。代码已确认人物/人脸写入 async 路由直接调用同步 `PersonService`，其 `Catalog.write()` 等待会阻塞 API event loop。

历史 P4-02U/V、P4-LOCK-CONTENTION-RECOVERY-A/B/C/D 和 P4-BUILD-STABILITY-A2 已覆盖部分 BUSY 重试、搜索追赶降级、扫描恢复、模型资源等待和 Worker 生命周期；应复用，不重复实现。

## B1：人物写操作线程隔离

实施卡：[P4-CATALOG-CONCURRENCY-B1](P4-CATALOG-CONCURRENCY-B1.md)。

目标：把用户触发的 PersonService 写操作移出 API event loop，使用一个专用单写 Worker 和线程自有 Catalog/SQLite connection。保留人物操作事务、幂等、undo、revision 和响应格式。

验收只做独立合成图库的少量关键测试：后台/测试连接持有 SQLite writer 时，人物写请求等待但健康接口仍响应；释放锁后人物修改成功；连接线程归属正确；应用关闭后无遗留 Worker/connection/lease。

B1 已通过上述合成验收。独立 Terra High 复审通过。人物操作、生命周期及 A0/A1 回归 JUnit 共 225 项通过。见[执行报告](../reports/P4-CATALOG-CONCURRENCY-B1.md)。

B1 不改变后台扫描、语义、人脸、OCR 的写入调度。

## B2：后台安全让步（条件任务，暂不实施）

只有 B1 完成后，若简单复现仍证明后台连续写事务会让前台人物写入长期等待，才建立 B2 实施卡。

若确有必要，先选出实测最明显的一个后台写入路径，在完整事务/安全批次边界让步；确认有效后再决定是否扩展到其他阶段。优先复用已有任务调度和恢复机制。

必须保留：
- 不强制中断正在执行的 SQLite 事务；
- 不拆分人物操作原子事务；
- 不拆分 LanceDB 写入与 SQLite outbox 确认协议；
- 不新增全局互斥锁，不改变 WAL/busy_timeout；
- 前台操作结束后后台能够继续。

如果 B1 后人物操作已能保持响应，且等待时间可接受，则 B2 可以不做，不为建立复杂协调器而建立协调器。

## 项目测试标准

这是个人项目：用独立合成图库，做少量可重复的关键测试即可。优先验证真实问题路径，不跑全仓压力测试、不做 30 万图库性能认证、不要求正式大型 DS-H。若改动共享锁顺序、事务原子协议或通用 Worker 关闭机制，再针对性扩大审查。

## 安全边界

不访问真实照片库；不增加 Schema、表或索引；不改真实数据；不下载或加载模型；不重建 Windows EXE。任何涉及跨进程互斥、数据迁移或改变现有事务/恢复协议的方案，先停下单独确认。
