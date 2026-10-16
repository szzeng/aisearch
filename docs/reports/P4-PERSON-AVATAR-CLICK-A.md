# P4-PERSON-AVATAR-CLICK-A — 待整理/已忽略及合集独立人脸保留头像打开

- 日期：2026-10-16；状态：IMPLEMENTED / BASIC VALIDATED（已补齐未识别人脸下的已忽略人脸）。
- 用户最终范围：待整理未识别人脸、该入口下已忽略人脸、合集单独收录人脸使用新交互；其他人物头像保持原模式。
- 实现：用户指定的 Luna High 子代理；主代理复核代码、恢复范围与 JUnit 证据。
- [任务卡](../tasks/P4-PERSON-AVATAR-CLICK-A.md)。

## 最终行为

| 区域 | 管理中点击头像 | 底框与 checkbox |
| --- | --- | --- |
| 待整理 → 未识别人脸 | 打开对应照片，不改变选择 | 切换选择；Shift 范围加选，checkbox 在头像之外 |
| 合集 → 单独收录的人脸 | 打开对应照片，不改变选择 | 底框普通点击单项切换，Shift 范围加选，checkbox 在头像之外 |
| 普通/未命名/已忽略人物、合集人物成员 | 恢复原批量选择行为 | 恢复原布局与逻辑 |
| 未识别人脸 → 已忽略的人脸 | 打开对应照片，不改变选择 | 切换选择；checkbox 移出头像，范围锚点与待整理独立 |

待整理与已忽略人脸共用头像浏览、键盘打开和底框选择逻辑；参考头像与业务按钮不触发底框选择。已忽略头像预览不改变选集或 Shift 锚点，底框可选择/取消，恢复请求 payload 保持原语义。保留 busy、pendingWrite、stale、独立选集和 500 上限保护，以及此前头像 hover；不改后端、人工归属/撤销、数据库、真实图库或共享查看器。

主代理逐函数与当前 HEAD（7330bfd）比较，`updatePersonCard`、`createPersonCard`、`activateCollectionPerson`、`loadCollectionPeople`、`backToLibrary` 完全一致，确认人物头像导航和合集返回快照已移除。最终 JS 差异仅为 `contains`、`collectionFaceCard`、`toggleCollectionFace`、`renderInboxFace`；CSS checkbox 静态布局仅限 `#pa-inbox-cards`、`#pa-inbox-hidden-cards` 和 `#pa-collection-faces`。脚本与样式资源版本为 `p4-person-avatar-click-a-3`，直接断言已同步。

## 简单验证

使用项目 Python 3.12.10 `.venv`、Node v24.21.0；uv cache 使用 `.uv-cache`，pytest 临时文件在 `.pytest-tmp`。最终实际命令：

```powershell
$env:UV_CACHE_DIR='.uv-cache'
.venv/Scripts/python.exe -m pytest tests/unit/test_person_inbox_hide_ui.py tests/unit/test_person_loaded_pages_ui.py tests/unit/test_p4_person_collection_count_ui.py tests/unit/test_photo_view_info_ui.py::test_all_photo_pages_load_the_versioned_shared_viewer -q --junitxml=test-results/P4-PERSON-AVATAR-CLICK-A-ignored.xml --basetemp=.pytest-tmp/avatar-click-ignored-20261016-1
node --check src/photo_search/web/persons-albums.js
.venv/Scripts/python.exe -m ruff check tests/unit/test_person_inbox_hide_ui.py tests/unit/test_person_loaded_pages_ui.py tests/unit/test_p4_person_collection_count_ui.py tests/unit/test_photo_view_info_ui.py
git diff --check
```

最新 JUnit **4 passed / 0 failures / 0 errors / 0 skipped**，已读取 XML 确认。Node、Ruff、diff-check 通过。前轮两组去重 16 项属于补齐已忽略人脸前的历史证据，本轮只重跑直接受影响的四项检查，不将旧结果作为新版本的完整回归结论。

本轮 DOM 检查覆盖待整理/已忽略头像浏览、参考区排除、已忽略头像预览不改选集/锚点、checkbox 在头像外、底框切换及恢复请求；同时回归普通人物/合集成员头像仍选择，以及合集独立脸头像浏览、底框切换和 Shift 范围，并检查资源版本。未重复原 12 项整理回归。

## 交付边界

- 浏览器交互、截图和视觉：`BROWSER_NOT_RUN_USER_REQUEST`。合成 DOM 不代替浏览器验收，不标整体或发行 PASS。
- 未运行全仓、真实图库、GPU、模型、性能、打包测试。
- 按用户追加要求本地范围提交，不推送；共享会话 `7330bfd` 的照片详情修改保留，此后仅定点更新人物资源版本及相关断言。
- 下一步为用户日常试用反馈，不自动扩展新任务。上述复核为主代理对实现的限定复核，不冒充独立 DS-H 工程审查。
