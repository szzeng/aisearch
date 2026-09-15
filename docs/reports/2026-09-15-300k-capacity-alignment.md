# 300k Capacity Alignment

Date: 2026-09-15  
Baseline: `b5f79cfd93b77499795944c64a1fd21f6918f6f2`

## Decision

The first release formally supports a local library of up to **300,000 image assets**.

All capacity, performance, recovery, and release PASS claims use 300k as the supported validation target.
A synthetic **350k–400k** run may be used as an optional non-commitment stress observation to identify
performance cliffs. Exceeding 300k does not require the application to hard-fail at asset 300001; the product
should warn that the library is outside the formally validated range.

## Architecture impact

No core architecture change is required.

The following remain unchanged:

- SQLite + WAL + FTS5 for authoritative/catalog/search text data.
- LanceDB for embedded vector storage and retrieval.
- Persistent scheduler, outbox, replay, repair and recovery semantics.
- One bounded GPU worker with interaction priority and staged model loading.
- CUDA as the normal AI execution path and GTX 1660 6 GB as the minimum support target.
- Existing OCR, face/person, place, undo/revision/idempotency and model-upgrade contracts.
- Existing migrations and accepted P1/P2/P3 work.

No production code, Python dependency, schema, or migration is changed by this alignment.

## P4 acceptance impact

P4-01 becomes measurement-driven: compare exact/ANN behavior at 100k and 300k and tune ANN/index settings
only if the 300k benchmark misses the architecture performance targets.

P4-02 becomes:

`10k → 100k → 300k` formal capacity validation.

`350k–400k` is optional stress observation only.

Do not add distributed scheduling, Redis/Celery, multi-GPU execution, another vector backend, manual sharding,
or other million-scale complexity solely for hypothetical scale. Such changes require measured evidence at
300k and a separate architecture decision.

## Historical impact

No accepted P1/P2/P3 task is reopened. Existing migrations remain forward-only and unchanged.
The current P3-04 contract/implementation route is independent of this scale revision and continues normally.
