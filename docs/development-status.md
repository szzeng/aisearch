# Development status

## Current user-directed task: P4-UI-PHOTOS-B — FUNCTIONAL SCOPED PASS / LUNA HIGH REVIEW COMPLETE / BROWSER AND PERF_200K PENDING (2026-10-14)

用户明确要求在 A/C/CORE 验收门尚未关闭时先执行本卡。照片页默认统一时间轴、保留列表/图库管理；LUNA High 受影响面复核发现的 4 项 P2 已修复并补回归，最终复核无剩余 findings。定向 JUnit **28/0/0/0**，关联 timeline integration **1/0/0/0**；Ruff、Node、diff-check 通过。[任务卡](tasks/P4-UI-PHOTOS-B.md) / [报告](reports/P4-UI-PHOTOS-B.md)。真实浏览器截图与 `PERF_200K` 仍 pending，不标整卡 PASS。

## Next necessary action: 完成 PHOTOS-B 浏览器与性能验收门 — NOT STARTED

补完本卡真实浏览器观察/截图、复用并确认 CORE 的 20 万性能证据后停止；用户指定的 LUNA High 受影响面复核已完成。D 卡 [P4-UI-PEOPLE-D](tasks/P4-UI-PEOPLE-D.md) 已存在但尚未启动。A/C/CORE 的既有未验收状态与报告不因本卡实现而关闭。

## Previous completed task: P4-UI-TIMELINE-REVISION-A — IMPLEMENTED / LUNA HIGH REVIEW COMPLETE (2026-10-13)

[Task card](tasks/P4-UI-TIMELINE-REVISION-A.md), [report](reports/P4-UI-TIMELINE-REVISION-A.md). Reused `catalog_filter_revision` for scanned new assets, source removal, collection membership/deletion, and changed person visibility; person operations and capture-time/place mutations retain existing hooks. User chose the existing per-file scanner transaction as the revision unit: each newly visible asset commits with one bump in that transaction; no-op/repeated content does not bump. Task-card JUnit is **14/0/0/0**; Ruff, `py_compile`, and diff-check pass. Regression covers single-location detach, duplicate-location detach no-op, first binding to a new source, and same-source duplicate content without an unnecessary bump. **LUNA High final repair review complete with no remaining findings**. No schema/API/cursor protocol change; existing uncommitted `photo-timeline.js` edits were preserved.

## Predecessor status: P4-UI-TIMELINE-CORE-A — PARTIAL / VALIDATION PENDING (2026-10-13)

[Task card](tasks/P4-UI-TIMELINE-CORE-A.md), [report](reports/P4-UI-TIMELINE-CORE-A.md). Timeline API/core and shared component are implemented. Browser evidence and `PERF_200K` remain pending; the revision task does not close CORE-A. Do not start B until CORE-A blockers close.

## Predecessor status: P4-UI-SHELL-A-F1 — IMPLEMENTED / VISUAL VALIDATION PENDING (2026-10-13)

[Task card](tasks/P4-UI-SHELL-A-F1.md), [report](reports/P4-UI-SHELL-A-F1.md), [visual tokens](plans/P4-UI-VISUAL-TOKENS.md). Shared theme/navigation and search page visual scope are implemented. Focused JUnit is **37/0/0/0**, Ruff and diff-check pass. Remaining visual gates: real-browser 200% zoom and a saved search-hit screenshot; LUNA High review is not DS-H.

## Related UI status

- [P4-UI-SHELL-A](tasks/P4-UI-SHELL-A.md): IMPLEMENTED / VALIDATION PENDING; real 200%, dark and persistent screenshots remain pending.
- [P4-UI-SEARCH-C](tasks/P4-UI-SEARCH-C.md): implementation and focused engineering validation passed; full visual checks remain pending.
- [P4-UI-INTERACTIVE-DEMO](reports/P4-UI-INTERACTIVE-DEMO.md): user-confirmed visual reference only; synthetic, not product acceptance.

## Additional requested task: P4-MEDIA-CR2-A

[Task card](tasks/P4-MEDIA-CR2-A.md), [report](reports/P4-MEDIA-CR2-A.md): implementation partial / validation pending. The user authorized `D:\SZ\天空\100CANON`; five read-only CR2 samples decode through the shared RAW frame at 1.101–1.183 s each, with unchanged SHA/size/mtime. Embedded JPEG and RAW geometry did not establish reliable crop mapping. Focused regression and static checks pass; real face/OCR inference, active-model text search, valid-GPS/portrait coverage, actual continuation idempotence, frozen-package execution, and old face/OCR terminal-job recovery remain pending. Do not claim SCOPED PASS.
