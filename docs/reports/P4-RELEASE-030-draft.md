# P4-RELEASE-030 Draft Preparation Report

Date: 2026-10-16
Status: DRAFT ONLY — not committed, tagged, pushed, or published.

## Candidate

- Application/package version: `0.3.0`
- Proposed Git tag: `UI2.0` (separate from the application version)
- Proposed GitHub pre-release title: `PhotoSearch 0.30 — UI 2.0 Personal Preview`
- Candidate base: `b85a17d76b7f11fb4a2aa6b844c04ae9bef2aa4b`
- Catalog schema: 32

## Prepared changes

- Bumped `pyproject.toml`, runtime API version, OpenAPI version expectation, and the local project entry in `uv.lock` to `0.3.0`.
- Added a draft `0.3.0` changelog entry describing the UI 2.0 personal preview and its limitations.
- No third-party dependency versions changed.

## Verification

- `uv lock --check`: PASS.
- Ruff on the changed Python files: PASS.
- `git diff --check`: PASS.
- Focused version, reproducible synthetic search, and catalog migration checks: 17 passed; JUnit: `docs/reports/artifacts/P4-RELEASE-030-core.xml`.
- Broader targeted page/API selection: 17 passed, 1 failed; JUnit: `docs/reports/artifacts/P4-RELEASE-030-draft.xml`. `/search` returned HTTP 200, but the existing test then failed because the UI2 shell no longer contains the asserted literal `href="/search"`. This navigation-shell assertion needs review; it is not counted as PASS.

## Release hold

The latest development status, P4-PERSON-FAST-ORGANIZE-B task card, and its report still mark the final face-collection browser write/read and related interaction evidence as `BROWSER_PENDING` / `ACCEPTANCE PARTIAL`. The current `0.3.0` draft preserves that state and must not be described as accepted. Other UI browser, visual, and scale gates also remain pending. No real user catalog was migrated for this preparation.

Before publishing, resolve or explicitly scope the pending acceptance, reconcile the stale page-shell test with the current UI2 navigation, rerun the required checks, and review the final release notes and exact tag target.

## GitHub state at inspection

- Existing local tag: `v0.2.0`.
- No `UI2.0` tag or matching pre-release existed at inspection.
- No release actions were performed.
