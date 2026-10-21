"""The offline name-dataset package: its frozen manifest and its read-only content.

ADR-0005 §8 fixes the package lifecycle and the manifest. This module owns the *file* half
of that contract: it parses a manifest, verifies every declared file's size and checksum,
reads the places/aliases/hierarchy records, refuses an illegal path or a broken hierarchy
reference, and answers the only question the resolver asks - which city points are near a
WGS84 coordinate. It never downloads anything, never writes into the catalog and never
sees a photo.

``build_package`` is the deterministic builder the explicit local preparation tool wraps;
it only writes the package directory the caller names and performs no network access.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from photo_search.catalog.errors import PLACE_DATASET_INVALID, PLACE_DATASET_MISSING, CatalogError
from photo_search.places.contracts import CONTRACT_VERSION, canonical_json

MANIFEST_SCHEMA = "photo-search-place-dataset-manifest.v1"
MANIFEST_FILENAME = "manifest.json"

ROLE_PLACES = "PLACES"
ROLE_ALIASES = "ALIASES"
ROLE_HIERARCHY = "HIERARCHY"
ROLE_LICENSE = "LICENSE"
ROLE_INDEX = "INDEX"
ROLES = (ROLE_PLACES, ROLE_ALIASES, ROLE_HIERARCHY, ROLE_LICENSE, ROLE_INDEX)

DATASET_STATES = ("STAGED", "VALIDATED", "ACTIVE", "RETAINED")

#: The kind values a dataset place record may carry. Only a ``city`` produces a candidate;
#: a ``country`` exists to place that city in a hierarchy.
PLACE_RECORD_KINDS = ("country", "city")

_SOURCE_KEYS = frozenset(
    {"provider", "source_url", "snapshot", "license_id", "license_url", "attribution"}
)
_BUILD_PARAMETER_KEYS = frozenset(
    {"source_files", "minimum_population", "languages", "include_aliases", "sort_order"}
)
_BUILD_KEYS = frozenset(
    {"builder_id", "builder_version", "generated_at", "parameters_sha256", "parameters"}
)
_COVERAGE_KEYS = frozenset(
    {"country_codes", "bounds_wgs84", "languages", "alias_languages", "record_count"}
)
_BOUNDS_KEYS = frozenset({"south", "west", "north", "east"})
_FILE_KEYS = frozenset({"relative_path", "role", "media_type", "sha256", "byte_size"})
MANIFEST_KEYS = frozenset(
    {
        "contract_version",
        "manifest_schema",
        "package_id",
        "package_version",
        "dataset_id",
        "source",
        "build",
        "content_sha256",
        "coordinate_reference_system",
        "coverage",
        "compatible_catalog_schema",
        "files",
        "state",
        "previous_dataset_id",
        "validated_at",
        "activated_at",
        "retained_at",
    }
)
_COMPATIBLE_KEYS = frozenset({"minimum", "maximum"})
PLACE_LEGACY_MAX_FLOOR = 16
# Schemas through 33 have been reviewed for package compatibility. Official 0032
# adds face-collection anchors; 0033 adds catalog read indexes. Neither changes
# place tables, GeoNames bytes, projection semantics or the CN addon contract.
# Keep this explicit: a future catalog version still needs a compatibility review.
# Schemas 34–36 add favorite and ordinary photo collection state; 0037 adds
# independent smart-collection rules and rebuildable cache tables; 0038 adds
# independent retention evidence/source-anomaly tables and 0039 adds retention
# tombstones. None changes place tables, GeoNames bytes, projection semantics or
# the CN addon contract.
# Schemas 0040-0042 only extend retention media identities and cleanup run
# settings/items/candidate fences. They do not change the place package contract.
# Keep this explicit: a future catalog version still needs a compatibility review.
PLACE_COMPAT_REVIEWED_THROUGH = 43

#: Earth radius used by the deterministic haversine distance, in metres.
EARTH_RADIUS_M = 6371008.8


@dataclass(frozen=True)
class DatasetFile:
    """One declared package file, with the verified on-disk size and checksum."""

    relative_path: str
    role: str
    media_type: str
    sha256: str
    byte_size: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "relative_path": self.relative_path,
            "role": self.role,
            "media_type": self.media_type,
            "sha256": self.sha256,
            "byte_size": int(self.byte_size),
        }


@dataclass(frozen=True)
class DatasetPlace:
    dataset_place_id: str
    kind: str
    display_name: str
    country_code: str | None
    latitude: float
    longitude: float
    population: int
    language: str | None
    parent_dataset_place_id: str | None = None


@dataclass(frozen=True)
class DatasetContent:
    """A verified package: the manifest plus its parsed, hierarchy-checked records."""

    dataset_id: str
    package_id: str
    package_version: str
    provider: str
    state_on_disk: str
    manifest: Mapping[str, Any]
    files: tuple[DatasetFile, ...]
    places: Mapping[str, DatasetPlace]
    aliases: Mapping[str, tuple[dict[str, Any], ...]] = field(default_factory=dict)
    province_index: Mapping[str, Any] = field(default_factory=dict)
    cn_admin: Any = None
    cn_legacy_links: Mapping[str, Any] = field(default_factory=dict)

    @property
    def city_places(self) -> tuple[DatasetPlace, ...]:
        return tuple(place for place in self.places.values() if place.kind == "city")

    def ancestors(self, dataset_place_id: str) -> list[str]:
        """The display names of a place's ancestors, nearest parent first."""
        names: list[str] = []
        seen: set[str] = set()
        current = self.places[dataset_place_id].parent_dataset_place_id
        while current is not None:
            if current in seen:  # pragma: no cover - load_content rejects cycles
                break
            seen.add(current)
            names.append(self.places[current].display_name)
            current = self.places[current].parent_dataset_place_id
        return names


def _invalid(message: str, **details: Any) -> CatalogError:
    return CatalogError(
        PLACE_DATASET_INVALID, message, details={key: value for key, value in details.items()} or None
    )


def _missing(message: str, **details: Any) -> CatalogError:
    return CatalogError(
        PLACE_DATASET_MISSING, message, details={key: value for key, value in details.items()} or None
    )


def _text(value: Any, *, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _invalid(f"{where} must be a non-empty string")
    return value


def _sha256_text(value: Any, *, where: str) -> str:
    text = _text(value, where=where)
    if len(text) != 64 or any(char not in "0123456789abcdef" for char in text):
        raise _invalid(f"{where} must be a lower-case sha256")
    return text


def _number(value: Any, *, where: str, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _invalid(f"{where} must be a number")
    number = float(value)
    if not math.isfinite(number) or not low <= number <= high:
        raise _invalid(f"{where} must be a finite number in [{low}, {high}]")
    return number


def _integer(value: Any, *, where: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or int(value) < minimum:
        raise _invalid(f"{where} must be an integer >= {minimum}")
    return int(value)


def _closed_object(value: Any, keys: frozenset[str], *, where: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise _invalid(f"{where} must be an object")
    unexpected = sorted(set(value) - keys)
    if unexpected:
        raise _invalid(f"{where} carries unknown fields", fields=unexpected)
    missing = sorted(keys - set(value))
    if missing:
        raise _invalid(f"{where} is missing required fields", fields=missing)
    return value


def _string_list(value: Any, *, where: str, min_items: int = 0) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise _invalid(f"{where} must be a list of non-empty strings")
    if len(value) < min_items:
        raise _invalid(f"{where} needs at least {min_items} entries")
    if len(set(value)) != len(value):
        raise _invalid(f"{where} must not repeat an entry")
    return [str(item) for item in value]


def check_relative_path(value: Any, *, where: str = "relative_path") -> str:
    """A package-relative path: no drive prefix, no leading separator, no ``..``.

    The frozen manifest pattern forbids a ``..`` *segment*; this implementation refuses any
    ``..`` substring, which is strictly stricter and can never accept a path the schema
    would reject.
    """
    text = _text(value, where=where)
    if text.startswith("/") or text.startswith("\\") or (len(text) > 1 and text[1] == ":"):
        raise _invalid(f"{where} must be a package-relative path")
    if ".." in text:
        raise _invalid(f"{where} must not contain a parent-directory reference")
    return text


def _legacy_place_schema_compatible(
    *,
    compatible_minimum: int,
    compatible_maximum: int,
    supported_schema_version: int,
    reviewed_through: int = PLACE_COMPAT_REVIEWED_THROUGH,
) -> bool:
    """Return whether an immutable legacy package is covered by the reviewed bridge."""
    return (
        int(compatible_minimum) <= int(supported_schema_version)
        and int(compatible_maximum) >= PLACE_LEGACY_MAX_FLOOR
        and int(compatible_maximum) < int(supported_schema_version)
        and int(supported_schema_version) <= int(reviewed_through)
    )


def validate_manifest(manifest: Any, *, supported_schema_version: int) -> dict[str, Any]:
    """Validate a decoded manifest against the frozen shape and the compatibility rules.

    Returns a normalised copy. Every failure is ``PLACE_DATASET_INVALID``: a bad manifest is
    a package defect, never a caller-shape error.
    """
    raw = _closed_object(manifest, MANIFEST_KEYS, where="manifest")
    if raw["contract_version"] != CONTRACT_VERSION:
        raise _invalid("the manifest carries a foreign contract version")
    if raw["manifest_schema"] != MANIFEST_SCHEMA:
        raise _invalid("unknown manifest schema")
    package_id = _text(raw["package_id"], where="package_id")
    package_version = _text(raw["package_version"], where="package_version")
    dataset_id = _text(raw["dataset_id"], where="dataset_id")
    source = _closed_object(raw["source"], _SOURCE_KEYS, where="source")
    for key in sorted(_SOURCE_KEYS):
        _text(source[key], where=f"source.{key}")
    build = _closed_object(raw["build"], _BUILD_KEYS, where="build")
    _text(build["builder_id"], where="build.builder_id")
    _text(build["builder_version"], where="build.builder_version")
    generated_at = _integer(build["generated_at"], where="build.generated_at")
    parameters_sha256 = _sha256_text(build["parameters_sha256"], where="build.parameters_sha256")
    parameters = _closed_object(
        build["parameters"], _BUILD_PARAMETER_KEYS, where="build.parameters"
    )
    source_files = _string_list(
        parameters["source_files"], where="build.parameters.source_files", min_items=1
    )
    minimum_population = _integer(
        parameters["minimum_population"], where="build.parameters.minimum_population"
    )
    languages = _string_list(
        parameters["languages"], where="build.parameters.languages", min_items=1
    )
    if not isinstance(parameters["include_aliases"], bool):
        raise _invalid("build.parameters.include_aliases must be a boolean")
    sort_order = _text(parameters["sort_order"], where="build.parameters.sort_order")
    normalized_parameters = {
        "source_files": source_files,
        "minimum_population": minimum_population,
        "languages": languages,
        "include_aliases": bool(parameters["include_aliases"]),
        "sort_order": sort_order,
    }
    # The declared build-parameter digest must describe the parameters it ships with.
    expected_parameters_sha256 = hashlib.sha256(
        canonical_json(normalized_parameters).encode("utf-8")
    ).hexdigest()
    if expected_parameters_sha256 != parameters_sha256:
        raise _invalid("build.parameters_sha256 does not describe build.parameters")
    content_sha256 = _sha256_text(raw["content_sha256"], where="content_sha256")
    if raw["coordinate_reference_system"] != "WGS84":
        raise _invalid("only a WGS84 dataset may be activated")
    coverage = _closed_object(raw["coverage"], _COVERAGE_KEYS, where="coverage")
    country_codes = _string_list(
        coverage["country_codes"], where="coverage.country_codes", min_items=1
    )
    for code in country_codes:
        if len(code) != 2 or not code.isupper() or not code.isalpha():
            raise _invalid("coverage.country_codes must be upper-case ISO-3166 alpha-2")
    bounds = _closed_object(coverage["bounds_wgs84"], _BOUNDS_KEYS, where="coverage.bounds_wgs84")
    south = _number(bounds["south"], where="coverage.bounds_wgs84.south", low=-90.0, high=90.0)
    north = _number(bounds["north"], where="coverage.bounds_wgs84.north", low=-90.0, high=90.0)
    west = _number(bounds["west"], where="coverage.bounds_wgs84.west", low=-180.0, high=180.0)
    east = _number(bounds["east"], where="coverage.bounds_wgs84.east", low=-180.0, high=180.0)
    if south > north or west > east:
        raise _invalid("coverage.bounds_wgs84 is not a valid box")
    coverage_languages = _string_list(
        coverage["languages"], where="coverage.languages", min_items=1
    )
    alias_languages = _string_list(
        coverage["alias_languages"], where="coverage.alias_languages"
    )
    record_count = _integer(coverage["record_count"], where="coverage.record_count")
    compatible = _closed_object(
        raw["compatible_catalog_schema"], _COMPATIBLE_KEYS, where="compatible_catalog_schema"
    )
    compatible_min = _integer(
        compatible["minimum"], where="compatible_catalog_schema.minimum", minimum=1
    )
    compatible_max = _integer(
        compatible["maximum"], where="compatible_catalog_schema.maximum", minimum=1
    )
    if compatible_min > compatible_max:
        raise _invalid("compatible_catalog_schema has an empty range")
    # A reviewed bridge preserves immutable packages whose declared maximum is
    # below this catalog schema. Keep its historical floor and review ceiling explicit.
    legacy_package_bridge = _legacy_place_schema_compatible(
        compatible_minimum=compatible_min,
        compatible_maximum=compatible_max,
        supported_schema_version=int(supported_schema_version),
    )
    if not (
        compatible_min <= int(supported_schema_version) <= compatible_max or legacy_package_bridge
    ):
        raise _invalid(
            "the dataset is not compatible with this catalog schema",
            supported_schema_version=int(supported_schema_version),
            compatible_minimum=compatible_min,
            compatible_maximum=compatible_max,
        )
    if not isinstance(raw["files"], list) or not raw["files"]:
        raise _invalid("files must be a non-empty list")
    files: list[DatasetFile] = []
    seen_paths: set[str] = set()
    for index, entry in enumerate(raw["files"]):
        item = _closed_object(entry, _FILE_KEYS, where=f"files[{index}]")
        relative_path = check_relative_path(
            item["relative_path"], where=f"files[{index}].relative_path"
        )
        if relative_path in seen_paths:
            raise _invalid("files must not repeat a relative_path", relative_path=relative_path)
        seen_paths.add(relative_path)
        role = item["role"]
        if role not in ROLES:
            raise _invalid(f"files[{index}].role is not one of {list(ROLES)}")
        files.append(
            DatasetFile(
                relative_path=relative_path,
                role=str(role),
                media_type=_text(item["media_type"], where=f"files[{index}].media_type"),
                sha256=_sha256_text(item["sha256"], where=f"files[{index}].sha256"),
                byte_size=_integer(item["byte_size"], where=f"files[{index}].byte_size"),
            )
        )
    places_files = [item for item in files if item.role == ROLE_PLACES]
    if len(places_files) != 1:
        raise _invalid("a dataset needs exactly one PLACES file")
    if not any(item.role == ROLE_LICENSE for item in files):
        raise _invalid("a dataset must ship its license file")
    state = raw["state"]
    if state not in DATASET_STATES:
        raise _invalid(f"state must be one of {list(DATASET_STATES)}")
    previous = raw["previous_dataset_id"]
    if previous is not None:
        previous = _text(previous, where="previous_dataset_id")
        if previous == dataset_id:
            raise _invalid("previous_dataset_id must not name the dataset itself")
    timestamps: dict[str, int | None] = {}
    for key in ("validated_at", "activated_at", "retained_at"):
        value = raw[key]
        timestamps[key] = None if value is None else _integer(value, where=key)
    return {
        "contract_version": CONTRACT_VERSION,
        "manifest_schema": MANIFEST_SCHEMA,
        "package_id": package_id,
        "package_version": package_version,
        "dataset_id": dataset_id,
        "source": {key: str(source[key]) for key in _SOURCE_KEYS},
        "build": {
            "builder_id": str(build["builder_id"]),
            "builder_version": str(build["builder_version"]),
            "generated_at": generated_at,
            "parameters_sha256": parameters_sha256,
            "parameters": normalized_parameters,
        },
        "content_sha256": content_sha256,
        "coordinate_reference_system": "WGS84",
        "coverage": {
            "country_codes": country_codes,
            "bounds_wgs84": {
                "south": south,
                "west": west,
                "north": north,
                "east": east,
            },
            "languages": coverage_languages,
            "alias_languages": alias_languages,
            "record_count": record_count,
        },
        "compatible_catalog_schema": {"minimum": compatible_min, "maximum": compatible_max},
        "files": [item.as_dict() for item in files],
        "state": str(state),
        "previous_dataset_id": previous,
        "validated_at": timestamps["validated_at"],
        "activated_at": timestamps["activated_at"],
        "retained_at": timestamps["retained_at"],
    }


def declared_content_sha256(files: list[DatasetFile] | tuple[DatasetFile, ...]) -> str:
    """The canonical digest of a package's *content* listing.

    The manifest itself is deliberately excluded (it carries the digest), so the value is a
    pure function of the declared files: sorted relative_path/role/sha256/byte_size tuples
    rendered as canonical JSON.
    """
    payload = [
        {
            "relative_path": item.relative_path,
            "role": item.role,
            "sha256": item.sha256,
            "byte_size": int(item.byte_size),
        }
        for item in sorted(files, key=lambda value: value.relative_path)
    ]
    return hashlib.sha256(canonical_json({"files": payload}).encode("utf-8")).hexdigest()


def read_manifest(package_dir: str | Path) -> dict[str, Any]:
    """Read and decode ``manifest.json``; a missing or broken one is a package defect."""
    directory = Path(package_dir)
    if not directory.is_dir():
        raise _missing("the dataset package directory does not exist")
    path = directory / MANIFEST_FILENAME
    if not path.is_file():
        raise _missing("the dataset package has no manifest.json")
    try:
        decoded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise _invalid("the dataset manifest is not valid UTF-8 JSON") from error
    if not isinstance(decoded, Mapping):
        raise _invalid("the dataset manifest must be a JSON object")
    return dict(decoded)


def verify_files(package_dir: str | Path, files: list[dict[str, Any]]) -> tuple[DatasetFile, ...]:
    """Check that every declared file exists with the declared size and SHA-256."""
    directory = Path(package_dir)
    verified: list[DatasetFile] = []
    for index, entry in enumerate(files):
        relative_path = check_relative_path(
            entry["relative_path"], where=f"files[{index}].relative_path"
        )
        path = directory / relative_path
        if not path.is_file():
            raise _missing("a declared dataset file is missing", relative_path=relative_path)
        try:
            data = path.read_bytes()
        except OSError as error:
            raise _missing(
                "a declared dataset file cannot be read", relative_path=relative_path
            ) from error
        if len(data) != int(entry["byte_size"]):
            raise _invalid(
                "a dataset file does not match its declared size",
                relative_path=relative_path,
                declared=int(entry["byte_size"]),
                actual=len(data),
            )
        digest = hashlib.sha256(data).hexdigest()
        if digest != str(entry["sha256"]):
            raise _invalid(
                "a dataset file does not match its declared checksum",
                relative_path=relative_path,
            )
        verified.append(
            DatasetFile(
                relative_path=relative_path,
                role=str(entry["role"]),
                media_type=str(entry["media_type"]),
                sha256=digest,
                byte_size=len(data),
            )
        )
    return tuple(verified)


def _jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise _invalid("a dataset file is not readable UTF-8", relative_path=path.name) from error
    rows: list[dict[str, Any]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            decoded = json.loads(line)
        except json.JSONDecodeError as error:
            raise _invalid(
                "a dataset record is not valid JSON", relative_path=path.name, line=number
            ) from error
        if not isinstance(decoded, Mapping):
            raise _invalid(
                "a dataset record must be an object", relative_path=path.name, line=number
            )
        rows.append(dict(decoded))
    return rows


_PLACE_KEYS = frozenset(
    {
        "dataset_place_id",
        "kind",
        "display_name",
        "country_code",
        "latitude",
        "longitude",
        "population",
        "language",
    }
)


def _parse_places(directory: Path, relative_path: str) -> dict[str, DatasetPlace]:
    places: dict[str, DatasetPlace] = {}
    for index, row in enumerate(_jsonl(directory / relative_path)):
        where = f"places[{index}]"
        unexpected = sorted(set(row) - _PLACE_KEYS)
        if unexpected:
            raise _invalid(f"{where} carries unknown fields", fields=unexpected)
        if "dataset_place_id" not in row or "kind" not in row or "display_name" not in row:
            raise _invalid(f"{where} is missing a required field")
        dataset_place_id = _text(row["dataset_place_id"], where=f"{where}.dataset_place_id")
        if dataset_place_id in places:
            raise _invalid("the places file repeats a dataset_place_id", dataset_place_id=dataset_place_id)
        kind = row["kind"]
        if kind not in PLACE_RECORD_KINDS:
            raise _invalid(f"{where}.kind must be one of {list(PLACE_RECORD_KINDS)}")
        display_name = _text(row["display_name"], where=f"{where}.display_name")
        country_code = row.get("country_code")
        if country_code is not None:
            country_code = _text(country_code, where=f"{where}.country_code")
        latitude = _number(row.get("latitude"), where=f"{where}.latitude", low=-90.0, high=90.0)
        longitude = _number(row.get("longitude"), where=f"{where}.longitude", low=-180.0, high=180.0)
        population = _integer(row.get("population", 0), where=f"{where}.population")
        language = row.get("language")
        if language is not None:
            language = _text(language, where=f"{where}.language")
        places[dataset_place_id] = DatasetPlace(
            dataset_place_id=dataset_place_id,
            kind=str(kind),
            display_name=display_name,
            country_code=country_code,
            latitude=latitude,
            longitude=longitude,
            population=population,
            language=language,
        )
    if not places:
        raise _invalid("the places file has no records")
    return places


def _parse_hierarchy(
    directory: Path, relative_path: str, places: Mapping[str, DatasetPlace]
) -> dict[str, str]:
    parents: dict[str, str] = {}
    for index, row in enumerate(_jsonl(directory / relative_path)):
        where = f"hierarchy[{index}]"
        if "child" not in row or "parent" not in row:
            raise _invalid(f"{where} is missing a required field")
        child = _text(row["child"], where=f"{where}.child")
        parent = _text(row["parent"], where=f"{where}.parent")
        if child not in places or parent not in places:
            raise _invalid("the hierarchy references an unknown place", child=child, parent=parent)
        if child == parent:
            raise _invalid("the hierarchy makes a place its own parent", child=child)
        if child in parents:
            raise _invalid("the hierarchy gives a place two parents", child=child)
        if places[child].kind == "country":
            raise _invalid("a country never has a parent", child=child)
        parents[child] = parent
    for child, parent in parents.items():
        seen = {child}
        current: str | None = parent
        while current is not None:
            if current in seen:
                raise _invalid("the hierarchy contains a cycle", child=child)
            seen.add(current)
            current = parents.get(current)
    return parents


def _parse_aliases(
    directory: Path, relative_path: str, places: Mapping[str, DatasetPlace]
) -> dict[str, tuple[dict[str, Any], ...]]:
    collected: dict[str, list[dict[str, Any]]] = {}
    for index, row in enumerate(_jsonl(directory / relative_path)):
        where = f"aliases[{index}]"
        if "dataset_place_id" not in row or "text" not in row:
            raise _invalid(f"{where} is missing a required field")
        dataset_place_id = _text(row["dataset_place_id"], where=f"{where}.dataset_place_id")
        if dataset_place_id not in places:
            raise _invalid("an alias references an unknown place", dataset_place_id=dataset_place_id)
        text = _text(row["text"], where=f"{where}.text")
        language = row.get("language")
        if language is not None:
            language = _text(language, where=f"{where}.language")
        collected.setdefault(dataset_place_id, []).append({"text": text, "language": language})
    return {key: tuple(value) for key, value in collected.items()}


def _parse_province_index(directory: Path, relative_path: str) -> dict[str, Any]:
    try:
        payload = json.loads((directory / relative_path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise _invalid("the INDEX file is not valid UTF-8 JSON", relative_path=relative_path) from error
    if not isinstance(payload, Mapping) or set(payload) != {"schema_version", "provider", "source_snapshot", "city_to_admin1"}:
        raise _invalid("the INDEX file has an unsupported shape", relative_path=relative_path)
    if payload["schema_version"] != 1:
        raise _invalid("the INDEX schema_version must be 1", relative_path=relative_path)
    provider = _text(payload["provider"], where="INDEX.provider")
    snapshot = _text(payload["source_snapshot"], where="INDEX.source_snapshot")
    mapping = payload["city_to_admin1"]
    if not isinstance(mapping, Mapping):
        raise _invalid("INDEX.city_to_admin1 must be an object")
    result: dict[str, Any] = {}
    for city_id, region in mapping.items():
        city_key = _text(city_id, where="INDEX city id")
        if not isinstance(region, Mapping) or set(region) != {"id", "name", "aliases"}:
            raise _invalid("an INDEX admin1 row has an unsupported shape", city_id=city_key)
        aliases = region["aliases"]
        if not isinstance(aliases, list) or any(not isinstance(value, str) or not value.strip() for value in aliases):
            raise _invalid("INDEX aliases must be a list of non-empty strings", city_id=city_key)
        result[city_key] = {"id": _text(region["id"], where="INDEX admin1 id"), "name": _text(region["name"], where="INDEX admin1 name"), "aliases": list(dict.fromkeys(aliases))}
    return {"provider": provider, "source_snapshot": snapshot, "city_to_admin1": result}


def load_content(
    package_dir: str | Path,
    *,
    supported_schema_version: int,
    manifest: Mapping[str, Any] | None = None,
) -> DatasetContent:
    """Validate the manifest and every file, then parse the records and the hierarchy.

    Raises ``PLACE_DATASET_MISSING`` when a declared file is absent and
    ``PLACE_DATASET_INVALID`` for any checksum, format, compatibility or reference defect.
    """
    directory = Path(package_dir)
    validated = validate_manifest(
        dict(manifest) if manifest is not None else read_manifest(directory),
        supported_schema_version=int(supported_schema_version),
    )
    files = verify_files(directory, validated["files"])
    if declared_content_sha256(files) != validated["content_sha256"]:
        raise _invalid("the dataset content checksum does not match the manifest")
    by_role: dict[str, DatasetFile] = {}
    for item in files:
        if item.role == ROLE_INDEX and item.relative_path.startswith("cn-admin/"):
            continue  # versioned addon files never replace GeoNames' original INDEX
        by_role.setdefault(item.role, item)
    places = _parse_places(directory, by_role[ROLE_PLACES].relative_path)
    parents = (
        _parse_hierarchy(directory, by_role[ROLE_HIERARCHY].relative_path, places)
        if ROLE_HIERARCHY in by_role
        else {}
    )
    if parents:
        places = {
            key: DatasetPlace(
                dataset_place_id=value.dataset_place_id,
                kind=value.kind,
                display_name=value.display_name,
                country_code=value.country_code,
                latitude=value.latitude,
                longitude=value.longitude,
                population=value.population,
                language=value.language,
                parent_dataset_place_id=parents.get(key),
            )
            for key, value in places.items()
        }
    aliases = (
        _parse_aliases(directory, by_role[ROLE_ALIASES].relative_path, places)
        if ROLE_ALIASES in by_role
        else {}
    )
    province_index = (
        _parse_province_index(directory, by_role[ROLE_INDEX].relative_path)
        if ROLE_INDEX in by_role
        else {}
    )
    if province_index and (
        province_index["provider"] != validated["source"]["provider"]
        or province_index["source_snapshot"] != validated["source"]["snapshot"]
    ):
        raise _invalid("the INDEX does not match the dataset source snapshot")
    if province_index:
        unknown_cities = sorted(set(province_index["city_to_admin1"]) - set(places))
        if unknown_cities:
            raise _invalid("the INDEX references an unknown city", dataset_place_id=unknown_cities[0])
    declared_records = int(validated["coverage"]["record_count"])
    if declared_records != len(places):
        raise _invalid(
            "coverage.record_count does not match the places file",
            declared=declared_records,
            actual=len(places),
        )
    from photo_search.geo import cn_admin

    admin_content = cn_admin.load(directory, files)
    if admin_content is not None and int(supported_schema_version) < 18:
        raise _invalid("CN administrative facets require catalog schema 18")
    content = DatasetContent(
        dataset_id=validated["dataset_id"],
        package_id=validated["package_id"],
        package_version=validated["package_version"],
        provider=validated["source"]["provider"],
        state_on_disk=validated["state"],
        manifest=validated,
        files=files,
        places=places,
        aliases=aliases,
        province_index=province_index,
        cn_admin=admin_content,
    )
    from dataclasses import replace

    return replace(content, cn_legacy_links=cn_admin.legacy_links(content))


def haversine_m(latitude_a: float, longitude_a: float, latitude_b: float, longitude_b: float) -> float:
    """Great-circle distance in metres; the only distance the v1 resolver claims."""
    phi_a = math.radians(float(latitude_a))
    phi_b = math.radians(float(latitude_b))
    delta_phi = phi_b - phi_a
    delta_lambda = math.radians(float(longitude_b) - float(longitude_a))
    term = (
        math.sin(delta_phi / 2.0) ** 2
        + math.cos(phi_a) * math.cos(phi_b) * math.sin(delta_lambda / 2.0) ** 2
    )
    return 2.0 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(term)))


def nearby_cities(
    content: DatasetContent,
    latitude: float,
    longitude: float,
    *,
    max_candidates: int,
    max_distance_m: float,
) -> list[dict[str, Any]]:
    """The ranked ``SUGGESTION_ONLY`` city candidates of one coordinate.

    Ordering is ``(distance, dataset_place_id)``, so the same package always produces the
    same ranking. A point with no city inside ``max_distance_m`` (a sea position, say)
    returns an empty list, and that is a successful empty result, not an error.
    """
    ranked: list[tuple[float, DatasetPlace]] = []
    for place in content.city_places:
        distance = haversine_m(latitude, longitude, place.latitude, place.longitude)
        if distance <= float(max_distance_m):
            ranked.append((distance, place))
    ranked.sort(key=lambda pair: (pair[0], pair[1].dataset_place_id))
    candidates: list[dict[str, Any]] = []
    for rank, (distance, place) in enumerate(ranked[: int(max_candidates)], start=1):
        candidates.append(
            {
                "candidate_id": f"{content.dataset_id}:{place.dataset_place_id}",
                "dataset_place_id": place.dataset_place_id,
                "kind": "city",
                "display_name": place.display_name,
                "ancestor_names": content.ancestors(place.dataset_place_id),
                "rank": rank,
                "distance_m": round(float(distance), 3),
                "match_basis": "NEARBY_CITY_POINT",
                "confidence_basis": "DISTANCE_ONLY",
                "disposition": "SUGGESTION_ONLY",
            }
        )
    return candidates


# ------------------------------------------------------------------------- the builder
SPEC_KEYS = frozenset(
    {
        "package_id",
        "package_version",
        "dataset_id",
        "source",
        "build",
        "coverage",
        "compatible_catalog_schema",
        "state",
        "previous_dataset_id",
        "places",
        "aliases",
        "license_text",
        "index",
    }
)

#: A user editing the spec may only name these keys inside one place record.
SPEC_PLACE_KEYS = frozenset(
    {
        "dataset_place_id",
        "kind",
        "display_name",
        "country_code",
        "latitude",
        "longitude",
        "population",
        "language",
        "parent_dataset_place_id",
    }
)


def build_package(spec: Mapping[str, Any], destination: str | Path) -> dict[str, Any]:
    """Deterministically build a package directory from one JSON specification.

    The explicit local preparation tool wraps this function. It only reads the spec and
    writes the destination directory: no network, no photo, no catalog access. The content
    files are written first, their checksums are computed, and only then is the manifest
    written, so a half-built package can never look complete.
    """
    raw = _closed_object(spec, SPEC_KEYS, where="spec")
    directory = Path(destination)
    directory.mkdir(parents=True, exist_ok=True)
    places_rows: list[dict[str, Any]] = []
    hierarchy_rows: list[dict[str, Any]] = []
    for index, row in enumerate(raw["places"]):
        if not isinstance(row, Mapping):
            raise _invalid(f"places[{index}] must be an object")
        unexpected = sorted(set(row) - SPEC_PLACE_KEYS)
        if unexpected:
            raise _invalid(f"places[{index}] carries unknown fields", fields=unexpected)
        place: dict[str, Any] = {
            "dataset_place_id": _text(
                row.get("dataset_place_id"), where=f"places[{index}].dataset_place_id"
            ),
            "kind": _text(row.get("kind"), where=f"places[{index}].kind"),
            "display_name": _text(row.get("display_name"), where=f"places[{index}].display_name"),
        }
        if place["kind"] not in PLACE_RECORD_KINDS:
            raise _invalid(f"places[{index}].kind must be one of {list(PLACE_RECORD_KINDS)}")
        for key in ("country_code", "language"):
            if row.get(key) is not None:
                place[key] = _text(row[key], where=f"places[{index}].{key}")
        for key, low, high in (("latitude", -90.0, 90.0), ("longitude", -180.0, 180.0)):
            place[key] = _number(row.get(key), where=f"places[{index}].{key}", low=low, high=high)
        if row.get("population") is not None:
            place["population"] = _integer(row["population"], where=f"places[{index}].population")
        parent = row.get("parent_dataset_place_id")
        if parent is not None:
            hierarchy_rows.append(
                {
                    "child": str(place["dataset_place_id"]),
                    "parent": _text(parent, where=f"places[{index}].parent_dataset_place_id"),
                }
            )
        places_rows.append(place)
    aliases_rows: list[dict[str, Any]] = []
    for index, row in enumerate(raw["aliases"]):
        if not isinstance(row, Mapping):
            raise _invalid(f"aliases[{index}] must be an object")
        entry = {
            "dataset_place_id": _text(
                row.get("dataset_place_id"), where=f"aliases[{index}].dataset_place_id"
            ),
            "text": _text(row.get("text"), where=f"aliases[{index}].text"),
        }
        if row.get("language") is not None:
            entry["language"] = _text(row["language"], where=f"aliases[{index}].language")
        aliases_rows.append(entry)
    content: dict[str, tuple[str, str, str]] = {
        "places.jsonl": (
            "application/x-ndjson",
            ROLE_PLACES,
            "".join(canonical_json(row) + "\n" for row in places_rows),
        ),
        "LICENSE.txt": ("text/plain", ROLE_LICENSE, str(raw["license_text"])),
    }
    if aliases_rows:
        content["aliases.jsonl"] = (
            "application/x-ndjson",
            ROLE_ALIASES,
            "".join(canonical_json(row) + "\n" for row in aliases_rows),
        )
    if hierarchy_rows:
        content["hierarchy.jsonl"] = (
            "application/x-ndjson",
            ROLE_HIERARCHY,
            "".join(canonical_json(row) + "\n" for row in hierarchy_rows),
        )
    if raw.get("index") is not None:
        content["index.json"] = (
            "application/json",
            ROLE_INDEX,
            canonical_json(dict(raw["index"])) + "\n",
        )
    files: list[dict[str, Any]] = []
    for name in sorted(content):
        media_type, role, text = content[name]
        path = directory / name
        path.write_text(text, encoding="utf-8", newline="\n")
        data = path.read_bytes()
        files.append(
            {
                "relative_path": name,
                "role": role,
                "media_type": media_type,
                "sha256": hashlib.sha256(data).hexdigest(),
                "byte_size": len(data),
            }
        )
    declared = [DatasetFile(**item) for item in files]
    coverage = dict(raw["coverage"])
    coverage["record_count"] = len(places_rows)
    build = dict(raw["build"])
    # The builder always re-derives the parameter digest so a hand-written spec cannot
    # declare parameters that disagree with the digest it ships.
    build["parameters_sha256"] = hashlib.sha256(
        canonical_json(dict(build["parameters"])).encode("utf-8")
    ).hexdigest()
    manifest = {
        "contract_version": CONTRACT_VERSION,
        "manifest_schema": MANIFEST_SCHEMA,
        "package_id": _text(raw["package_id"], where="package_id"),
        "package_version": _text(raw["package_version"], where="package_version"),
        "dataset_id": _text(raw["dataset_id"], where="dataset_id"),
        "source": dict(raw["source"]),
        "build": build,
        "content_sha256": declared_content_sha256(declared),
        "coordinate_reference_system": "WGS84",
        "coverage": coverage,
        "compatible_catalog_schema": dict(raw["compatible_catalog_schema"]),
        "files": files,
        "state": str(raw["state"]),
        "previous_dataset_id": raw["previous_dataset_id"],
        "validated_at": None,
        "activated_at": None,
        "retained_at": None,
    }
    (directory / MANIFEST_FILENAME).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return manifest
