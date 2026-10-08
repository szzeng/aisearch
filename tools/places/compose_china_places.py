"""Create an immutable GeoNames + CN addon package from local verified inputs."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

from photo_search.catalog.schema import SUPPORTED_SCHEMA_VERSION
from photo_search.geo import cn_admin, dataset


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def compose(base: Path, prepared: Path, destination: Path) -> dict:
    content = dataset.load_content(base, supported_schema_version=SUPPORTED_SCHEMA_VERSION)
    if content.cn_admin is not None:
        raise ValueError("base must be the original GeoNames package, not another composite")
    report = json.loads((prepared / "source-report.json").read_text(encoding="utf-8"))
    if not all(sample["passed"] for sample in report["samples"]) or len(report["samples"]) != 4:
        raise ValueError("the actual four-region source gate has not passed")
    if not report.get("upstream_md5_verified"):
        raise ValueError("verify the locally supplied published MD5 before composition")
    data = prepared / "cn-admin.jsonl.gz"
    if (file_sha256(data), data.stat().st_size) != (
        report["data_sha256"],
        report["data_byte_size"],
    ):
        raise ValueError("prepared data no longer matches its report")
    destination.mkdir(parents=True, exist_ok=False)
    manifest = json.loads(json.dumps(dict(content.manifest)))
    # Preserve every original content byte and the original source/INDEX provenance.
    for item in content.files:
        target = destination / item.relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(base / item.relative_path, target)
    addon = destination / "cn-admin"
    addon.mkdir()
    for name in ("cn-admin.jsonl.gz", "source-report.json", "ODbL.txt"):
        shutil.copyfile(prepared / name, addon / name)
    addon_manifest = {
        "schema": cn_admin.SCHEMA,
        "contract_version": cn_admin.CONTRACT,
        "crs": "WGS84",
        "data": "cn-admin/cn-admin.jsonl.gz",
        "report": "cn-admin/source-report.json",
        "license": "cn-admin/ODbL.txt",
    }
    (destination / cn_admin.MANIFEST).write_text(
        json.dumps(addon_manifest, indent=2) + "\n", encoding="utf-8"
    )
    for path in sorted(addon.iterdir()):
        manifest["files"].append(
            {
                "relative_path": path.relative_to(destination).as_posix(),
                "role": "LICENSE" if path.suffix == ".txt" else "INDEX",
                "media_type": "application/gzip"
                if path.suffix == ".gz"
                else "text/plain"
                if path.suffix == ".txt"
                else "application/json",
                "sha256": file_sha256(path),
                "byte_size": path.stat().st_size,
            }
        )
    fingerprint = report["source_sha256"][:8] + report["data_sha256"][:8]
    manifest.update(
        dataset_id=f"{content.dataset_id}-cn-{fingerprint}",
        package_id=f"{content.package_id}-cn-admin",
        package_version=f"{content.package_version}+cn-admin-v1-{fingerprint}",
        previous_dataset_id=content.dataset_id,
        state="STAGED",
        compatible_catalog_schema={"minimum": 18, "maximum": 18},
        validated_at=None,
        activated_at=None,
        retained_at=None,
    )
    manifest["content_sha256"] = dataset.declared_content_sha256(
        [dataset.DatasetFile(**item) for item in manifest["files"]]
    )
    (destination / dataset.MANIFEST_FILENAME).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    verified = dataset.load_content(destination, supported_schema_version=SUPPORTED_SCHEMA_VERSION)
    return {
        "dataset_id": verified.dataset_id,
        "base_records": len(verified.places),
        "admin_records": len(verified.cn_admin.rows),
        "legacy_links": len(verified.cn_legacy_links),
        "byte_size": sum(item.byte_size for item in verified.files),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-package", type=Path, required=True)
    parser.add_argument("--prepared", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(compose(args.base_package, args.prepared, args.output), ensure_ascii=False))


if __name__ == "__main__":
    main()
