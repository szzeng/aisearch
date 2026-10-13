# Development status

## Current user-directed task: P4-UI-TIMELINE-CORE-A — PARTIAL / VALIDATION PENDING (2026-10-13)

[Task card](tasks/P4-UI-TIMELINE-CORE-A.md), [report](reports/P4-UI-TIMELINE-CORE-A.md). User explicitly directed CORE despite F1 visual gates still pending; this does not close F1. Timeline API/core and shared component are implemented; focused JUnit is **7/0/0/0**, Ruff, py_compile, Node syntax and diff-check pass. The requested 30万 fixture was reused from `tmp/p402f-阶段0/scale-300000-wave/data` via an isolated migrated copy; the mother catalog remains unchanged. 30万 P50: first page 2116 ms, page 2 1815 ms, year/month seek 1780/1793 ms, pending page 1805 ms, year summary 3424 ms; plans scan all assets and use temporary sort/group B-trees. The source fixture has no taken-time metadata/persons/collections; 20万 was not run per the user's instruction. LUNA High's reviews found summary-generation, stale-cursor, revision-display, cross-page heading, and listener cleanup issues; fixes are implemented, including one-shot anchor reposition and strict stale cursor handling, with focused checks rerun. Final LUNA High read-only review found no remaining implementation defect in the reviewed changes; this user-routed review is not DS-H. New asset ingestion still does not bump the shared cursor revision and remains a card-level blocker; do not silently change write/schema semantics. **BROWSER_PENDING**, **PERF_200K unverified**, **LUNA High review complete**. Do not start B until blockers close.

## Predecessor status: P4-UI-SHELL-A-F1 — IMPLEMENTED / VISUAL VALIDATION PENDING (2026-10-13)

[Task card](tasks/P4-UI-SHELL-A-F1.md), [report](reports/P4-UI-SHELL-A-F1.md), [visual tokens](plans/P4-UI-VISUAL-TOKENS.md). Shared theme/navigation and the search page visual scope are implemented. Focused JUnit is **37/0/0/0** after fixing the Luna High P2 contrast and decorative-icon findings; Ruff and `git diff --check` pass. Synthetic browser observations covered the six-result filter view, advanced filters, Chinese query error, and shared navigation across `/`, `/search`, `/persons`, `/places`, `/map`, and `/?view=sources`.

Follow-up fixes: suppress the decorative focus outline on home `<main>` and focus it with `preventScroll` so gallery-management navigation does not move the viewport; style only the map photo panel scrollbar with theme colors and a thinner track. Interactive control focus-visible styling and map scroll behavior remain. F1 JUnit **37/0/0/0**, Ruff, `node --check app.js` (for the prior focus fix), and `git diff --check` pass. Shared stylesheet cache is `p4-ui-shell-a-f1-6`; `app.js` is `p4-ui-shell-a-2`; `map.css` is `p4-ui-shell-a-2`.

Chrome screenshots now cover `/` at 1440×1200 light with six synthetic cards, `/search` at exact 1440 and 640 CSS viewports in light mode, dark theme at 1440, and expanded advanced filters at exact 1440×1200. Visual gates remain pending for real browser 200% zoom and a saved search-hit state; the in-app browser observed six FILTER_ONLY hits but its screenshot was not saved. Chrome zoom API/shortcut attempts did not change the effective zoom, and those diagnostic images are not acceptance evidence. Luna High review passed after the contrast and decorative-icon fixes; this is not DS-H. F1 remains validation-pending; the user explicitly directed CORE to start, which does not close these F1 evidence gates.

## Related UI status

- [P4-UI-SHELL-A](tasks/P4-UI-SHELL-A.md): IMPLEMENTED / VALIDATION PENDING. Real 200%, dark, persistent screenshots remain pending; historical LUNA High review passed, not DS-H.
- [P4-UI-SEARCH-C](tasks/P4-UI-SEARCH-C.md): IMPLEMENTED / focused engineering validation and LUNA High review passed; complete visual checks remain pending.
- [P4-UI-INTERACTIVE-DEMO](reports/P4-UI-INTERACTIVE-DEMO.md): user-confirmed visual reference only; synthetic, not product acceptance.

## Next step

Close P4-UI-TIMELINE-CORE-A's new-asset revision blocker, browser evidence, and performance decision before starting [P4-UI-PHOTOS-B](tasks/P4-UI-PHOTOS-B.md). P4-UI-SHELL-A-F1's 200% and saved search-hit screenshot gates remain pending. Do not start subsequent UI cards automatically.

## Additional requested task: P4-MEDIA-CR2-A

[Task card](tasks/P4-MEDIA-CR2-A.md), [report](reports/P4-MEDIA-CR2-A.md): implementation partial / validation pending. The user authorized `D:\SZ\天空\100CANON`; five read-only CR2 samples decode through the shared RAW frame at 1.101–1.183 s each, with unchanged SHA/size/mtime. Their 6000×4000 embedded JPEG versus 6024×4020 RAW geometry did not establish a reliable crop mapping, so preview remains unused. The specified focused regression set is **127/0/0/0**; Ruff, `py_compile`, `uv lock --check`, `git diff --check`, and rawpy collector smoke pass. Real face/OCR inference, active-model text search, valid-GPS/portrait coverage, actual continuation idempotence, full frozen-package execution, and old face/OCR terminal-job recovery remain pending. Do not claim SCOPED PASS. The primary UI handoff above remains unchanged.

Historical status snapshot: [development-status-archive-2026-10-13.md](development-status-archive-2026-10-13.md).
