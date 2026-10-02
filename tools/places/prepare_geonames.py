"""P4-02Y-P: turn local GeoNames dumps into a PhotoSearch place-dataset spec.

The application never downloads a dataset (ADR-0005 §8). This explicit local tool reads
raw GeoNames dump files that were downloaded by hand, keeps only the agreed set of Asian
countries/territories, applies the project naming priority (简体中文 → 其他中文 →
GeoNames 主名称 → 英文) and writes the plain-JSON **specification** that the existing
builder already owns:

    tools/places/prepare_geonames.py --raw-dir <dir> --spec-out <file>
    tools/places/manage_dataset.py build --spec <file> --out <package dir>
    tools/places/manage_dataset.py import --source <package dir> --activate

It reads only the raw directory it is given and writes only the spec file (and, with
``--package-out``, one package directory). It never touches the network, a photo, a source
root or the catalog, and it does not change the package format or the resolver.

Inputs (GeoNames "dump" export):

* ``cities500.txt``   - populated places (population ≥ 500 or a capital)
* ``alternateNamesV2.txt`` - language-tagged alternate names
* ``countryInfo.txt`` - country codes, English names and the country geonameid
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from photo_search.catalog import schema as schema_module
from photo_search.geo import dataset as dataset_module

#: The first-version scope of the personal Asian package (P4-02Y-P §1).
DEFAULT_COUNTRIES: tuple[str, ...] = (
    "CN",
    "HK",
    "MO",
    "TW",
    "JP",
    "KR",
    "MN",
    "SG",
    "MY",
    "TH",
    "VN",
    "PH",
    "ID",
    "KH",
    "LA",
    "MM",
    "BN",
    "IN",
    "NP",
    "LK",
    "BD",
    "PK",
    "BT",
    "MV",
)

#: ``zh`` spellings that mean "simplified Chinese" in the GeoNames language tags.
SIMPLIFIED_LANGUAGES = frozenset({"zh-hans", "zh-cn", "zh-sg", "zh-my"})
#: Every other language tag that counts as Chinese.
CHINESE_PREFIX = "zh"
ENGLISH_LANGUAGE = "en"

#: Characters that only exist in traditional Chinese, so a name carrying one is demoted
#: *inside* the Chinese class - never below a Latin name: the card's priority keeps any
#: available Chinese name above the GeoNames main name. GeoNames tags many names with the
#: generic ``zh`` whatever script they use (``马累``/``馬累``), which is exactly the case
#: this table resolves.
TRADITIONAL_ONLY = frozenset(
    "馬瑪內滿倫爾灣與學國龍東車門長陽電說話語讓隊際難雲頭題顏風飛飯館讀寫樂"
    "個們這來時對開關見現點無會產業傳統鐵銀錢陸險隨雙靈韓顯願類島嶼縣鎮鄉區"
    "號樓廣場園醫蘭羅維"
)

#: What ``AlternateNames.ranked`` returns per name, lower is better:
#: ``(script, historic, prefix_penalty, length, preferred_penalty, tag_rank, text, language)``.
#: ``script`` is 0 for a Chinese name written in simplified script, 1 for a Chinese name
#: carrying a traditional-only character and 2 for everything else, so any Chinese name
#: outranks any Latin one; ``tag_rank`` then prefers a name the dump itself tagged as
#: simplified Chinese.
RankedName = tuple[int, int, int, int, int, int, str, str]

SOURCE_URL = "https://download.geonames.org/export/dump/"
LICENSE_ID = "CC-BY-4.0"
LICENSE_URL = "https://creativecommons.org/licenses/by/4.0/"
ATTRIBUTION = (
    "Contains data from GeoNames (https://www.geonames.org/), licensed CC BY 4.0. "
    "All GeoNames data is available under the Creative Commons Attribution 4.0 License."
)
LICENSE_TEXT = f"""GeoNames place data

{ATTRIBUTION}

Snapshot files: cities500, alternateNamesV2 and countryInfo, downloaded from
{SOURCE_URL} and converted locally for offline use. This package contains a filtered
subset (selected Asian countries/territories) and must not be redistributed as a
full GeoNames export.
"""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _open_text(path: Path) -> Any:
    return path.open("r", encoding="utf-8", errors="replace", newline="\n")


def _snapshot_stamp(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).strftime("%Y-%m-%d")


def _tag_rank(language: str) -> int:
    """0 = the dump says "simplified Chinese", 1 = any other Japanese/Chinese-ish tag."""
    return 0 if language.strip().lower() in SIMPLIFIED_LANGUAGES else 1


def _has_cjk(text: str) -> bool:
    return any("\u3400" <= character <= "\u9fff" for character in text)


def _script_class(text: str) -> int:
    """0 = Chinese in simplified script, 1 = Chinese with a traditional-only character, 2 = other."""
    if not _has_cjk(text):
        return 2
    return 1 if _looks_traditional(text) else 0


def _looks_traditional(text: str) -> bool:
    return any(character in TRADITIONAL_ONLY for character in text)


class AlternateNames:
    """The language-tagged alternate names of the selected GeoNames ids."""

    def __init__(self) -> None:
        self._names: dict[int, list[tuple[str, bool, bool, str]]] = {}

    def add(
        self,
        geonameid: int,
        language: str,
        text: str,
        *,
        preferred: bool,
        historic: bool = False,
    ) -> None:
        cleaned = " ".join(str(text).split())
        if not cleaned:
            return
        self._names.setdefault(int(geonameid), []).append(
            (str(language), bool(preferred), bool(historic), cleaned)
        )

    def ranked(self, geonameid: int) -> list[RankedName]:
        """The place's names, best first, de-duplicated by text.

        Three traps of the raw dump are handled here, all of them seen in the real data:

        * the *same* text may appear under several language tags (``新加坡`` as ``zh`` and as
          the preferred ``zh-CN``), so de-duplication keeps the best-tagged copy instead of
          the first one seen;
        * a name may be romanized Chinese (``Dānmǎxī``) or a historical form (``Batavia``,
          ``天府``), so the class is decided by the *text*, and the shorter base form of the
          same place (``成都`` between ``成都市``) wins over an unrelated equal-length one;
        * traditional spellings tagged as plain ``zh`` (``馬累``/``马累``) are demoted inside
          the Chinese class.
        """
        rows = self._names.get(int(geonameid), [])
        texts = {text for _language, _preferred, _historic, text in rows}
        best: dict[str, RankedName] = {}
        for language, preferred, historic, text in rows:
            entry: RankedName = (
                _script_class(text),
                1 if historic else 0,
                0 if any(other.startswith(text) for other in texts if other != text) else 1,
                len(text),
                0 if preferred else 1,
                _tag_rank(language),
                text,
                str(language),
            )
            current = best.get(text)
            if current is None or entry < current:
                best[text] = entry
        return sorted(best.values())

    def __len__(self) -> int:
        return len(self._names)


def read_country_info(path: Path, countries: Sequence[str]) -> dict[str, dict[str, Any]]:
    """``ISO2 -> {name, geonameid}`` for the requested countries only."""
    wanted = {str(code).upper() for code in countries}
    found: dict[str, dict[str, Any]] = {}
    with _open_text(path) as stream:
        for line in stream:
            if not line or line.startswith("#"):
                continue
            fields = line.rstrip("\n").split("\t")
            if len(fields) < 17:
                continue
            code = fields[0].strip().upper()
            if code not in wanted:
                continue
            found[code] = {
                "name": fields[4].strip(),
                "geonameid": int(fields[16]) if fields[16].strip().isdigit() else None,
            }
    return found


def read_cities(path: Path, countries: Sequence[str]) -> list[dict[str, Any]]:
    """Every ``cities500`` row of the requested countries, as plain records."""
    wanted = {str(code).upper() for code in countries}
    cities: list[dict[str, Any]] = []
    with _open_text(path) as stream:
        for line in stream:
            if not line:
                continue
            fields = line.rstrip("\n").split("\t")
            if len(fields) < 15:
                continue
            code = fields[8].strip().upper()
            if code not in wanted:
                continue
            latitude = fields[4].strip()
            longitude = fields[5].strip()
            if not latitude or not longitude:
                continue
            population = fields[14].strip()
            cities.append(
                {
                    "geonameid": int(fields[0]),
                    "name": fields[1].strip(),
                    "asciiname": fields[2].strip(),
                    "latitude": float(latitude),
                    "longitude": float(longitude),
                    "country_code": code,
                    "population": int(population) if population.isdigit() else 0,
                    "admin1_code": fields[10].strip(),
                    "feature_code": fields[7].strip(),
                }
            )
    return cities


def read_admin1(path: Path, codes: Iterable[str]) -> dict[str, dict[str, Any]]:
    wanted = set(codes)
    found: dict[str, dict[str, Any]] = {}
    with _open_text(path) as stream:
        for line in stream:
            fields = line.rstrip("\n").split("\t")
            if len(fields) < 4 or fields[0] not in wanted:
                continue
            found[fields[0]] = {"id": fields[0], "name": fields[1].strip(), "geonameid": int(fields[3]) if fields[3].isdigit() else None}
    return found


def collect_alternate_names(
    path: Path, wanted_ids: Iterable[int], languages: Sequence[str] | None = None
) -> AlternateNames:
    """Stream ``alternateNamesV2`` and keep the names of the selected ids.

    ``languages`` (when given) keeps only the exact tags listed. The default keeps the
    Chinese and English tags, because those are the only ones this package can place in its
    naming priority and its alias list - everything else is dropped while streaming, which
    is also what keeps the memory footprint of a 12-million-line dump bounded.
    """
    wanted = {int(value) for value in wanted_ids}
    keep = {str(v).lower() for v in languages} if languages is not None else None
    names = AlternateNames()
    with _open_text(path) as stream:
        for line in stream:
            if not line:
                continue
            # Fields: 0 id, 1 geonameid, 2 language, 3 name, 4 preferred, 5 short,
            # 6 colloquial, 7 historic, 8 from, 9 to. The split must isolate field 7: a
            # historical entry carries "1<TAB>1778<TAB>1924" after it, so a smaller maxsplit
            # would hand back the whole remainder and never see the flag.
            fields = line.rstrip("\n").split("\t", 8)
            if len(fields) < 5:
                continue
            geonameid = fields[1].strip()
            if not geonameid.isdigit():
                continue
            identifier = int(geonameid)
            if identifier not in wanted:
                continue
            language = fields[2].strip()
            lowered = language.lower()
            if keep is None:
                if not (lowered.startswith(CHINESE_PREFIX) or lowered == ENGLISH_LANGUAGE):
                    continue
            elif lowered not in keep:
                continue
            names.add(
                identifier,
                language,
                fields[3],
                preferred=fields[4].strip() == "1",
                historic=len(fields) > 7 and fields[7].strip() == "1",
            )
    return names


def _aliases_for(
    primary: str,
    ascii_name: str,
    *,
    display_name: str,
    ranked: Sequence[RankedName],
    max_aliases: int,
) -> list[dict[str, str]]:
    """The searchable aliases of one place, Chinese names first."""
    # Chinese names first, then the Latin spellings (both the English alternates and the
    # romanized Chinese ones), so the alias list is useful for search in either script.
    rows: list[RankedName] = [row for row in ranked if row[0] <= 2]
    for fallback in (primary, ascii_name):
        if fallback:
            # The main/ASCII name is a last-resort alias: it never outranks a tagged name.
            rows.append((2, 0, 1, len(fallback), 1, 2, fallback, ""))
    ordered: list[dict[str, str]] = []
    seen = {display_name}
    # A language-tagged copy of the same text is preferred over the untagged fallback.
    for _script, _historic, _prefix, _length, _preferred, _tag, text, language in sorted(
        rows,
        key=lambda row: (
            row[0],
            row[1],
            row[2],
            row[3],
            row[4],
            row[5],
            0 if row[7] else 1,
            row[6],
        ),
    ):
        if text in seen:
            continue
        seen.add(text)
        entry = {"text": text}
        if language:
            entry["language"] = language
        ordered.append(entry)
        if len(ordered) >= int(max_aliases):
            break
    return ordered


def build_spec(
    raw_dir: Path,
    *,
    countries: Sequence[str] = DEFAULT_COUNTRIES,
    max_aliases_per_place: int = 10,
    dataset_id: str = "geonames-asia-cities500-v1",
    package_id: str = "photo-search-geonames-asia-cities500-package",
    snapshot: str | None = None,
) -> dict[str, Any]:
    """Read the raw dumps and return the builder spec (no file is written)."""
    raw_dir = Path(raw_dir)
    cities_path = raw_dir / "cities500.txt"
    alternate_path = raw_dir / "alternateNamesV2.txt"
    country_path = raw_dir / "countryInfo.txt"
    for path in (cities_path, alternate_path, country_path):
        if not path.is_file():
            raise FileNotFoundError(f"missing GeoNames dump file: {path}")

    country_rows = read_country_info(country_path, countries)
    missing = sorted({str(code).upper() for code in countries} - set(country_rows))
    if missing:
        raise ValueError(f"countryInfo.txt has no rows for {missing}")
    cities = read_cities(cities_path, countries)
    if not cities:
        raise ValueError("cities500.txt produced no city inside the requested countries")

    wanted_ids = {int(row["geonameid"]) for row in cities}
    admin_path = raw_dir / "admin1CodesASCII.txt"
    admin_rows = read_admin1(admin_path, {f"{row['country_code']}.{row['admin1_code']}" for row in cities if row["admin1_code"]}) if admin_path.is_file() else {}
    wanted_ids.update(int(row["geonameid"]) for row in admin_rows.values() if row["geonameid"] is not None)
    wanted_ids.update(
        int(row["geonameid"]) for row in country_rows.values() if row["geonameid"] is not None
    )
    names = collect_alternate_names(alternate_path, wanted_ids)

    places: list[dict[str, Any]] = []
    aliases: list[dict[str, Any]] = []
    for code in sorted(country_rows):
        row = country_rows[code]
        geonameid = row["geonameid"]
        ranked = [] if geonameid is None else names.ranked(int(geonameid))
        display = _country_display_name(row["name"], ranked)
        place_id = f"geonames-country-{code.lower()}"
        places.append(
            {
                "dataset_place_id": place_id,
                "kind": "country",
                "display_name": display,
                "country_code": code,
                "latitude": _country_centroid(cities, code)[0],
                "longitude": _country_centroid(cities, code)[1],
            }
        )
        for alias in _aliases_for(
            str(row["name"]),
            "",
            display_name=display,
            ranked=ranked,
            max_aliases=max_aliases_per_place,
        ):
            aliases.append({"dataset_place_id": place_id, **alias})

    ordered_cities = sorted(
        cities, key=lambda row: (row["country_code"], -int(row["population"]), row["geonameid"])
    )
    for row in ordered_cities:
        geonameid = int(row["geonameid"])
        ranked = names.ranked(geonameid)
        display = _city_display_name(row, ranked)
        place_id = f"geonames-city-{geonameid}"
        places.append(
            {
                "dataset_place_id": place_id,
                "kind": "city",
                "display_name": display,
                "country_code": row["country_code"],
                "latitude": float(row["latitude"]),
                "longitude": float(row["longitude"]),
                "population": int(row["population"]),
                "parent_dataset_place_id": f"geonames-country-{row['country_code'].lower()}",
            }
        )
        for alias in _aliases_for(
            str(row["name"]),
            str(row["asciiname"]),
            display_name=display,
            ranked=ranked,
            max_aliases=max_aliases_per_place,
        ):
            aliases.append({"dataset_place_id": place_id, **alias})

    latitudes = [float(row["latitude"]) for row in cities]
    longitudes = [float(row["longitude"]) for row in cities]
    source_names = ["cities500.txt", "alternateNamesV2.txt", "countryInfo.txt"]
    if admin_path.is_file():
        source_names.append("admin1CodesASCII.txt")
    source_files = [
        f"{name} sha256={_sha256_file(raw_dir / name)} bytes={(raw_dir / name).stat().st_size}"
        for name in source_names
    ]
    # The dump's own *date* is what identifies the snapshot; a mirror (or a later unzip)
    # leaves today's mtime on the files, so the caller may name the snapshot it knows.
    resolved_snapshot = (
        str(snapshot)
        if snapshot
        else min(
            _snapshot_stamp(cities_path),
            _snapshot_stamp(alternate_path),
            _snapshot_stamp(country_path),
            *([_snapshot_stamp(admin_path)] if admin_path.is_file() else []),
        )
    )
    province_index = None
    if admin_rows:
        province_index = {
            "schema_version": 1,
            "provider": "GeoNames",
            "source_snapshot": resolved_snapshot,
            "city_to_admin1": {
                f"geonames-city-{row['geonameid']}": {
                    "id": admin_rows[f"{row['country_code']}.{row['admin1_code']}"]["id"],
                    "name": admin_rows[f"{row['country_code']}.{row['admin1_code']}"]["name"],
                    "aliases": [item[6] for item in names.ranked(int(admin_rows[f"{row['country_code']}.{row['admin1_code']}"]["geonameid"])) if item[0] < 2][:10],
                }
                for row in cities
                if row["admin1_code"] and f"{row['country_code']}.{row['admin1_code']}" in admin_rows
            },
        }
    return {
        "package_id": package_id,
        "package_version": "1",
        "dataset_id": dataset_id,
        "source": {
            "provider": "GeoNames",
            "source_url": SOURCE_URL,
            "snapshot": resolved_snapshot,
            "license_id": LICENSE_ID,
            "license_url": LICENSE_URL,
            "attribution": ATTRIBUTION,
        },
        "build": {
            "builder_id": "photo-search-geonames-converter",
            "builder_version": "1",
            "generated_at": int(datetime.now(tz=UTC).timestamp() * 1000),
            "parameters_sha256": "0" * 64,
            "parameters": {
                "source_files": source_files,
                # cities500 (population ≥ 500 or a capital) is the source filter, so this
                # build adds no further population threshold of its own.
                "minimum_population": 0,
                "languages": ["zh"],
                "include_aliases": True,
                "sort_order": "country,population-desc,geonameid",
            },
        },
        "coverage": {
            "country_codes": sorted(country_rows),
            "bounds_wgs84": {
                "south": min(latitudes),
                "west": min(longitudes),
                "north": max(latitudes),
                "east": max(longitudes),
            },
            "languages": ["zh"],
            "alias_languages": ["zh", "en"],
            "record_count": len(places),
        },
        "compatible_catalog_schema": {
            "minimum": 5,
            "maximum": int(schema_module.SUPPORTED_SCHEMA_VERSION),
        },
        "state": "STAGED",
        "previous_dataset_id": None,
        "places": places,
        "aliases": aliases,
        "license_text": LICENSE_TEXT,
        "index": province_index,
    }


def _country_centroid(cities: Sequence[Mapping[str, Any]], code: str) -> tuple[float, float]:
    rows = [row for row in cities if str(row["country_code"]) == code]
    if not rows:
        return (0.0, 0.0)
    # The country row keeps a bounded, honest point: the population-weighted mean of its
    # cities. It is never used as a candidate (only ``city`` records are candidates).
    weight = sum(int(row["population"]) for row in rows)
    if weight <= 0:
        latitude = sum(float(row["latitude"]) for row in rows) / len(rows)
        longitude = sum(float(row["longitude"]) for row in rows) / len(rows)
        return (round(latitude, 5), round(longitude, 5))
    latitude = sum(float(row["latitude"]) * int(row["population"]) for row in rows) / weight
    longitude = sum(float(row["longitude"]) * int(row["population"]) for row in rows) / weight
    return (round(latitude, 5), round(longitude, 5))


def _city_display_name(row: Mapping[str, Any], ranked: Sequence[RankedName]) -> str:
    """简体中文 → 其他中文 → GeoNames 主名称 → 英文（→ ASCII 名）。"""
    chinese = _first_chinese(ranked)
    if chinese:
        return chinese
    primary = str(row.get("name") or "").strip()
    if primary:
        return primary
    latin = next((row[6] for row in ranked if row[0] == 2), None)
    if latin:
        return latin
    return str(row.get("asciiname") or "").strip() or f"geonames-{row['geonameid']}"


def _country_display_name(english_name: str, ranked: Sequence[RankedName]) -> str:
    return _first_chinese(ranked) or english_name


def _first_chinese(ranked: Sequence[RankedName]) -> str | None:
    """The best Chinese-script name: simplified-tagged first, any other Chinese second."""
    for row in ranked:
        if row[0] <= 1:
            return row[6]
    return None


def _write_spec(spec: Mapping[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(spec, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--raw-dir", type=Path, required=True, help="directory of the unzipped dumps"
    )
    parser.add_argument("--spec-out", type=Path, required=True, help="spec JSON to write")
    parser.add_argument(
        "--package-out",
        type=Path,
        default=None,
        help="also build the package directory with the existing builder",
    )
    parser.add_argument(
        "--countries",
        default=",".join(DEFAULT_COUNTRIES),
        help="comma-separated ISO2 codes (default: the P4-02Y-P Asian set)",
    )
    parser.add_argument("--max-aliases-per-place", type=int, default=10)
    parser.add_argument("--dataset-id", default="geonames-asia-cities500-v1")
    parser.add_argument("--package-id", default="photo-search-geonames-asia-cities500-package")
    parser.add_argument(
        "--snapshot",
        default=None,
        help="the dump date to record (default: the newest mtime of the raw files)",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    countries = tuple(
        code.strip().upper() for code in str(args.countries).split(",") if code.strip()
    )
    spec = build_spec(
        Path(args.raw_dir),
        countries=countries,
        max_aliases_per_place=int(args.max_aliases_per_place),
        dataset_id=str(args.dataset_id),
        package_id=str(args.package_id),
        snapshot=args.snapshot,
    )
    _write_spec(spec, Path(args.spec_out))
    summary: dict[str, Any] = {
        "status": "ok",
        "spec": str(Path(args.spec_out).resolve()),
        "dataset_id": spec["dataset_id"],
        "countries": len(spec["coverage"]["country_codes"]),
        "places": len(spec["places"]),
        "cities": sum(1 for row in spec["places"] if row["kind"] == "city"),
        "countries_with_cities": len(
            {row["country_code"] for row in spec["places"] if row["kind"] == "city"}
        ),
        "aliases": len(spec["aliases"]),
        "bounds_wgs84": spec["coverage"]["bounds_wgs84"],
        "snapshot": spec["source"]["snapshot"],
    }
    if args.package_out is not None:
        manifest = dataset_module.build_package(spec, Path(args.package_out))
        summary["package_dir"] = str(Path(args.package_out).resolve())
        summary["package_id"] = manifest["package_id"]
        summary["content_sha256"] = manifest["content_sha256"]
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover - explicit local entry point
    sys.exit(main())
