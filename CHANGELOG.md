# Changelog

## 0.2.0 — 2026-10-13 — Personal preview

- Local photo catalog with text and semantic search, uploaded-image search, and explicit model-profile and catalog-schema status.
- People, places, OCR, and collection browsing and management.
- Storage diagnostics, manual catalog backups, and bounded maintenance actions. Catalog backups do not include original photos or model weights.
- Video files are excluded from new photo scans and photo listings. Existing video-only records become missing after a successful refresh.
- People with no effective photo assets are hidden from person listings.
- Escape exits person batch management without discarding already saved operations.
- Pending-face suggestion cards now show the suggested person's reference avatar. This has synthetic UI test coverage; visual behavior on the user's library remains unverified.

This is a personal preview, not a formal Windows release. UI 2.0 and the unified photo timeline remain plans and are not included as delivered features. Real-library interaction checks and formal release gates remain separate.
