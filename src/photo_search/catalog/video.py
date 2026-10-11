"""Shared, extension-only video location policy for ordinary photo queries."""

from __future__ import annotations

VIDEO_EXTENSIONS = frozenset({
    ".mp4", ".mov", ".m4v", ".avi", ".mkv", ".wmv", ".flv", ".webm",
    ".mpg", ".mpeg", ".m2v", ".mts", ".m2ts", ".ts", ".3gp", ".3g2",
    ".vob", ".asf",
})


def is_video_path(relative_path: str) -> bool:
    name = str(relative_path).replace("\\", "/").rsplit("/", 1)[-1]
    return "." in name and ("." + name.rsplit(".", 1)[-1].casefold()) in VIDEO_EXTENSIONS


def non_video_location_sql(alias: str = "l") -> str:
    """SQL predicate for one registered, non-video location; alias is internal SQL."""
    video_patterns = " OR ".join(
        f"LOWER({alias}.relative_path) GLOB '*." + extension[1:] + "'"
        for extension in sorted(VIDEO_EXTENSIONS)
    )
    return f"NOT ({video_patterns})"


def photo_asset_sql(*, asset_alias: str = "a", source_id: int | None = None) -> tuple[str, list[int]]:
    """Keep legacy zero-location assets; otherwise require a non-video registered path.

    With a source scope, the qualifying photo path must itself belong to that source.
    """
    alias = "photo_location"
    predicate = non_video_location_sql(alias)
    if source_id is not None:
        return (
            f"EXISTS (SELECT 1 FROM file_locations AS {alias} WHERE {alias}.asset_id = "
            f"{asset_alias}.asset_id AND {alias}.source_id = ? AND {predicate})",
            [int(source_id)],
        )
    return (
        f"(NOT EXISTS (SELECT 1 FROM file_locations AS any_location WHERE "
        f"any_location.asset_id = {asset_alias}.asset_id) OR EXISTS (SELECT 1 FROM "
        f"file_locations AS {alias} WHERE {alias}.asset_id = {asset_alias}.asset_id AND {predicate}))",
        [],
    )
