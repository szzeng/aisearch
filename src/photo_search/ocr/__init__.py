"""P3-01 OCR processor: pinned PP-OCRv4 weights, bounded slicing, and the OCR document.

Importing this package is cheap on purpose (see :mod:`photo_search.ocr.contract`). The
runtime pieces live in :mod:`photo_search.ocr.weights`, :mod:`photo_search.ocr.engine`,
:mod:`photo_search.ocr.processor`, :mod:`photo_search.ocr.service` and
:mod:`photo_search.ocr.cli`, and importing those is what loads Pillow/numpy/OpenCV/ONNX
Runtime.
"""

from __future__ import annotations

__all__: tuple[str, ...] = ()
