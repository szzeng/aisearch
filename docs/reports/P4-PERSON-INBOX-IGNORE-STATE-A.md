# P4-PERSON-INBOX-IGNORE-STATE-A report

## Result

**SCOPED PASS — LUNA High affected-surface review complete (not DS-H).** The pending-face toolbar now applies TEMPORARY or EXCLUDED state only to selected `face_id` values. EXCLUDED faces receive no person recommendations and are skipped as automatic-match targets; they remain eligible as reference faces for matching other people. Existing links and recommendation history are retained and become visible again after restoration.

## Implementation

- Added schema migration 0031. Existing inbox-hidden rows default to `TEMPORARY`; the state is constrained to `TEMPORARY` or `EXCLUDED`.
- Extended the existing atomic inbox visibility endpoint and UI selector. The hidden-face list shows each state; restore removes the marker.
- Suppressed excluded-face suggestions in inbox, person-face, and assignment-state projections.
- Rechecked `EXCLUDED` inside the automatic association write transaction to prevent a precomputed late result from matching the selected face. The face remains in the candidate reference pool for other faces.
- Preserved excluded detections through face reprocessing. Advanced the reviewed legacy place-package compatibility ceiling to schema 31.
- Updated schema downgrade fixtures to recreate their historical pre-31 table shapes.

## Verification

- Focused JUnit: **7 passed, 0 failed, 0 errors, 0 skipped**, saved at `test-results/P4-PERSON-INBOX-IGNORE-STATE-A-focused.xml`. Covers migration default/check, schema 24 and schema 29 upgrade fixtures, inbox TEMPORARY/EXCLUDED/restoration, late exclusion between candidate computation and commit, continued use as a representative face, both recommendation read projections, DOM payload, and schema 30 place-package compatibility at schema 31.
- Ruff: passed for the changed Python implementation and tests.
- `node --check src/photo_search/web/persons-albums.js`: passed.
- `py_compile` for changed Python modules and tests: passed.
- `git diff --check`: passed.

An additional broad unit+integration sweep was attempted after the initial collection path was corrected. The sweep completed 2,433 collected tests with 104 failures and 50 skips; collection excluded the unrelated malformed `tests/integration/test_p4_02y_l_identity.py` and the large `test_p4_place_cn_300k.py`. Failures span UI, place, search, and runtime areas; this sweep is not used as task acceptance evidence. Three issues introduced by the schema 31 transition (two downgrade fixtures and the package compatibility ceiling) were repaired, and their focused regressions pass. A CUDA-related test also emitted a Windows ONNX Runtime native exception during that broad run. The broad sweep was not rerun after these repairs, and no broad-suite PASS is claimed.

## LUNA High review

LUNA High identified three findings: filtering an excluded face from the global representative pool was broader than face-local scope; two suggestion-availability projections still exposed excluded-face suggestions; and the schema 31 increase exceeded the reviewed place-package compatibility ceiling. Repairs keep the face in the reference pool, filter both read projections, and raise the reviewed ceiling to 31 with a schema-30 compatibility regression. LUNA High then confirmed all three findings closed and found no new high-risk issue in the affected surface. This is not a DS-H review.

## Boundaries and handoff

No real photo library, recognition model, GPU inference, packaging, or real-browser matrix was used. The next necessary task remains `P4-UI-TIMELINE-INTEGRATION-VERIFY-A` browser validation.
