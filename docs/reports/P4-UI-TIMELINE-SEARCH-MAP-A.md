# P4-UI-TIMELINE-SEARCH-MAP-A — execution report

Date: 2026-10-14
Status: **IMPLEMENTED / ACCEPTANCE PARTIAL / LUNA HIGH REVIEWS COMPLETE**

## Scope and result

Implemented the stage-one fixed-scope search handoff for person albums, collections, and place albums. Each CTA transfers identity-only scope through a one-time `sessionStorage` handoff and opens the shared `/search` input. The API revalidates active person identity, collection instance/revision, or the current place choice; a fixed scope remains conjunctive with additional conditions. Pure place filtering uses the authoritative SQL membership predicate and keyset paging rather than materializing all place member IDs. FTS/vector retrieval receives the same predicate before ranking, and place/collection/time revisions are bound to the search session.

Added deterministic library-time bounds on `assets.created_at`, capture-date `unknown`/`pending` predicates, parser plan fields, and removable chips. Pure time/scope filters stay on the SQL filter path and do not request an encoder. The map aggregation/bbox/member-version protocol is unchanged.

Stage-one API contracts are covered for person, collection, and place scope, including pagination, empty results, revision/stale-cursor refusal, ambiguous choices, library-time and capture-status choices. Actual browser CTA → search → preview → back interaction remains unverified, so no scope receives full UI acceptance yet. Stage two is now implemented: semantic time browsing pages only the frozen, bounded (≤5000) candidate session and preserves semantic rank provenance; map time browsing pages authoritative members of the complete current viewport with bbox/member-version and catalog/time-rule cursor bindings. These are implementation and focused API/UI-test results, not browser or scale acceptance.

## Validation

- Focused JUnit command:
  `uv run --no-sync pytest -q tests/unit/test_p4_search_intent_unified.py tests/integration/test_p4_search_nl_ux.py tests/integration/test_p4_place_search_flow.py tests/integration/test_p4_search_collection.py --basetemp=.pytest-tmp/P4-UI-TIMELINE-SEARCH-MAP-A --junitxml=test-results/P4-UI-TIMELINE-SEARCH-MAP-A.xml`
- Result: **72 tests, 0 failures, 0 errors, 0 skipped**.
- Ruff on changed Python source and focused tests: passed.
- `python -m compileall -q` on changed Python modules: passed.
- `node --check` on `search.js`, `persons-albums.js`, and `places-albums.js`: passed.
- `git diff --check` and `git diff --cached --check`: passed.
- User-requested independent affected-surface review used **LUNA High**, not DS-H. The reviewer found two time-choice plan defects; both were fixed, regression tests were added, and the final review found no remaining actionable findings.

## Pending acceptance gates

- `BROWSER_INTERACTION=PENDING`: Computer Use stopped because it could not determine the current Chrome URL with enough confidence to enforce policy. No browser interaction or screenshot evidence is claimed.
- `PERF_200K=PENDING`: no valid reusable 200,000-item fixture is available. CORE’s existing 300,000 catalog has no `asset_metadata`, people, or linked members; the CORE report records that its 200,000 fixture was removed and was not rebuilt. Small synthetic API tests are functional evidence only.
- `CHECK_300K=PENDING for this task`: the inherited 300,000 catalog cannot represent trusted capture dates or populated person/collection/place member scopes, so it cannot verify this task’s changed high-member query paths. No new scale fixture was created.
- `PERSON_SCOPE`, `COLLECTION_SCOPE`, `PLACE_SCOPE`: API behavior verified; end-to-end browser acceptance remains pending for all three.
- Stage two semantic candidate time browsing and map viewport time browsing: **IMPLEMENTED / FOCUSED TESTS PASS / BROWSER ACCEPTANCE PENDING**. The semantic view is bounded to the frozen candidate snapshot; map time order applies only to the complete viewport. Neither changes semantic ranking, TopK, or map aggregation. These features are no longer marked DEFERRED.
- Predecessor acceptance states A/C/CORE/B/D/E are unchanged. E remains acceptance-partial as recorded in its own report.

No commit was created. No real photo library, model, or GPU was accessed.

\n
## Stage-two implementation follow-up (2026-10-14)

- Semantic candidate endpoint: `GET /api/v1/search/sessions/{session_id}/time`; sorts trusted capture dates inside the existing frozen session, pages with query/session/catalog/time-rule cursor bindings, reports original semantic rank, truncation, and candidate scope. The search UI exposes this only for semantic sessions, labels the bounded candidate scope, and can return to relevance order.
- Map endpoint: `/api/v1/map/assets?order=capture_time_desc`; pages all authoritative members in the current viewport, keeps the existing map aggregation and default asset-ID cursor contract, and binds time cursors to bbox, map member version, catalog filter revision, time-rule version, and page size. Cell-specific lists stay on the existing order. The map UI can toggle viewport ordering and retains the selection when returning from detail.
- Focused JUnit: `test-results/P4-UI-TIMELINE-SEARCH-MAP-final-verified.xml`, **82 passed / 0 failed / 0 errors / 0 skipped**. Includes regression coverage for candidate qualification when availability changes without a filter-revision bump, binding mismatch, and an out-of-order time response. This run covered both new time APIs, map membership behavior, search UI paging/snapshot behavior, and directly related intent/search regressions.
- Ruff on changed Python modules/tests, Python compilation, `node --check` for `search.js` and `map.js`, and `git diff --check` passed.
- Stage-two independent affected-surface review used **LUNA High**, not DS-H. Three findings were repaired: profile/generation binding validation; current authoritative SearchScope eligibility recheck; and a stale semantic-time response race across a new search. Follow-up review confirmed all three closed with no blocking findings. The reviewer noted that the binding regression mutates the frozen session to induce mismatch; a live active-profile switch test would add coverage but is not needed to establish the implemented comparison logic.
- Browser acceptance, `PERF_200K`, and representative `CHECK_300K` remain pending. Therefore both stage-two features are implemented, but their end-to-end and scale acceptance is not claimed; overall task remains acceptance-partial.
