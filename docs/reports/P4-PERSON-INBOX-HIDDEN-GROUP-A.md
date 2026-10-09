# P4-PERSON-INBOX-HIDDEN-GROUP-A 实现与定向验证

日期：2026-10-09。基线：`31d3690b3d4fbf9a558335a1bb947a00c52df49e`。结论：**PASS / 定向验证通过，用户已确认功能可用**。

## 实现

- 增加只读 GET `/faces/inbox/hidden/groups` 展示卡分页与 GET `/faces/inbox/hidden/groups/{group_key}` 组内分页；原 GET `/faces/inbox/hidden` 响应保持不变。
- 从所有现存隐藏标记脸中，按每脸当前有效首选 SUGGESTED 人物候选归组，排除合并候选和该脸对候选人物的 active exclusion。至少跨 2 张不同照片的候选才显示合集；同图多脸仍按脸逐项展开，其余脸显示独立卡片。
- 组/单脸卡片按不同照片数降序、稳定键升序分页；展开按 face_id 分页，复用原脸信息与受保护缩略图。列表与展开操作不写人物关系、身份、建议或隐藏标记，不计算脸向量相似度。
- 隐藏列表改用候选组及单脸卡片；仍保持显式 face_id 选择、预览独立、恢复请求只包含已勾选人脸。成功恢复后刷新列表并尝试恢复仍存在的展开组。

## 变更文件

- `src/photo_search/persons/albums.py`
- `src/photo_search/api/routes.py`
- `src/photo_search/web/persons-albums.js`
- `tests/integration/test_p4_person_albums.py`
- `tests/unit/test_person_inbox_hide_ui.py`

## 验证

- 定向 pytest（后端隐藏分组集成 + Node DOM）：**2 passed / 0 failed / 0 errors / 0 skipped**。
- JUnit：`test-results/p4-person-inbox-hidden-group-a.junit.xml`。
- 修改面 Ruff、`node --check src/photo_search/web/persons-albums.js`、`git diff --check`：通过。
- 合成集成覆盖同图多脸与跨图候选、按 distinct 照片分组计数、单脸留列、组内分页成员精确及读取前后 catalog 不变。DOM 覆盖组标题不批量选择、展开跨页、预览与选择隔离、仅恢复明确 face_id 及刷新。
- 未运行全仓、浏览器矩阵、性能/GPU/模型/打包；未访问 PhotoSearchData 或现有服务，提交后未推送。第一次 DOM 运行的测试夹具 JavaScript 有语法错误，调整夹具后按原命令重跑通过。

## 后续

用户已在真实页面验证功能可用。工程测试证据仍限于合成 API 与 DOM；未据此声称全仓、真实图库规模或发行验证通过。
