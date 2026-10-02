# P4-HOME-UX-A implementation and validation

Date: 2026-10-02
Status: **IMPLEMENTED / VALIDATION PENDING**

## Changes

- Added authenticated `GET /api/v1/sources/summary`, returning source identity, display name, volume hint, recorded status, and last successful scan. It uses `list_sources()` only; it does not call per-source counts/progress or probe source roots. Existing full source endpoints keep their behavior.
- Reworked the home page around immediately available gallery entry buttons and search. Source details, counts, progress, and management actions load only when an individual source detail is opened. Duplicate display names show source IDs; cards use only existing `volume_hint` and explicitly say the status is a record value, not a live disk check.
- Added per-source browse selection, an explicit all-galleries action and cancel filter, source labels and multi-location hints on tiles, stale browse response protection, and return-to-gallery state with a clicked-tile visual anchor.
- Gave the detail preview its own `contain`-sized frame, reset the detail view to its top, and adjusted the mobile preview height for the space left below the navigation and return controls. Long fingerprints now wrap inside the details grid. The home and search pages share the updated stylesheet; the home assets use a versioned URL so the changed CSS/JS is fetched after an update.

## Small synthetic browser run

Used only `tmp/p4-home-ux-a`: three registered source roots, 88 synthetic JPEGs plus one corrupt file, including two identical display names, horizontal and vertical images, and repeated-content locations. The catalog deduplicated identical bytes into 65 assets. No model was loaded; no `PhotoSearchData` or real photo library was accessed.

| Measurement | Before (3 samples) | After (3 samples) | Notes |
|---|---:|---:|---|
| `GET /api/v1/sources` | 21.50, 20.50, 15.97 ms | — | Same authenticated client and 3-source synthetic catalog; full counts/progress response. |
| `GET /api/v1/sources/summary` | — | 15.01, 15.12, 15.51 ms | Same process/catalog. Small fixture is too small to prove large-library acceleration. |
| Home gallery entry visible after reload | 170, 155, 156 ms | 73, 71, 62 ms | Old UI measured until its first full source card was visible; new UI measured until the direct gallery CTA was visible. Samples are warm; first-use/cold time is not represented. |
| Detail preview visible after gallery click | 163, 138, 140 ms | 163, 180, 164 ms | Three warm samples each, same 3-source catalog/cache. Old preview box stayed at 768×192; new portrait preview was 271×480 on desktop. |

The code-path change addresses the identified blocking work directly: home rendering now requests only the summary endpoint, and full per-source work is deferred until that source's disclosure is opened. For the UI baseline, the three old frontend assets from `fe46753` ran against the current compatible backend and an isolated copy of the same small synthetic catalog/cache. The before/after samples show this local small-fixture behavior; they do not prove real or 300k library performance.

## Browser observations

- Home rendered three gallery cards before opening any source detail. The two `重复图库` cards were distinguishable as `#1` and `#2`; both showed the existing `D:` volume hint. Opening detail for one source did not hide the other cards.
- “查看此图库照片” filtered to that source; “查看全部图库照片” cleared it. The all-gallery list first loaded 50 cards and reached 65 after loading the next page. A tile with repeated content showed the additional-location count.
- After opening a tile partway down the page, detail began at scroll position 0 with its preview near the top. Returning retained the source filter and the clicked tile's viewport position; after lazy-image reflow the measured anchor moved 17 px.
- At a 390×844 viewport, the portrait preview displayed at 286×508 with its bottom at y=820; the landscape preview displayed at 351×198. In both cases document scroll width equaled the 375 px content width. The long content fingerprint wrapped without horizontal overflow.
- Simulated one missing original only inside the synthetic source, then restored it. The detail correctly said `SOURCE_FILE_MISSING`, showed the cached thumbnail, and labeled the quality downgrade and unavailable original.
- Ordinary search could not produce a result for opening its detail: the synthetic data root has no activated search profile, and the request was rejected with `VALIDATION_ERROR` before any result was returned. The task forbids loading models, so the search-result detail path remains unverified in a live browser.

## Validation

- Required focused command: `uv run --no-sync pytest -q tests/unit/test_api_frontend.py tests/integration/test_api_sources_assets.py tests/integration/test_api_access.py --junitxml=test-results/p4-home-ux-a-focused.xml --basetemp=.pytest-tmp/p4-home-ux-a-focused`
- Final result: **113 tests, 0 failures, 0 errors, 0 skipped**, exit code 0. The first sandbox run hit the documented `.pytest-tmp` `WinError 5`; the same command passed via the normal permission route. No ACLs were changed.
- `uv run --no-sync ruff check src tests tools`: passed, exit code 0.
- `node --check src/photo_search/web/app.js`: passed, exit code 0.
- `git diff --check`: passed, exit code 0.

## Remaining validation

- Per the task card, status stays **IMPLEMENTED / VALIDATION PENDING** until an ordinary search result can be opened into the shared detail view. The synthetic run has no active query profile/provider; do not activate or load a model for this task.
- No 300k measurement, full suite, model run, packaging run, or independent review was performed, as the updated task card excludes them.

## Follow-up requested on 2026-10-03

- Startup now requests `/sources/summary` before `/capabilities`. The latter performs several synchronous catalog summary reads from an async route; starting it first can occupy the event loop before the lightweight source request is served. The summary remains rootless and does not call source counts/progress. A live 300k timing was not run.
- Each source card's existing explicit “完整路径、状态与管理明细（按需读取）” disclosure now shows the full `SourceDetail.root_path`. The path is fetched only after that disclosure opens; the summary response remains rootless, and server error path redaction plus frontend `PATH_LIKE` handling remain in place.
- The shared detail view now has zoom out/in, “适合窗口”, and “实际大小” controls. Fit recomputes on window resize/orientation resize; actual size uses the image's natural pixel dimensions. In the synthetic browser run, a 96×72 image displayed at 644% to fit the view, changed to 100% for actual size, and returned to 644% for fit.
- Search review found a reproducible blank-query regression: the search form promises pure-filter search, but `/search/place-aware` forwarded `text=""` and received `422 VALIDATION_ERROR`. The route now normalizes an empty unrecognized query to `None`. Browser verification then returned all 6 synthetic assets (`FILTER_ONLY`), and a focused regression test covers it. A separate endpoint test with a deterministic synthetic encoder returned ordinary text-search hits.
- The current local app was not running on its default port during this check. The synthetic browser server intentionally had no model/query encoder; its page correctly reported that non-empty semantic text queries are unavailable instead of showing an empty result. The real configured profile's non-empty text search therefore remains unverified, and status stays **IMPLEMENTED / VALIDATION PENDING**.
- Follow-up JUnit command included the three required task-card files, `tests/integration/test_p4_place_search_flow.py`, and `tests/integration/test_p2_search_endpoint.py::test_an_explicitly_bound_text_search_answers_one_reproducible_page`: **116 tests / 0 failed / 0 errors / 0 skipped**, exit code 0. XML: `test-results/p4-home-ux-a-focused.xml`. Ruff, `node --check` for `app.js` and `search.js`, and `git diff --check` passed. The first sandbox pytest attempt hit the documented temp-directory `WinError 5`; the same command passed through the normal permission route, without ACL changes.
- All browser work used only the new six-image synthetic fixture under `tmp/p4-home-ux-a-search-ui`; its synthetic profile was isolated there. No real library, `PhotoSearchData`, model, or existing 300k fixture was accessed or changed.

## Follow-up diagnosis on 2026-10-03

- A read-only check found a local service responding on `127.0.0.1:8000`; `GET /api/v1/health` returned `200 {"status":"ok","service":"photo-search"}`.
- Opening the plain root URL in a fresh in-app browser reached the page, but its source-summary request returned `401 SESSION_REQUIRED` (request ID `e333dce05d9517af`). The page itself instructed using the one-time link printed by the launcher. This fresh browser had no authenticated session; it did not submit a search request.
- Therefore this observation does **not** establish a zero-hit retrieval or a query-encoder failure. Actual search behavior under the user's authenticated session remains unverified. No bootstrap token/link was read or replayed; no real catalog, library, profile, or model was accessed. To continue the live check, open the launcher-printed one-time link in the browser where search will be tried, then report the search result/error code without sharing the link or token.
