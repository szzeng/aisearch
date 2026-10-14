# P4-UI-TIMELINE-SEARCH-MAP-A — execution report

Date: 2026-10-14
Status: **IMPLEMENTED / ACCEPTANCE PARTIAL / LUNA HIGH REVIEW COMPLETE**

## Scope and result

Implemented the stage-one fixed-scope search handoff for person albums, collections, and place albums. Each CTA transfers identity-only scope through a one-time `sessionStorage` handoff and opens the shared `/search` input. The API revalidates active person identity, collection instance/revision, or the current place choice; a fixed scope remains conjunctive with additional conditions. Pure place filtering uses the authoritative SQL membership predicate and keyset paging rather than materializing all place member IDs. FTS/vector retrieval receives the same predicate before ranking, and place/collection/time revisions are bound to the search session.

Added deterministic library-time bounds on `assets.created_at`, capture-date `unknown`/`pending` predicates, parser plan fields, and removable chips. Pure time/scope filters stay on the SQL filter path and do not request an encoder. The map aggregation/bbox/member-version protocol is unchanged.

Stage-one API contracts are covered for person, collection, and place scope, including pagination, empty results, revision/stale-cursor refusal, ambiguous choices, library-time and capture-status choices. Actual browser CTA → search → preview → back interaction remains unverified, so no scope receives full UI acceptance yet. Stage two is **DEFERRED**: semantic candidate time browsing and viewport-wide map time browsing were not added.

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
- Stage two: semantic candidate time browsing and map viewport time browsing are **DEFERRED**; existing relevance ranking and map behavior remain in place.
- Predecessor acceptance states A/C/CORE/B/D/E are unchanged. E remains acceptance-partial as recorded in its own report.

No commit was created. No real photo library, model, or GPU was accessed.

\n