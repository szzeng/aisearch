# Photo Search

P0-01 establishes only the runnable Python application skeleton. See
[`docs/development-status.md`](docs/development-status.md) and the P0-01 report for
the verified environment and commands.

## Semantic providers (P4-Embedding-Arch)

Text search and uploaded-image search are served by exactly **one active model**. Two providers
ship today; each has its own representation space, index generation and search profile, and a
512-dimensional vector from one of them is never mixed with the other:

| provider id | model | adapter | device / precision |
|---|---|---|---|
| `chinese-clip-vit-b-16` | Chinese-CLIP ViT-B-16 (512D) | `chinese_clip_onnx` | CUDA by default, an explicit CPU request is a reported degradation |
| `qwen3-vl-embedding-2b` | Qwen3-VL-Embedding-2B (2048D, 512D output) | `qwen3_vl` | CUDA required, FP16, batch 1, no CPU/offload path |

Installed models live under `<data_root>/models/...` and their manifests under
`<data_root>/models/manifests/`. Nothing is downloaded at run time: a missing file, a changed
checksum or an unknown provider is a structured refusal (`EMBEDDING_WEIGHTS_MISSING`,
`EMBEDDING_WEIGHTS_HASH_MISMATCH`, `EMBEDDING_PROVIDER_UNKNOWN`).

```powershell
uv run photo-search embedding list --data-root D:\path\to\PhotoSearchData

uv run photo-search embedding prepare-local --provider qwen3-vl-embedding-2b `
  --source tmp/qwen-speed-model --data-root D:\path\to\PhotoSearchData

uv run photo-search embedding build-candidate --provider qwen3-vl-embedding-2b `
  --data-root D:\path\to\PhotoSearchData

# Publish the candidate: stop the service and every other writer of this data root first.
uv run photo-search embedding select-profile --profile semantic-image-gen-XXXXXXXX `
  --data-root D:\path\to\PhotoSearchData --stopped
```

The rules those commands enforce:

* `build-candidate` never moves the served pointer. A profile for another space is created as an
  immutable candidate; the first build of a new library, and every daily/incremental build of the
  space that is already active, keep their previous behaviour.
* `select-profile` acquires the data-root maintenance lease, refuses `--stopped`-less runs, and
  publishes the pointer only inside one `BEGIN IMMEDIATE` transaction that re-verifies the whole
  bundle: current-library coverage by the current content revision, no unconfirmed outbox or
  repair entry, readable artifact content, a healthy Lance table, and recorded runtime
  qualification for the device the model really ran on. A failed check leaves the old profile
  active. An offline file or a partial build is a refusal, never a silent partial switch.
* The lease only excludes *managed* processes. The operator's `--stopped` declaration covers the
  HTTP service, the first-party maintenance writers and anything else holding the data root.

Evidence for this slice: [`docs/reports/P4-EMBEDDING-ARCH-implementation.md`](docs/reports/P4-EMBEDDING-ARCH-implementation.md).
