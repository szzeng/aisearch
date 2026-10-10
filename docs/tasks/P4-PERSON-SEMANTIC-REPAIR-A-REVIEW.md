# P4-PERSON-SEMANTIC-REPAIR-A-REVIEW — independent engineering review

**Status: COMPLETE / full affected-surface review passed; broad validation gate unresolved.**

## Assignment

- Review method: current-session full affected-surface engineering review at the user's direction; DS-H was not used. The implementation was produced in a GPT-6 High session.
- Review method: full affected-surface engineering review under V3.1 §2.4, because the implementation is adjacent to the durable semantic outbox and person-index recovery path.
- Input order: `AGENTS.md` → `docs/development-status.md` → this card → [implementation card](P4-PERSON-SEMANTIC-REPAIR-A.md) → [implementation report](../reports/P4-PERSON-SEMANTIC-REPAIR-A.md) → actual Git diff and the files/tests named below.
- Read only relevant architecture sections: V3.0 §§7.1–7.4, 8.3.1–8.3.5, 9.2–9.3, and 10.2; V3.1 §§2.2–2.4, 3.2–3.4, 9, and 11. Read the directly relevant outbox/person ADR clauses identified by those files. Do not read either architecture document in full.

## Review scope

Review every changed line in:

- `src/photo_search/search/documents.py`: local import placement; confirm busy classification, retry budget, and error propagation remain unchanged and the cold CLI cycle is actually broken.
- `src/photo_search/web/persons-albums.js`: exact existing-response-field conditions for the no-index message; verify `engine_blocks>0` retains the true no-match message, `POST /api/v1/search` is unchanged, and all-photo browsing remains available.
- `tests/integration/test_p4_person_semantic_repair.py`: validate subprocess isolation, the four-way `DISTINCT asset_id` classification, latest outbox revision/state/error assertions, idempotent rename, nonzero production drain, no re-encoding, scoped hits, and the `NO_ENTRY` non-confirmation assertion.
- `docs/reports/P4-PERSON-SEMANTIC-REPAIR-A.md`, `docs/development-status.md`, and `docs/development-status-archive-2026-10-10.md`: verify claims match the saved JUnit/report and accurately preserve the unresolved broad-suite gate and real-library boundary.

Inspect the direct call chain `indexing.cli → indexing.maintenance → scheduler.projection → persons/API imports → search.documents`; `persons.operations.publish_derived → places.revision.projection_refresher → IndexCoordinator.schedule_projection_refresh`; the existing production `drain_semantic_outbox()`; current-generation person filtering in retrieval; and person-album rendering. Verify that no current-generation state, transaction ordering, response envelope, or image-versus-face index semantics changed.

## Evidence to verify

- Focused JUnit `tmp/test-reports/P4-PERSON-SEMANTIC-REPAIR-A-focused.xml`: 3/0/0/0.
- Broad JUnit `tmp/test-reports/P4-PERSON-SEMANTIC-REPAIR-A-D-with-import-path.xml`: 2253/70/43/49, with the separate collection-failure record and the `0xc0000139` ONNX fatal observation in the implementation report.
- First broad attempt `tmp/test-reports/P4-PERSON-SEMANTIC-REPAIR-A-D.xml`: collection errors, including the unrelated syntax-invalid `test_p4_02y_l_identity.py` and repository import-path setup. Verify that the report does not call either broad run a pass or assert all errors are pre-existing.
- Ruff, Node check, py_compile, and diff-check results in the report.

Do not open a real library or service. Do not change code in this review stage. If a review finding requires a code fix, return the minimal counterexample and changed boundary for a new implementation turn. Do not rerun the entire broad suite; inspect only evidence and run the directly affected cases if the diff/evidence has changed.

## Required decision and stopping point

Give a finding-by-finding disposition and an overall engineering-review result. Explicitly decide whether failures in the required broad B/D gate block completion under V3.1 §3.2–3.4; the current report conservatively leaves the task non-PASS. Do not label the task PASS while a required validation gate remains unresolved.

Assess Sol separately: if the diff changes a high-risk state invariant or leaves a high-risk ADR/data-safety question, report `ENGINEERING REVIEW PASS / SOL FOCUSED REVIEW PENDING`, name the exact invariant and minimum counterexample, and stop. If the review confirms that the outbox state machine and formal contracts are unchanged and no high-risk question remains, record `SOL_NOT_REQUIRED` with the reason. Do not escalate to DS-MAX absent a documented V3.1 §2.2 condition and unanswered question.

After this review, update the report/status with the actual review and validation decision. If the required B/D gate still blocks acceptance, keep `IMPLEMENTED / VALIDATION PENDING`, name the exact missing closure, and do not create a later implementation card merely to continue the sequence. If the gate is resolved and review closes, transfer to one-person real-library recheck only when the user chooses to perform it; never access that library on the reviewer’s own initiative.
