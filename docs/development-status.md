# Development status

## Current user-directed task: P4-UI-PEOPLE-D — IMPLEMENTED / LUNA HIGH REVIEW COMPLETE / BROWSER AND PERF_200K PENDING (2026-10-14)

用户明确要求在 A/C/CORE/B 浏览器或性能门尚未关闭时开始本卡。人物与合集照片页已接入共享时间轴，保留旧局部搜图与人物整理操作；合集实例/revision 不匹配时停止显示结果并提供刷新后的当前范围重载，查看器续页沿用有界照片 ID。定向 JUnit **31/0/0/0**；Ruff、Node、diff-check 通过。用户指定的 LUNA High 受影响面复核无剩余实现 findings，不代表 DS-H。[任务卡](tasks/P4-UI-PEOPLE-D.md) / [报告](reports/P4-UI-PEOPLE-D.md)。浏览器交互/截图及 `PERF_200K` 仍 pending，故不标整卡 PASS。

## Next necessary action: 完成 PEOPLE-D 浏览器与性能验收门 — PENDING

在可用的本地合成产品浏览器中观察人物/合集切换、选择与取消、忽略恢复、待整理单脸对话框、预览返回；保存截图，并复用 CORE 的 20 万性能证据或补齐人物/合集差异证据。此前本地文件打开被浏览器安全策略拒绝，未尝试规避。D 的实现、定向 JUnit 与用户指定 Luna High 受影响面复核已完成；用户要求本卡完成后停止。A/C/CORE/B 的既有未验收门按各自报告保留，不因 D 开始或实现而关闭。

## Previous executed task: P4-UI-PHOTOS-B — FUNCTIONAL SCOPED PASS / LUNA HIGH REVIEW COMPLETE / BROWSER AND PERF_200K PENDING (2026-10-14)

照片页默认统一时间轴、保留列表/图库管理；定向 JUnit **28/0/0/0**，关联 timeline integration **1/0/0/0**；Ruff、Node、diff-check 通过。[任务卡](tasks/P4-UI-PHOTOS-B.md) / [报告](reports/P4-UI-PHOTOS-B.md)。真实浏览器截图与 `PERF_200K` 仍 pending；用户后来明确要求先执行 D，不改变 B 或 CORE 的验收状态。
## Previous completed task: P4-UI-TIMELINE-REVISION-A — IMPLEMENTED / LUNA HIGH REVIEW COMPLETE (2026-10-13)

[Task card](tasks/P4-UI-TIMELINE-REVISION-A.md), [report](reports/P4-UI-TIMELINE-REVISION-A.md). Reused `catalog_filter_revision` for scanned new assets, source removal, collection membership/deletion, and changed person visibility; person operations and capture-time/place mutations retain existing hooks. User chose the existing per-file scanner transaction as the revision unit: each newly visible asset commits with one bump in that transaction; no-op/repeated content does not bump. Task-card JUnit is **14/0/0/0**; Ruff, `py_compile`, and diff-check pass. Regression covers single-location detach, duplicate-location detach no-op, first binding to a new source, and same-source duplicate content without an unnecessary bump. **LUNA High final repair review complete with no remaining findings**. No schema/API/cursor protocol change; existing uncommitted `photo-timeline.js` edits were preserved.

## Predecessor status: P4-UI-TIMELINE-CORE-A — PARTIAL / VALIDATION PENDING (2026-10-13)

[Task card](tasks/P4-UI-TIMELINE-CORE-A.md), [report](reports/P4-UI-TIMELINE-CORE-A.md)。Timeline API/核心组件已实现；REVISION-A 已修复共享 catalog revision 信号。CORE-A 的浏览器证据、`PERF_200K` 和独立工程审查仍 pending。Integration-A 只接入首页，不代表 CORE-A 关闭。

## Predecessor status: P4-UI-SHELL-A-F1 — IMPLEMENTED / VISUAL VALIDATION PENDING (2026-10-13)

[Task card](tasks/P4-UI-SHELL-A-F1.md), [report](reports/P4-UI-SHELL-A-F1.md), [visual tokens](plans/P4-UI-VISUAL-TOKENS.md). Shared theme/navigation and search page visual scope are implemented. Focused JUnit is **37/0/0/0**, Ruff and diff-check pass. Remaining visual gates: real-browser 200% zoom and a saved search-hit screenshot; LUNA High review is not DS-H.

## Related UI status

- [P4-UI-SHELL-A](tasks/P4-UI-SHELL-A.md): IMPLEMENTED / VALIDATION PENDING; real 200%, dark and persistent screenshots remain pending.
- [P4-UI-SEARCH-C](tasks/P4-UI-SEARCH-C.md): implementation and focused engineering validation passed; full visual checks remain pending.
- [P4-UI-INTERACTIVE-DEMO](reports/P4-UI-INTERACTIVE-DEMO.md): user-confirmed visual reference only; synthetic, not product acceptance.

## Additional requested task: P4-MEDIA-CR2-A

[Task card](tasks/P4-MEDIA-CR2-A.md), [report](reports/P4-MEDIA-CR2-A.md): implementation partial / validation pending. The user authorized `D:\SZ\天空\100CANON`; five read-only CR2 samples decode through the shared RAW frame at 1.101–1.183 s each, with unchanged SHA/size/mtime. Embedded JPEG and RAW geometry did not establish reliable crop mapping. Focused regression and static checks pass; real face/OCR inference, active-model text search, valid-GPS/portrait coverage, actual continuation idempotence, frozen-package execution, and old face/OCR terminal-job recovery remain pending. Do not claim SCOPED PASS.