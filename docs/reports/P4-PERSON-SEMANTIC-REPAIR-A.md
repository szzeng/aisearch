# P4-PERSON-SEMANTIC-REPAIR-A — implementation report

**Status: IMPLEMENTED / focused validation passed; full affected-surface review passed; broad B/D suite gate remains failed.**

## Baseline and scope

- Baseline: `6076090` (`docs(status): keep dated introduction before active tasks`); worktree was clean before implementation.
- Changed product files: `src/photo_search/search/documents.py`, `src/photo_search/web/persons-albums.js`.
- Added isolated regression coverage: `tests/integration/test_p4_person_semantic_repair.py` (3 cases).
- No schema, migration, vector/search protocol, model, or dependency changes. No real `PhotoSearchData`, user service, model, or GPU was opened.

## Diagnosis and implementation

### A — CLI cold-start import loop

The reproduction of `python -m photo_search.indexing.cli --help` failed before argument parsing with the documented partially initialized module error. The cycle was `indexing.maintenance → scheduler.projection → persons → api.queries → search.documents → indexing.maintenance`.

Moved only `is_busy_rejection` into the `CatalogError` exception path in `search.documents.catch_up()`. This preserves the busy classifier, bounded retry/wait behavior, and error propagation, while allowing the maintenance CLI to import cold.

Fresh Python subprocesses successfully ran `--help`, `status --data-root <isolated-root> --json`, and `drain --data-root <isolated-root> --limit 0 --json`; the zero-limit drain reported zero writes. The core regression also called the production `drain_semantic_outbox()` with one real pending synthetic event and observed it confirmed.

### B — synthetic index-state classification

A synthetic three-photo catalog used the current generation/space and `persons.members.MEMBER_PREDICATE` with distinct asset counts. Before the simulated rename, the selected person had a vector hit. After rename:

- `PERSON_CHANGED` covered two distinct member assets once.
- The member with a current vector entry transitioned `CONFIRMED → PENDING_REPAIR`; its newest outbox revision was 1, `PENDING`, with no failure code.
- The second member was deliberately seeded as `NO_ENTRY`; its old revision-0 outbox row remained `CONFIRMED`, but no `index_entries` row existed. It remained `NO_ENTRY` after drain and was never falsely reported as confirmed.
- The four-way member count was `total=2, CONFIRMED=0, PENDING_REPAIR=1, STALE=0, NO_ENTRY=1`.
- Replaying the same rename request created no additional event.

The bounded production drain applied the one queued projection using the existing embedding artifact. It added no artifact and performed no image encoding. A subsequent person-scoped search again returned the matching member photo and excluded the other person's photo. This confirms the pending/outbox branch in a synthetic catalog; it does not establish which branch the real 131-photo catalog currently has.

No automatic worker was added. The existing controlled operator entry is the repeatable catch-up path after naming or when the album empty-state appears:

```powershell
uv run python -m photo_search.indexing.cli status --data-root "<configured PhotoSearchData root>" --json
uv run python -m photo_search.indexing.cli drain --data-root "<configured PhotoSearchData root>" --json
```

The default drain is bounded by its existing limit and can be rerun safely. If status/drain reports an artifact awaiting re-execution or a failure code, follow that existing recovery branch; do not set entries to `CONFIRMED` manually. `NO_ENTRY` photos are not repaired by drain; only if a read-only diagnosis confirms missing vector artifacts should the existing incremental build selector be considered. A whole-library rebuild is not required for `PENDING_REPAIR` events.

### C — person-album empty state

When existing response fields show authoritative album photos, vector engaged, `engine_blocks=0`, and no FTS candidates, the album now says “此人物的语义索引尚未就绪，可先查看全部照片。” If the engine scanned blocks but found no hit, it keeps “没有找到匹配的照片。” The frozen `POST /api/v1/search` response was not changed. The “全部照片” browse path remains available.

## Validation

- Focused JUnit: **3 tests / 0 failures / 0 errors / 0 skipped** (`tmp/test-reports/P4-PERSON-SEMANTIC-REPAIR-A-focused.xml`), including cold CLI subprocess smoke, rename/drain/search recovery and no-entry protection, and UI empty-state conditions.
- Ruff: passed for both changed Python files.
- `node --check src/photo_search/web/persons-albums.js`: passed.
- `py_compile` for changed Python: passed.
- `git diff --check`: passed.
- Required broad B/D suite was attempted but did not pass. Initial collection failed on repository import-path setup and the unrelated syntax-invalid `tests/integration/test_p4_02y_l_identity.py`. Retried with repository `PYTHONPATH` and excluded only that syntax-invalid file: **2253 tests / 70 failures / 43 errors / 49 skipped** (`tmp/test-reports/P4-PERSON-SEMANTIC-REPAIR-A-D-with-import-path.xml`). The run also emitted a Windows ONNX Runtime fatal exception (`0xc0000139`) but continued to a pytest summary. JUnit inspection showed unrelated current fixture/contract failures including `PLACE_NOT_FOUND` setup errors and missing `face_detail_auto_assets`; no case in the new test file failed. These results are not a passing broad gate, and no claim is made that every failure is pre-existing.
- No full-suite baseline at commit `6076090` was run, so the broad-suite failures cannot be conclusively labeled pre-existing.

## Open acceptance items and handoff

1. Full affected-surface engineering review is complete in this session at the user's direction; DS-H was not used. See the review disposition below and [review card](../tasks/P4-PERSON-SEMANTIC-REPAIR-A-REVIEW.md).
2. The broad B/D gate remains failed/pending under V3.1 §3.2–3.4; do not mark this task PASS until the gate is resolved under repository policy.
3. The user-provided `person_id=19`, 131 authoritative photos, zero `CONFIRMED`, and 2924 library-wide count remain the only real-library facts used. The real catalog/outbox, current generation, and failure codes were not read. After review and a valid local install/service update, the user can run the read-only status, drain, and one-person search check; real-library repair is not claimed here.


## Full affected-surface review (2026-10-10)

**Result: ENGINEERING REVIEW PASS / SOL_NOT_REQUIRED.** Per the user's instruction, this review was completed in the current session without DS-H. No source or test files changed during review, and no tests were rerun. The saved evidence and current source were checked directly.

### Finding dispositions

- **CLI cold-start cycle — PASS.** `catch_up()` now imports `is_busy_rejection` only inside the `CatalogError` handler. The normal indexing maintenance import path no longer imports `maintenance` through `search.documents` at module initialization. Busy classification, bounded wait/defer behavior, and propagation of non-busy errors are unchanged. Focused JUnit records the three cold CLI invocations passing.
- **Rename projection and recovery — PASS within the synthetic pending-outbox branch.** The integration case uses the shared `MEMBER_PREDICATE`, distinct asset counts for `CONFIRMED` / `PENDING_REPAIR` / `STALE` / `NO_ENTRY`, checks the newest outbox revision/state/error, idempotent rename replay, and invokes the production `drain_semantic_outbox()`. It confirms one event, no new artifact/encoding, a scoped hit after drain, exclusion of the other person's asset, and that `NO_ENTRY` is not falsely confirmed. The synthetic scenario does not identify the branch present in the user's real catalog.
- **Album empty state and response boundary — PASS.** The note is selected only when authoritative album photos exist, vector search is engaged, `engine_blocks` is zero, and FTS candidates are zero. A positive engine block count keeps the ordinary no-match message. The existing search response fields and all-photo browsing path are untouched.
- **Person and image-index semantics — PASS.** The examined authority transaction still queues projection revisions through the existing refresher; image `index_entries` and face `face_index_entries` remain separate. Retrieval still derives confirmed image candidates from the bound generation/space and canonical person scope. No current-generation state transition, transaction order, response envelope, or identity/manual-state contract changed.
- **Reports, status, archive, and saved test evidence — PASS with acceptance gate open.** Focused JUnit is 3/0/0/0. The first broad JUnit is collection-only with 9 errors. The retry records 2253 tests, 70 failures, 43 errors, and 49 skips, and the implementation report also records the ONNX fatal observation. There is no baseline run proving these broad failures pre-existing, so none are classified that way. The archive is the correct pre-implementation snapshot; the active status and report state the unresolved gate and do not claim PASS or real-library recovery.

### Acceptance decision

V3.1 §3.4 prevents task PASS while the required broad B/D validation has not passed. This review therefore closes the engineering review only; implementation status remains **IMPLEMENTED / VALIDATION PENDING** until the broad-suite gate is resolved. The user asked not to repeat complex tests, so no broad or focused tests were rerun. No real library or service was accessed.
