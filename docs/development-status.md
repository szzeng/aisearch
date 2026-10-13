# Development status

## Current task: P4-UI-SHELL-A-F1 — IMPLEMENTED / VISUAL VALIDATION PENDING (2026-10-13)

[Task card](tasks/P4-UI-SHELL-A-F1.md), [report](reports/P4-UI-SHELL-A-F1.md), [visual tokens](plans/P4-UI-VISUAL-TOKENS.md). Shared theme/navigation and the search page visual scope are implemented. Focused JUnit is **37/0/0/0** after fixing the Luna High P2 contrast and decorative-icon findings; Ruff and `git diff --check` pass. Synthetic browser observations covered the six-result filter view, advanced filters, Chinese query error, and shared navigation across `/`, `/search`, `/persons`, `/places`, `/map`, and `/?view=sources`.

Follow-up fixes: suppress the decorative focus outline on home `<main>` and focus it with `preventScroll` so gallery-management navigation does not move the viewport; style only the map photo panel scrollbar with theme colors and a thinner track. Interactive control focus-visible styling and map scroll behavior remain. F1 JUnit **37/0/0/0**, Ruff, `node --check app.js` (for the prior focus fix), and `git diff --check` pass. Shared stylesheet cache is `p4-ui-shell-a-f1-6`; `app.js` is `p4-ui-shell-a-2`; `map.css` is `p4-ui-shell-a-2`.

Chrome screenshots now cover `/` at 1440×1200 light with six synthetic cards, `/search` at exact 1440 and 640 CSS viewports in light mode, dark theme at 1440, and expanded advanced filters at exact 1440×1200. Visual gates remain pending for real browser 200% zoom and a saved search-hit state; the in-app browser observed six FILTER_ONLY hits but its screenshot was not saved. Chrome zoom API/shortcut attempts did not change the effective zoom, and those diagnostic images are not acceptance evidence. Luna High review passed after the contrast and decorative-icon fixes; this is not DS-H. Do not mark this task PASS or start CORE until required evidence is complete.

## Related UI status

- [P4-UI-SHELL-A](tasks/P4-UI-SHELL-A.md): IMPLEMENTED / VALIDATION PENDING. Real 200%, dark, persistent screenshots remain pending; historical LUNA High review passed, not DS-H.
- [P4-UI-SEARCH-C](tasks/P4-UI-SEARCH-C.md): IMPLEMENTED / focused engineering validation and LUNA High review passed; complete visual checks remain pending.
- [P4-UI-INTERACTIVE-DEMO](reports/P4-UI-INTERACTIVE-DEMO.md): user-confirmed visual reference only; synthetic, not product acceptance.

## Next step

Finish P4-UI-SHELL-A-F1 visual gates. Only then continue to [P4-UI-TIMELINE-CORE-A](tasks/P4-UI-TIMELINE-CORE-A.md). Do not start subsequent UI cards automatically. The 20万有效照片元数据 performance requirement remains unchanged.

## Additional requested task: P4-MEDIA-CR2-A

[Task card](tasks/P4-MEDIA-CR2-A.md), [report](reports/P4-MEDIA-CR2-A.md): implementation partial / validation pending. The user authorized `D:\SZ\天空\100CANON`; five read-only CR2 samples decode through the shared RAW frame at 1.101–1.183 s each, with unchanged SHA/size/mtime. Their 6000×4000 embedded JPEG versus 6024×4020 RAW geometry did not establish a reliable crop mapping, so preview remains unused. The specified focused regression set is **127/0/0/0**; Ruff, `py_compile`, `uv lock --check`, `git diff --check`, and rawpy collector smoke pass. Real face/OCR inference, active-model text search, valid-GPS/portrait coverage, actual continuation idempotence, full frozen-package execution, and old face/OCR terminal-job recovery remain pending. Do not claim SCOPED PASS. The primary UI handoff above remains unchanged.

Historical status snapshot: [development-status-archive-2026-10-13.md](development-status-archive-2026-10-13.md).
