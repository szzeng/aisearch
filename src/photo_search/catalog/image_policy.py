"""Static extension-only policy for files treated as images by the catalog."""

from __future__ import annotations

IMAGE_EXTENSIONS = frozenset({
    # Common raster and camera formats.
    ".jpg", ".jpeg", ".jpe", ".jfif", ".png", ".webp", ".bmp", ".gif", ".mpo",
    ".tif", ".tiff", ".heic", ".heif", ".hif", ".avif", ".jxl", ".jp2", ".j2k",
    ".jpf", ".jpx", ".ico", ".psd", ".tga", ".ppm", ".pgm", ".pbm", ".svg",
    # Camera RAW families. Deliberately excludes .r3d (RED video) and .eip containers.
    ".dng", ".cr2", ".cr3", ".nef", ".nrw", ".arw", ".sr2", ".srf", ".srw",
    ".raf", ".rw2", ".orf", ".pef", ".raw", ".rwl", ".3fr", ".x3f", ".k25",
    ".kdc", ".dcr", ".mos", ".mef", ".erf", ".iiq", ".ari", ".bay", ".cap",
    ".dcs", ".drf", ".fff", ".gpr", ".mdc", ".mrw", ".obm", ".ptx", ".pxn",
    ".rwz",
})


def is_image_path(relative_path: str) -> bool:
    """Return whether the final filename extension is in the static image allowlist."""
    name = str(relative_path).replace("\\", "/").rsplit("/", 1)[-1]
    suffix = "." + name.rsplit(".", 1)[-1].casefold() if "." in name else ""
    return suffix in IMAGE_EXTENSIONS


def image_location_sql(alias: str = "l") -> str:
    """SQL predicate for a registered path whose extension is an allowed image type."""
    patterns = " OR ".join(
        f"LOWER({alias}.relative_path) GLOB '*{extension}'"
        for extension in sorted(IMAGE_EXTENSIONS)
    )
    return f"({patterns})"


def photo_asset_sql(
    *, asset_alias: str = "a", source_id: int | None = None
) -> tuple[str, list[int]]:
    """Keep zero-location legacy assets; otherwise require a qualifying image path."""
    alias = "photo_location"
    predicate = image_location_sql(alias)
    if source_id is not None:
        return (
            f"EXISTS (SELECT 1 FROM file_locations AS {alias} WHERE {alias}.asset_id = "
            f"{asset_alias}.asset_id AND {alias}.source_id = ? AND {predicate})",
            [int(source_id)],
        )
    return (
        f"(NOT EXISTS (SELECT 1 FROM file_locations AS any_location WHERE "
        f"any_location.asset_id = {asset_alias}.asset_id) OR EXISTS (SELECT 1 FROM "
        f"file_locations AS {alias} WHERE {alias}.asset_id = {asset_alias}.asset_id "
        f"AND {predicate}))",
        [],
    )
