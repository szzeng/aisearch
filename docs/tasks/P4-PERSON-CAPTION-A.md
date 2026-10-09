# P4-PERSON-CAPTION-A — 移除人物相册照片冗余标签

日期：2026-10-09。状态：**PASS / 定向 DOM 验证通过**。基线：`eae58ea`。

## 目标与范围

人物相册照片卡片不再显示“这张照片里的这个人”；保留卡片中的人脸缩略图和其他必要提示。合集照片卡片原有说明保持不变。

仅修改 `src/photo_search/web/persons-albums.js` 及对应 DOM 回归测试。不改 API、数据、其他人物操作或真实图库。

## 验收

- 定向 JUnit：`tests/unit/test_person_album_photo_caption_ui.py`。
- `node --check src/photo_search/web/persons-albums.js`。
- 对测试文件运行 Ruff；`git diff --check`。

已全部通过；细节见 [执行报告](../reports/P4-PERSON-CAPTION-A.md)。下一步继续 P4-PERSON-QUALITY-A 的少量个人试用；无后续工程卡。
