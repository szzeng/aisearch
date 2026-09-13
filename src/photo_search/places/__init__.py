"""Local place directory, GPS facts and the authoritative manual place control.

The frozen protocol is ADR-0005 and its companion ``p2-place-contracts.v1.json``. This
package implements it on top of the 0004 migration: it never redefines a status, an error
code or a transaction rule. ``geo`` owns the EXIF/GPS half of the same protocol.
"""

from photo_search.places.contracts import CONTRACT_VERSION

__all__ = ["CONTRACT_VERSION"]
