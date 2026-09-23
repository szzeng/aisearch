"""The one pillow-heif registration point for HEIC/HEIF (P4-00C-A, architecture §2).

Pillow has no HEIF decoder of its own, and the architecture fixes ``Pillow + pillow-heif``
as the single supported route. Everything about that dependency is funnelled through this
module so that three separate concerns cannot drift apart:

1. **One registration.** ``register_heif_opener()`` is called from exactly one place, is
   idempotent, and is never repeated by an importer.
2. **Fail closed.** A missing wheel, a missing ``_pillow_heif`` extension or a missing
   libheif runtime is a *capability fact* (``heif_available()`` returns ``False``) instead
   of an import-time crash: the media layer then refuses HEIC/HEIF with the structured
   ``FORMAT_UNSUPPORTED`` it already used, and the capability list does not advertise a
   format this build cannot decode.
3. **Orientation honesty.** ``pillow_heif`` reads the orientation out of the HEIF EXIF
   block, *rewrites that tag to 1* in the in-memory EXIF and reports the value it removed in
   ``info["original_orientation"]``. Pillow's own ``ImageOps.exif_transpose`` therefore sees
   "no rotation" on a HEIC whose only orientation evidence is that tag. The recorded value
   is read back through :func:`recorded_orientation` by the decoder, which restores the tag
   so the ordinary normalisation path still runs (P4-00C-A report §3).
"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from typing import Any

#: The Pillow format label the pillow-heif plugin reports for both containers (see
#: ``pillow_heif.as_plugin.HeifImageFile.format``), plus the ``.heic`` spelling the brand
#: sniff can produce. Both are the same decoder and the same policy decision.
HEIF_INPUT_FORMATS = frozenset({"HEIF", "HEIC"})

#: The key ``pillow_heif.set_orientation`` fills in ``Image.info`` with the orientation it
#: removed from the EXIF block (``None`` when the file carried no orientation tag).
ORIGINAL_ORIENTATION_KEY = "original_orientation"

#: The HEIF container properties libheif applies while decoding. When one of them is
#: present the returned pixels are already upright, so the EXIF orientation must *not* be
#: applied a second time (P4-00C-A report §3, measured on both file shapes).
CONTAINER_ORIENTATION_BOXES = ("irot", "imir")

_LOCK = threading.Lock()
_PROBED = False
_AVAILABLE = False
_ERROR: str | None = None
_MODULE_VERSION: str | None = None
_LIBHEIF_VERSION: str | None = None

#: The Pillow registries ``pillow_heif.register_heif_opener()`` writes to. They are
#: snapshotted before the call and restored when it fails: ``register_heif_opener`` installs
#: the Pillow opener *before* it asks the C extension about its codecs, so a missing libheif
#: DLL (P4-00C-B's degraded bundle) would otherwise leave a registered-but-broken plugin
#: behind and turn the fail-closed answer into a per-file ``DECODE_FAILED``.
_REGISTRY_NAMES = ("OPEN", "ID", "SAVE", "SAVE_ALL", "MIME", "EXTENSION")


def _snapshot_registries() -> dict[str, Any]:
    """A private copy of every Pillow registry the registration call can mutate."""
    from PIL import Image

    snapshot: dict[str, Any] = {}
    for name in _REGISTRY_NAMES:
        value = getattr(Image, name, None)
        if isinstance(value, dict):
            snapshot[name] = dict(value)
        elif isinstance(value, list):
            snapshot[name] = list(value)
    return snapshot


def _restore_registries(snapshot: Mapping[str, Any]) -> None:
    """Put the snapshot back *in place*, so no caller keeps a stale registry object."""
    from PIL import Image

    for name, value in snapshot.items():
        current = getattr(Image, name, None)
        if isinstance(current, dict) and isinstance(value, dict):
            current.clear()
            current.update(value)
        elif isinstance(current, list) and isinstance(value, list):
            current[:] = value


def ensure_heif_support() -> bool:
    """Register the HEIF Pillow plugin once; ``True`` when HEIC/HEIF can be decoded.

    The result is probed once per process and cached, so the call is cheap to repeat and
    every caller sees the same answer. A failure of either the import or the registration
    is remembered as "not available" instead of being raised (fail closed, architecture
    §5.4: an unverified format is reported, never decoded opportunistically).

    The registration is **atomic**: ``pillow_heif.register_heif_opener()`` registers the
    Pillow opener first and only then asks the C extension for its codecs, so a wheel whose
    extension or libheif runtime is missing raises *after* the opener is installed. Any
    failure therefore rolls the Pillow registries back to their exact previous state, which
    is what makes a degraded build answer ``FORMAT_UNSUPPORTED`` for HEIC instead of
    ``DECODE_FAILED`` (P4-00C-B degraded acceptance).
    """
    global _PROBED, _AVAILABLE, _ERROR, _MODULE_VERSION, _LIBHEIF_VERSION
    with _LOCK:
        if _PROBED:
            return _AVAILABLE
        _PROBED = True
        snapshot = _snapshot_registries()
        try:
            import pillow_heif

            pillow_heif.register_heif_opener()
            # Force the runtime read inside the same try: with a missing libheif DLL it is
            # this call that raises, and the rollback below is what removes the opener again.
            _LIBHEIF_VERSION = str(pillow_heif.libheif_version())
            _MODULE_VERSION = str(getattr(pillow_heif, "__version__", "unknown"))
        except Exception as error:  # noqa: BLE001 - any failure is "this build cannot read HEIC"
            _restore_registries(snapshot)
            _ERROR = f"{type(error).__name__}: {error}"[:200]
            _AVAILABLE = False
            return False
        _AVAILABLE = True
        return True


def heif_available() -> bool:
    """``True`` when this process can decode HEIC/HEIF (probing on first use)."""
    return ensure_heif_support()


def runtime_fact() -> dict[str, Any]:
    """The measured pillow-heif/libheif fact behind the capability list (report evidence)."""
    available = ensure_heif_support()
    return {
        "available": available,
        "module_version": _MODULE_VERSION,
        "libheif_version": _LIBHEIF_VERSION,
        "error": _ERROR,
        "formats": sorted(HEIF_INPUT_FORMATS) if available else [],
    }


def recorded_orientation(image: Any) -> int | None:
    """The orientation pillow-heif removed from a HEIF EXIF block, or ``None``.

    Only a HEIF image can carry the value, only ``1..8`` is a legal EXIF orientation, and
    a non-HEIF image (or one this build cannot decode) is never asked.
    """
    if str(getattr(image, "format", "") or "").upper() not in HEIF_INPUT_FORMATS:
        return None
    info = getattr(image, "info", None)
    if not isinstance(info, dict):
        return None
    value = info.get(ORIGINAL_ORIENTATION_KEY)
    if value is None:
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if 1 <= number <= 8 else None


def container_orientation_applied(image: Any) -> bool | None:
    """Whether libheif already applied an ``irot``/``imir`` transform to this image.

    ``True``  - the container carries the transform, so the decoded pixels are upright and
                the EXIF orientation (which ``pillow_heif`` still reports through
                :func:`recorded_orientation`) must be ignored.
    ``False`` - the container carries no orientation property, so a recorded EXIF
                orientation still has to be applied by the caller.
    ``None``  - the container property list could not be read (not a HEIF image, or a
                plugin whose private handle moved). The caller then keeps pillow-heif's own
                statement instead of inventing a rotation.

    Read through the handle ``pillow_heif`` itself keeps on the opened image, so no image
    parsing and no extra decode happens here; ``pillow-heif`` is pinned exactly, and
    ``tests/unit/test_p4_heic_media.py`` asserts this discriminator on both file shapes.
    """
    handle = getattr(image, "_heif_file", None)
    if handle is None:
        return None
    try:
        transforms = handle[int(image.tell())]._c_image.transformations
    except Exception:  # noqa: BLE001 - an unreadable property list is "cannot measure"
        return None
    if transforms is None:
        return None
    try:
        return any(str(entry[0]) in CONTAINER_ORIENTATION_BOXES for entry in transforms)
    except (TypeError, IndexError):
        return None


__all__ = [
    "CONTAINER_ORIENTATION_BOXES",
    "HEIF_INPUT_FORMATS",
    "ORIGINAL_ORIENTATION_KEY",
    "container_orientation_applied",
    "ensure_heif_support",
    "heif_available",
    "recorded_orientation",
    "runtime_fact",
]
