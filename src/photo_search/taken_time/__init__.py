"""The P4-00B capture-time layer (ADR-0011).

``contract``   the frozen vocabulary, the civil sort key, the capture range grammar and the
               honesty block's pure state function;
``exif``       reading the pinned EXIF time fields (header only, never a pixel decode);
``extract``    the ``taken_time_extract`` scheduler processor and its persistence hook;
``read``       the read-only projections (per-asset detail and the honesty block counts);
``service``    selection, idempotent enqueueing and bounded queue draining;
``cli``        ``python -m photo_search.taken_time.cli`` for diagnostics and acceptance.
"""
