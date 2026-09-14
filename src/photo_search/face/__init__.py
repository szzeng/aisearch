"""P3-02 face detection, alignment, canonical vectors and conservative grouping.

The package is deliberately split the way P3-01 split OCR:

``contract``   frozen identity, pinned-weight table and the calibrated thresholds. It
               imports nothing heavy, so an API or CLI process can describe the face
               layer without paying for ONNX Runtime.
``weights``    resolution and SHA-256 verification of the pinned ``buffalo_l`` files.
``engine``     the pinned SCRFD detector, the ArcFace alignment and the recognition graph.
``processor``  the ADR-0003 shell, the structured face document and its persistence hook.
``index``      the independent face candidate index (own generations and outbox).
``service``    enqueue/run/reconcile/status orchestration, with no resident thread.
``cli``        ``python -m photo_search.face.cli``.
"""

from __future__ import annotations

__all__ = ["contract"]
