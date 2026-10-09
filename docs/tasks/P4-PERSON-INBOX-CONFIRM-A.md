# P4-PERSON-INBOX-CONFIRM-A — 候选人脸直接确认

日期：2026-10-09。状态：**PASS / PERSONAL-SCOPED REVIEW（用户指定简单验收）**。基线：`65bec73`。

## 目标

在人物待整理收件箱里，对显示“像‘XX’，需确认”的单脸提供直接“确认是XX”操作；用户不必先打开“这是谁？”再搜索/选同一候选。错误候选仍可用“选其他人”进入原逐脸选择流程；无建议时保留“这是谁？”。

## 实施范围与不变量

- 只改 `src/photo_search/web/persons-albums.js` 与其现有行为测试 `tests/unit/test_person_rematch_ui.py`，另更新本卡、报告和开发状态。
- 复用 `PUT /faces/{face_id}/assignment` 的 `assign`；只传当前卡片的 `face_id`、建议 `person_id`、`control_revision`、请求 ID 和既有 contract version。
- 保持每脸单独人工归属、CSRF、revision 冲突、人工排除/锁定、幂等及撤销语义。不给候选组增加批量确认；不改 API、数据库、模型或匹配阈值。
- 人物收件箱及单脸纠错约束参考 `P4-PERSON-INBOX-GROUP-SORT-A` 的架构章节 V3.0 §8.3.1、§8.3.4、§10.2；本次只消费既有建议和写接口，不修改跨模块协议。

## 验收

- 合成 UI 行为确认：按钮文案/候选替代入口、CSRF 请求、目标人物 ID、`face_id` 路径及 expected revision。
- 复用单脸写入与撤销集成用例核验现有服务端保护；运行定向 pytest、Ruff、Node 语法及 diff-check。
- 不访问真实图库/服务，不做浏览器现场或全仓回归。用户授权在当前会话简审，不要求独立模型轮审。
