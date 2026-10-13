# P4-PERSON-INBOX-IGNORE-STATE-A — 待整理人脸暂时忽略与明确排除

## 用户场景

人物管理 → 待整理 → 下方待整理人脸卡片。选中一张或多张明确的人脸后，用户可选择“暂时忽略”或“明确排除”。状态只作用于被选中的 `face_id`，绝不扩展到候选人物、同照片其他人脸或该人物其他照片。

- 暂时忽略：沿用现有收件箱隐藏语义，保留已有推荐；以后从“已忽略人脸”恢复即可继续处理。
- 明确排除：当前脸不再参与任何人物推荐/匹配；保留照片、人脸、向量、归属和建议历史，可从“已忽略人脸”恢复。

## 前置核对

- 当前 HEAD、现有未提交差异和任务状态：执行时检查；保留时间线、人物 UX 和其它并行工作。
- `face_inbox_hidden` 当前只有二态标记；schema 30，迁移目录按实际连续版本生成 0031。
- 当前 `set_inbox_visibility` endpoint 已有逐 `face_id` 1–500 原子写入、会话/CSRF 保护、隐藏列表恢复；按此扩展 `ignore_state`。
- 人脸候选生成读取 `FaceIndexCoordinator.candidates()`，自动关联最终写入在 `reconcile_asset()` 的 SQLite 写事务；明确排除必须同时影响预过滤和事务内 late-result 防护。

## 架构与策略

- `个人图片搜索系统_V3.0_开发主架构.md` §4.2、§8.3.1、§8.3.4、§8.3.5。
- `个人图片搜索系统_V3.1_Codex_DeepSeek_开发执行策略.md` §2.2、§2.4、§3.2、§11。
- 参考 `P4-PERSON-INBOX-HIDE-A` 对 face_id 隐藏和 `P4-PERSON-IGNORE-STATE-A` 对旧忽略数据默认为 TEMPORARY 的约定。
- 推荐实现模型：GPT-6 High。用户要求 LUNA High 独立复核；不得声称 DS-H PASS。

## 行为与不变量

1. `face_inbox_hidden.ignore_state` 取 `TEMPORARY` / `EXCLUDED`；迁移既有每条隐藏脸为 `TEMPORARY`，无数据删除，失败事务回滚。
2. 待整理操作工具栏提供忽略状态选择；现有逐脸选择集不变，操作仍严格按选中的 face_id，支持 1–500、整批校验后原子写入和重复请求幂等。
3. `TEMPORARY` 只屏蔽待整理 UI，保留建议和自动匹配语义。
4. `EXCLUDED` 使该 face_id 不接收候选建议且不被自动关联；自动关联事务内再次读取排除状态，阻止晚到结果。该脸仍可作为其它脸的代表参考，不影响其代表人物或其它 face_id。不得移除现有 face-person links，只对该脸的 read projections 隐去建议；恢复操作解除标记，保留已有建议以便继续处理。
5. 人脸排除只针对该 `face_id`，不把其它脸、照片或 person_id 加入请求；其它人脸和该人物仍可参与所有处理。
6. 隐藏脸列表显示 TEMPORARY/EXCLUDED 状态；恢复两个状态都清除标记。旧 face_id 清理继续按既有外键生命周期工作。
7. 不修改 `face_assignment_controls` / `face_exclusions` / FACE_CONTROL operation contract，不引入识别、相似度重算、照片级屏蔽或全局人物排除。

## 允许修改

新增连续 Catalog migration、schema version 描述、face inbox models/routes/service/query、face candidate index/association gate、人物整理 HTML/JS、直接相关测试、任务报告/status。不要覆盖并行时间线与其他 UX 修改。

## 验收

- 迁移测试：旧隐藏记录全部保留并成为 TEMPORARY，非法状态受 CHECK 拒绝；失败原子回滚沿用现有通用迁移回归。
- 定向测试：迁移旧行默认 TEMPORARY；同图多脸中 TEMPORARY/EXCLUDED 状态隔离、隐藏建议及恢复；自动匹配预计算后 EXCLUDED 状态先提交时 late result 不写链接，且 excluded face 仍保留为其它脸的代表参考；人物页和 assignment read projection 均不报告 excluded face 有建议；DOM 请求保留所选状态与 face_id。
- DOM 测试：选择状态随当前工具栏写请求发送；只作用选中的 face_id；确认取消/失败保持选择；成功刷新 inbox / hidden 列表；显示清楚状态。
- 验证范围按用户最新要求收敛为定向测试及修改面静态检查；全量 unit+integration 不作为本任务验收门槛，任何额外尝试需在报告单独注明结果。
- 按用户要求完成 LUNA High 全受影响面独立复核，覆盖迁移、API、查询、face index/processor、UI、测试、TEMPORARY/EXCLUDED 与 late-result、恢复边界；不能记作 DS-H/Sol。

## 禁止范围与停止点

不读取、扫描或修改真实照片库；不跑真实识别、模型/GPU、30 万、全浏览器矩阵或打包。若自动匹配的预计算/提交顺序无法证明 EXCLUDED 不会被晚到结果写入，最小反例保持 BLOCKED，不得仅靠 UI 过滤并报告 PASS。

完成后更新 `docs/reports/P4-PERSON-INBOX-IGNORE-STATE-A.md` 和 `docs/development-status.md`。下一既有必要闭环仍是 `P4-UI-TIMELINE-INTEGRATION-VERIFY-A` 浏览器验证。
