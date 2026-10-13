# Development status

## Current user-directed task: P4-UI-TIMELINE-REVISION-A — IMPLEMENTED / LUNA HIGH REVIEW COMPLETE (2026-10-13)

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
