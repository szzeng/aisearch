"""Shared, extension-only video location policy for ordinary photo queries."""

from __future__ import annotations

from photo_search.catalog.image_policy import photo_asset_sql

__all__ = ("VIDEO_EXTENSIONS", "is_video_path", "non_video_location_sql", "photo_asset_sql")

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
