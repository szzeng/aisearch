# P4-PLACE-SEARCH-A implementation record

Date: 2026-10-02. Base: `f3b04af` (place albums). Status: **PASS — 本机个人地点＋内容搜索与小样本验证**. This is local synthetic-only work; no real photo library was accessed.

## Short design

Added the personal endpoint `POST /api/v1/search/place-aware`; the frozen `POST /api/v1/search` request and response contracts remain unchanged. The additive body retains `original_query`, accepts an explicit `content_query`, optional `place_kind` + `place_key`, and `place_cancelled`. The server recognizes only exact active local place names/aliases as a pure place, `地点的内容`, and `在地点拍的内容`; longest matching place spelling wins. Same-name entries return `needs_place_choice` with selectable place keys. Unknown forms retain the original text. Cancellation sends the remaining text with an explicit no-place state.

For semantic text, the endpoint resolves current members with `places.albums.album_member_ids()` and passes them to the existing `search_request`/`search_assets` path. The additive `SearchScope.album_asset_ids` condition joins source, availability, assigned-place, person, and capture filters and therefore is applied by the common SQL predicate before FTS ranking, vector block candidate selection, authoritative paging, pending-projection checks, and final verification. Session digests bind the selected member IDs and `(catalog filter revision, suggestion revision, active package revision, kind, key)`; a changed binding refuses continuation as stale.

Pure place searches take a separate uncapped SQLite `asset_id` page over the same shared album membership and all explicit filters. Their cursor binds the album revisions and filter fingerprint; this path does not invoke the query encoder or the semantic top-k cap. The ordinary result grid renders a cancellable `地点：…` chip and retains album links.

Known city/country candidates include their parent names in ambiguity choices. Province candidates are derived from the active package INDEX rather than extant photo albums, so an indexed province with no current photos remains recognizable and returns an empty scoped result. Search-session continuation binds member ids and place filter/suggestion/package revisions; the pure-place page cursor binds those revisions and every explicit filter.

## Automated verification

Command (exit 0):

```powershell
$env:UV_CACHE_DIR = Join-Path $PWD '.uv-cache'
uv run --no-sync pytest -q tests/integration/test_p4_place_search_flow.py tests/integration/test_p4_place_auto_flow.py tests/integration/test_p2_search_api.py tests/integration/test_p2_search_authority.py tests/unit/test_p2_search_sessions.py tests/integration/test_p4_01c_retrieval_blocks.py --junitxml=test-results/p4-place-search.xml --basetemp=.pytest-tmp/p4-place-search
```

JUnit: **28 tests, 0 failures, 0 errors, 0 skipped**, `test-results/p4-place-search.xml`. The new synthetic tests verify pure `济南` paging/cancellation, FTS restriction, and that the vector channel's confirmed-candidate SQL walks only the selected member before the controlled fake index ranks it. Existing place album, search authority, sessions, and vector block tests pass.

Static checks (exit 0): `uv run --no-sync ruff check src tests tools`; `git diff --check`. Python syntax checks passed for the changed Python modules. An initial sandboxed pytest attempt hit the repository-documented `.pytest-tmp` WinError 5; rerun by the normal permission route passed. No ACL was modified.

## Browser evidence and limits

Used the project-owned `.tmp/p4-place-search-browser` fixture with synthetic JPEGs, one manually assigned synthetic city photo, a deterministic fake query encoder/index and local server `127.0.0.1:18765`; no downloaded model or real library. In the browser, `济南的日落` displayed the `地点：济南` chip and one ordinary grid result (asset #25, FTS filename hit; semantic channel correctly disclosed as pending in the fixture). Opening “查看详情” displayed the synthetic thumbnail preview. Clicking “取消地点条件” retained `日落`, removed the chip, and returned two matching results (one controlled vector hit and one FTS hit).

Then asset #25 was moved from 济南 to 上海 using the production assignment service against the synthetic fixture. The Places UI showed 上海 1 / 济南 1; after a browser page reload, returning to Search and submitting `济南的日落` showed the 济南 chip and zero rows. This confirms the edited asset did not re-enter the old range. The edit action was made through the production service fixture, not through the browser's location form.

Evidence is a personal small-sample contract check, not a quality claim for a real semantic model. The focused suite does not separately exercise every combination of GPS-only membership, multi-person/time/source intersections, same-name selection interactions, and package-revision cursor invalidation; these remain bounded follow-up verification opportunities, not a basis for claiming real-library accuracy or scale.

No B/C follow-up card, benchmark, full pytest run, model download, or real-library scan was performed. No commit was created in this turn.
