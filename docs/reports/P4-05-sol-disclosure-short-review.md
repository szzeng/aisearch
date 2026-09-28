# P4-05 — 恢复一致性披露聚焦短复审

日期：2026-09-28。

**结论：`SOL SHORT REREVIEW FAIL`。新包、缺失声明旧包与正常 CLI 路径已补齐；SOL-F1R2-1 仍未关闭，原因是无有效声明的字典被认定为操作员声明，且合法的旧式操作员声明被附加了未记录的 HTTP lease 说明。两项均为原恢复披露边界内的 P2 问题。P4-05 暂不能 PASS，不生成下一卡。**

## 1. 范围与基线

仅复核 [上轮报告 §3](P4-05-sol-f1r2-focused-review.md) 指定的 SOL-F1R2-1：[披露修复记录](P4-05-sol-f1r2-disclosure-fix.md)、`backup.py::_restored_consistency/restore_backup`、CLI JSON 出口与对应定向证据。没有重跑全量、租约探针或恢复故障矩阵，也没有扩大到维护 CLI 租约、remap、升级回退、打包、真实图库。

基线 `HEAD=f00955e` 加未提交工作树。当前 SHA-256 前 16 位：

| 文件 | SHA-256 前 16 位 |
|---|---|
| `backup.py` | `aa19007e1111c1e4` |
| `tests/unit/test_p4_05_backup_restore.py` | `9e6b2fd9a1657932` |
| `__main__.py` | `1b9f0496394b136c` |
| `runtime/data_root_lease.py` | `05e30d6f1482dacf` |
| `app.py` | `c75da20621936123` |

后面三项与前次审查一致；本轮没有修改产品或测试源文件。

## 2. 已修复的部分

- `restore_backup` 成功结果已调用 `_restored_consistency(manifest)` 并返回 `consistency`。
- 当前新包声明原样携带，说明明确不证明所有可能写者已经停止。
- 缺失声明或非字典声明返回 `status=unknown/declaration=null`，没有自动补成确认。
- CLI 直接输出恢复返回值；实现方已有新包 restore CLI 的退出码 0、干净 stderr、JSON 声明断言。

因此本次不是恢复输出字段仍缺失，而是新增字段中的判定及文字还存在两个直接边界问题。

## 3. SOL-F1R2-1 剩余问题（P2）

### A. 字典类型不能证明存在有效停机声明

`backup.py` 审查时点第 282 行只检查 `isinstance(declaration, dict)`。当 manifest 为：

```json
{"consistency": {}}
```

当前返回 `status=operator_declared`，并附上操作员停机确认与 HTTP lease 的说明。空对象没有记录任何停机声明，应显示未知/未确认。只认字典类型也会让无法识别的字典形状走同一分支，与修复记录“无法识别时 unknown”的承诺不符。

### B. 合法旧式声明被附加了未记录的租约事实

第一轮确认修复已实际使用过以下 manifest 声明：

```json
{"consistency": {"mode": "stopped", "operator_confirmed": true}}
```

它记录了操作员确认，未记录 HTTP lease。当前第 294 行却统一返回：

```text
operator stopped-service confirmation plus the managed HTTP service lease; this does not prove every possible writer was stopped
```

后半句限制了全局保证，仍不能消除前半句多声称了一个未记录事实的问题。合法旧包应保留操作员声明，并明确未记录受控 HTTP lease，不能一概采用当前新包的说明。

**最小回修：**停机声明识别至少核对支持的 `mode` 和明确的操作员确认字段；空对象/无法识别形状不得表述为已确认。说明依据实际记录生成：只有记录了 `managed_service_lease=acquired` 的包才说明该受控 HTTP lease 事实；旧式操作员声明仅说明已记录的操作员确认，并注明租约记录缺失。保留原始声明，不升级为程序验证全部写者已停止的证明。补恢复 API/CLI 的直接边界断言即可，不改变恢复发布、租约、checkpoint、schema 或全局协议。

## 4. 证据与验证

| 项目 | 结果 |
|---|---|
| 实现方定向 JUnit（本轮实读） | `test-results/p4-05-sol-f1r2-disclosure-focus.xml`：17 tests，17 passed，0 failures/errors/skipped，15.182 s；这不是本轮复跑 |
| 本轮独立披露小探针 | `test-results/p4-05-sol-disclosure-review-probe.xml`：5 tests，3 passed，2 failures，0 errors/skipped |
| 小探针细节 | `tmp/p405-sol-disclosure-review/probe.json` 保存五个输入、预期、实际输出及完整源文件 SHA-256 |
| 正例 | 当前新包声明保留、缺失声明 unknown、非字典声明 unknown，共 3 项符合预期 |
| 反例 | 空字典误标为操作员声明；旧式操作员声明被附加 HTTP lease 说明，共 2 项不符合预期 |
| 本轮 Git 检查 | `git diff --check` 退出码 0；产品未修改 |

小探针通过 AST 从当前 `backup.py` 只提取 `_restored_consistency`，在内存中执行五种 manifest 输入，没有导入产品模块、打开 catalog、复制数据根或调用恢复流程。它验证的是真实 helper 的披露结果，不冒充端到端恢复测试。命令形式为项目 `.venv/Scripts/python.exe -B -c <内存探针>`；探针按两个断言失败返回 1，JUnit 与 JSON 为实际产物。本轮新增产物只有探针报告/JSON、本报告和任务卡/状态交接。

## 5. 交接与保留裁决

F1R2-2 仍接受为非阻断：操作员停掉 HTTP、第一方维护 CLI 及直接写库/派生物进程，并在整个复制期间保持停机；不重开维护 CLI 租约接线。此前 DS-H 完整受影响面结论保留，既有全量结果不追认为当前披露修复的独立复跑。

下一步只修本报告 §3 的识别与说明文字，补两个直接边界，再做 SOL-F1R2-1 短审。不生成下一任务卡，不授予 P4-05 整体、发行备份或个人试用全部门槛 PASS。
