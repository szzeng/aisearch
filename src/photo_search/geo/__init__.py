"""Versioned, non-model CPU GPS extraction (ADR-0005 §3).

The package owns the EXIF/GPS half of the frozen place protocol: raw field parsing, the
``VALID``/``ABSENT``/``INVALID``/``UNSUPPORTED_CRS`` decision and the exact shape of the
structured fact an execution publishes. It never chooses a storage path, never reads a
path the caller supplied and never invents a model or a vector space.
"""

from photo_search.geo.exif import GPS_IFD, GeoExtraction, analyze, read_gps_fields

__all__ = ["GPS_IFD", "GeoExtraction", "analyze", "read_gps_fields"]
