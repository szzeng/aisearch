# P4-PLACE-UNIFIED-A — implementation and evidence

Date: 2026-10-09. Status: IMPLEMENTED / SCOPED VALIDATION COMPLETE / LOCAL BROWSER VALIDATION PENDING / INDEPENDENT REVIEW PENDING.

## Identity and manual-assignment decision

`places.kind` and `asset_place_assignments.place_id` remain unchanged. OSM province/city/district identity is authoritative only when the current validated package contains the corresponding `cn_admin_ids` / `cn_dataset_regions` row. A new additive `cn_admin_place_refs` table maps a stable OSM admin identity to a local `Place` FK carrier when a user first assigns it. The carrier uses the legacy `custom` kind solely to satisfy the frozen assignment FK; it is hidden from the general free-form Place directory and cannot be renamed. Its kind is never used to infer administrative level. OSM region rows determine its actual type and complete parent path. Existing `cn_legacy_links` remain the only equivalence proof for folding a GeoNames city into an OSM city; name similarity alone never merges candidates.

The unified choice API returns a controlled source key containing the active package identity and source ID. Assignment writes resolve and validate that key inside the existing single/batch operation transaction. Legacy `target_place_id` writes remain structurally accepted for compatibility but now succeed only when that ID is proven by the active source directory (including a verified GeoNames external reference or OSM bridge). USER/custom historical Places, assignments, exclusions, operation records, and undo snapshots remain readable and untouched; they cannot receive new manual assignments without source proof. No second assignment table or state machine is introduced. An assignment to an OSM district reuses the existing lock, exclusion, revision, idempotency, batch, and compensation-undo behavior.

The choice layer reads only active validated local sources. OSM facets win over GeoNames only through `cn_legacy_links`; otherwise same-name choices remain separate. OSM manual membership uses the same `cn_facets.members_sql` path as GPS facets and projects to the selected region and trusted parents. Assignment locks prevent a conflicting GPS facet from being added. No photos, EXIF, models, package downloads, or real PhotoSearchData are in scope.

## Baseline and workspace notes

Task-card baseline `5d7bc139` is stale; actual HEAD at start was `fdd10457e9cb5a60e07e6181846d01424855a7e3`. The worktree contained uncommitted P4-SEARCH-PLACE-NL-A changes in routes, search UI, and integration fixtures; those were committed during this task as `a5aaab1`, and implementation preserves that commit and layers this task on top. Additional unrelated documentation-only commits arrived while this task was in progress: `85d4653`, `3712b82`, and latest `d822d56`; they were left untouched. P4-PLACE-CN is implemented but its independent review remains pending. Its review is a recommended prerequisite, not a hard stop in the explicitly requested implementation card.

## Changed paths

**Identity/schema:** `src/photo_search/catalog/migrations/0021_cn_manual_place_refs.sql` adds `cn_admin_place_refs` and package-scoped `geo_dataset_place_refs`; `catalog/schema.py` advances support to schema 21; `geo/dataset.py` adds an explicit maximum-schema compatibility bridge for validated package schemas 16–20; `places/datasets.py` records exact package-to-Place source proofs at activation; `docs/decisions/ADR-0016-cn-admin-boundaries.md` documents the bridge.

**Shared choices and writes:** new `places/choices.py`; `api/models.py`, `api/routes.py`, `places/service.py`, and `places/assignments.py` expose source-backed selection references and validate both single and batch writes. Legacy `target_place_id` stays readable but new manual writes require source proof.

**Membership and presentation:** `places/cn_facets.py` projects OSM manual membership through the region and trusted parents while retaining old `cn_legacy_links`; `places/filter.py` reports real source/type for a mapped assignment; `places/directory.py` hides and protects internal OSM FK carriers; `places/natural_search.py` recognizes explicit unavailable place expressions without inventing a filter. Updated `web/places-albums.js`, `web/places.html`, `web/places.js`, and `web/search.js`.

**Tests/compatibility:** new `tests/integration/test_p4_place_unified.py` and `tests/unit/test_p4_place_unified_ui.py`; updated synthetic source fixtures and place-search integration tests; corrected the full migration table-set assertion in `tests/unit/test_catalog_migrations.py` to include the existing 0017–0020 tables and new 0021 tables.

## Validation and browser closure

Scoped final command (PowerShell):

```powershell
$env:PYTHONPATH = (Get-Location).Path + ';' + (Join-Path (Get-Location) 'tests')
$env:UV_CACHE_DIR = (Join-Path (Get-Location) '.uv-cache')
uv run pytest -q tests/integration/test_p4_place_unified.py tests/integration/test_p4_place_cn.py tests/integration/test_p4_place_search_flow.py tests/integration/test_p4_place_auto_flow.py tests/unit/test_catalog_migrations.py tests/unit/test_p2_dataset_manifest.py tests/unit/test_search_place_nl_ui.py tests/unit/test_p4_place_cn_ui.py tests/unit/test_p4_place_unified_ui.py tests/unit/test_p2_place_contracts.py tests/unit/test_p2_contracts.py --junitxml=.tmp/p4-place-unified-final.junit.xml --basetemp=.pytest-tmp/p4-place-unified-final
```

Result: exit 0; JUnit **116 tests / 0 failures / 0 errors / 0 skipped**. Coverage includes active-package source choices, OSM-over-GeoNames only through verified links, GeoNames fallback, duplicate parent paths, nonexistent and ordinary phrases, district membership through city/province, single and batch controlled-ref writes, assignment switch/undo, forged legacy ID rejection, cursor/version behavior, catalog migration and API contract regressions.

`uv run ruff check` on all changed Python modules/tests: exit 0. `node --check` on `places-albums.js`, `places.js`, `search.js`: exit 0. `python -m compileall -q` on changed Python files: exit 0. `git diff --check`: exit 0. The actual manual-correction JS interaction was exercised with a small DOM/HTTP substitute: full parent path and `district · OSM` are shown; selection submits only `target_place_ref`. The search page synthetic interaction separately verifies same-name selection and cancelling the place scope.

**A real local browser round-trip remains unverified.** The synthetic app and package fixture initialized, but Uvicorn could not bind `127.0.0.1:8765` (`WinError 10013`, Windows denied the loopback bind). Browser inventory had no open page to reuse. No permission/ACL changes or user service restart were attempted. This is an environment limitation, not a product test result; the required real-browser closure must be completed when a local browser service can be started normally.

## Not verified

No real library/catalog, real package installation/switch, model, EXIF, or full geographic coverage validation is authorized or planned. No live page flow through a real browser was completed. Independent DS-H review of the complete affected surface remains required before PASS; focused Sol high review is conditional on high-risk unresolved findings. This is not a nationwide coverage or full-suite result.
