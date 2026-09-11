"""Data contract for the authorized evaluation set (architecture §12.3).

The quality stage only runs on images the user explicitly authorized. This module is the
single place that defines the on-disk schema, enforces the tuning/holdout separation and
refuses to treat a partially labelled query as measurable, so a later evaluator cannot
quietly mix the two splits.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
SPLITS = ("tuning", "holdout")


class DatasetError(ValueError):
    """The evaluation set violates the documented schema or the split contract."""


@dataclass(frozen=True)
class EvalImage:
    id: str
    path: Path
    split: str
    sha256: str | None = None


@dataclass(frozen=True)
class EvalQuery:
    id: str
    text: str
    split: str
    relevant: dict[str, int]


@dataclass(frozen=True)
class EvalDataset:
    """Validated evaluation set: images, Chinese queries and their graded relevance."""

    name: str
    images: tuple[EvalImage, ...]
    queries: tuple[EvalQuery, ...]
    annotation_rules: str
    authorization: str

    def images_for_split(self, split: str) -> tuple[EvalImage, ...]:
        return tuple(image for image in self.images if image.split == split)

    def queries_for_split(self, split: str) -> tuple[EvalQuery, ...]:
        return tuple(query for query in self.queries if query.split == split)

    def summary(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "images": {
                split: len(self.images_for_split(split)) for split in SPLITS
            },
            "queries": {
                split: len(self.queries_for_split(split)) for split in SPLITS
            },
            "annotation_rules": self.annotation_rules,
            "authorization": self.authorization,
        }


def load_dataset(path: Path, *, base_directory: Path | None = None) -> EvalDataset:
    """Parse and validate an evaluation set file."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise DatasetError(f"cannot read dataset {path}: {error}") from error
    return parse_dataset(raw, base_directory=base_directory or path.parent)


def parse_dataset(raw: dict[str, Any], *, base_directory: Path) -> EvalDataset:
    if int(raw.get("schema_version", 0)) != SCHEMA_VERSION:
        raise DatasetError(f"unsupported schema_version: {raw.get('schema_version')!r}")
    authorization = str(raw.get("authorization") or "").strip()
    if not authorization:
        raise DatasetError("the dataset must record the user's explicit authorization")

    images: list[EvalImage] = []
    seen_images: set[str] = set()
    for entry in raw.get("images") or []:
        image_id = str(entry["id"])
        if image_id in seen_images:
            raise DatasetError(f"duplicate image id: {image_id}")
        seen_images.add(image_id)
        split = str(entry.get("split", ""))
        if split not in SPLITS:
            raise DatasetError(f"image {image_id} has an unknown split {split!r}")
        images.append(
            EvalImage(
                id=image_id,
                path=_resolve(entry["path"], base_directory),
                split=split,
                sha256=entry.get("sha256"),
            )
        )
    if not images:
        raise DatasetError("the dataset contains no images")

    queries: list[EvalQuery] = []
    seen_queries: set[str] = set()
    for entry in raw.get("queries") or []:
        query_id = str(entry["id"])
        if query_id in seen_queries:
            raise DatasetError(f"duplicate query id: {query_id}")
        seen_queries.add(query_id)
        split = str(entry.get("split", ""))
        if split not in SPLITS:
            raise DatasetError(f"query {query_id} has an unknown split {split!r}")
        text = str(entry.get("text") or "").strip()
        if not text:
            raise DatasetError(f"query {query_id} has no text")
        relevant = {
            str(image_id): int(grade)
            for image_id, grade in (entry.get("relevant") or {}).items()
        }
        if not relevant:
            raise DatasetError(f"query {query_id} has no graded relevance labels")
        for image_id in relevant:
            if image_id not in seen_images:
                raise DatasetError(f"query {query_id} references unknown image {image_id}")
            image_split = next(image.split for image in images if image.id == image_id)
            if image_split != split:
                raise DatasetError(
                    f"query {query_id} ({split}) uses {image_id} from the {image_split} split"
                )
        if any(grade <= 0 for grade in relevant.values()):
            raise DatasetError(f"query {query_id} has a non-positive relevance grade")
        queries.append(EvalQuery(id=query_id, text=text, split=split, relevant=relevant))
    if not queries:
        raise DatasetError("the dataset contains no queries")

    return EvalDataset(
        name=str(raw.get("name") or "unnamed"),
        images=tuple(images),
        queries=tuple(queries),
        annotation_rules=str(raw.get("annotation_rules") or ""),
        authorization=authorization,
    )


def _resolve(value: str, base_directory: Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (base_directory / path)
