"""The data-root registry of installed semantic providers.

One model = one manifest + one data-root model directory. The registry reads both without
importing a framework, answers "which providers does this machine actually have?", and resolves a
``processor_id`` (the identity the catalog already persists) back to the model description that
provides it. Nothing here loads weights or chooses a device, and nothing writes: the registry is a
pure description of what is installed.

Resolution order is deliberately narrow:

* the data-root manifests (``<data_root>/models/manifests/*.json``) are the *installed*
  descriptions and win over the shipped templates;
* the shipped repository manifests are the fallback, so the historical Chinese-CLIP install keeps
  working with the exact identity it was published under;
* a manifest that cannot describe its provider (see :mod:`photo_search.models.embedding.base`) is
  skipped instead of being guessed at, and an unknown ``provider_id`` is a structured failure.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

from photo_search.catalog.errors import (
    EMBEDDING_PROVIDER_UNKNOWN,
    EMBEDDING_WEIGHTS_MISSING,
    CatalogError,
)
from photo_search.models.embedding.base import (
    CLIP_ADAPTER,
    Identity,
    LEGACY_PROVIDERS,
    analyze_manifest,
    model_dir_for,
    provider_block,
    provider_id_of_processor,
    read_manifest,
    required_artifact_names,
)

#: The data-root relative directories this registry knows about.
MODELS_DIRNAME = "models"
MANIFESTS_DIRNAME = "manifests"


def models_dir(data_root: str | Path) -> Path:
    """``<data_root>/models`` - every installed semantic model lives below this path."""
    return Path(data_root) / MODELS_DIRNAME


def model_manifests_dir(data_root: str | Path) -> Path:
    """``<data_root>/models/manifests`` - the installed model descriptions."""
    return models_dir(data_root) / MANIFESTS_DIRNAME


def shipped_manifest_dir() -> Path:
    """The repository manifests shipped next to the prepare tools (frozen, read-only)."""
    return Path(__file__).resolve().parents[4] / "tools" / "prepare_models" / "manifests"


@dataclass(frozen=True)
class ProviderEntry:
    """One installed (or at least described) semantic provider."""

    provider_id: str
    adapter: str
    display_name: str
    manifest_path: Path
    model_dir: Path
    device_policy: str
    identity: Identity
    missing_files: tuple[str, ...]
    source: str

    @property
    def installed(self) -> bool:
        return bool(self.manifest_path.is_file()) and not self.missing_files

    @property
    def reason(self) -> str:
        if not self.manifest_path.is_file():
            return "MANIFEST_MISSING"
        if self.missing_files:
            return "WEIGHTS_MISSING"
        if not self.model_dir.is_dir():
            return "MODEL_DIR_MISSING"
        return "READY"

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "adapter": self.adapter,
            "display_name": self.display_name,
            "dimension": self.identity.dimension,
            "processor_id": self.identity.processor_id,
            "space_id": self.identity.space_id,
            "model_id": self.identity.model_id,
            "model_revision": self.identity.model_revision,
            "device_policy": self.device_policy,
            "installed": bool(self.installed),
            "reason": self.reason,
            "model_dir": str(self.model_dir),
            "manifest": str(self.manifest_path),
            "missing_files": list(self.missing_files),
            "source": self.source,
        }


class EmbeddingRegistry:
    """The immutable set of providers one data root can build or serve."""

    def __init__(self, entries: Sequence[ProviderEntry], *, skipped: Sequence[str] = ()) -> None:
        self._entries = tuple(entries)
        self._by_id = {entry.provider_id: entry for entry in self._entries}
        #: Manifests that were present but could not describe a provider (reported, not used).
        self.skipped = tuple(str(item) for item in skipped)

    # ------------------------------------------------------------------- construction
    @classmethod
    def discover(
        cls,
        data_root: str | Path,
        *,
        extra_manifest_dirs: Iterable[str | Path] = (),
        include_shipped: bool = True,
    ) -> EmbeddingRegistry:
        root = Path(data_root)
        directories: list[tuple[Path, str]] = [(model_manifests_dir(root), "data_root")]
        for directory in extra_manifest_dirs:
            directories.append((Path(directory), "explicit"))
        if include_shipped:
            directories.append((shipped_manifest_dir(), "shipped"))
        seen: dict[str, ProviderEntry] = {}
        skipped: list[str] = []
        for directory, source in directories:
            for manifest_path in _manifest_files(directory):
                try:
                    entry = _entry_for(manifest_path, root, source=source)
                except Exception:  # noqa: BLE001 - an unusable manifest is reported, not used
                    skipped.append(str(manifest_path))
                    continue
                if entry.provider_id in seen:
                    continue
                seen[entry.provider_id] = entry
        return cls(sorted(seen.values(), key=lambda item: item.provider_id), skipped=skipped)

    # ------------------------------------------------------------------------ lookups
    def providers(self) -> tuple[ProviderEntry, ...]:
        return self._entries

    def find(self, provider_id: str) -> ProviderEntry | None:
        return self._by_id.get(str(provider_id))

    def get(self, provider_id: str) -> ProviderEntry:
        entry = self.find(provider_id)
        if entry is None:
            raise CatalogError(
                EMBEDDING_PROVIDER_UNKNOWN,
                "this data root has no such semantic provider installed",
                details={
                    "provider_id": str(provider_id),
                    "known": [item.provider_id for item in self._entries],
                },
            )
        return entry

    def for_processor(self, processor_id: str) -> ProviderEntry:
        """Resolve the identity the catalog persists back to its installed description."""
        entry = self.get(provider_id_of_processor(processor_id))
        if str(entry.identity.processor_id) != str(processor_id):
            raise CatalogError(
                EMBEDDING_PROVIDER_UNKNOWN,
                "the installed provider is not the exact revision this catalog references",
                details={
                    "requested_processor_id": str(processor_id),
                    "installed_processor_id": str(entry.identity.processor_id),
                    "provider_id": entry.provider_id,
                },
            )
        return entry

    def default(self) -> ProviderEntry | None:
        """The provider a *new* library uses when nothing is configured yet.

        That is the historical Chinese-CLIP install, because it is the one configuration the
        shipped manifests always describe and the only one an existing catalog can be serving.
        """
        for provider_id in LEGACY_PROVIDERS.values():
            entry = self.find(str(provider_id["provider_id"]))
            if entry is not None and str(entry.adapter) == CLIP_ADAPTER:
                return entry
        return None

    def require_installed(self, provider_id: str) -> ProviderEntry:
        """``get`` plus the structured "the weights are really here" refusal."""
        entry = self.get(provider_id)
        if not entry.manifest_path.is_file():
            raise CatalogError(
                EMBEDDING_WEIGHTS_MISSING,
                "the model manifest of this provider is not installed",
                details={"provider_id": entry.provider_id, "manifest": str(entry.manifest_path)},
            )
        if entry.missing_files:
            raise CatalogError(
                EMBEDDING_WEIGHTS_MISSING,
                "the model weights of this provider are not installed",
                details={
                    "provider_id": entry.provider_id,
                    "model_dir": str(entry.model_dir),
                    "missing_files": list(entry.missing_files),
                },
            )
        if not entry.model_dir.is_dir():
            raise CatalogError(
                EMBEDDING_WEIGHTS_MISSING,
                "the model directory of this provider does not exist",
                details={"provider_id": entry.provider_id, "model_dir": str(entry.model_dir)},
            )
        return entry

    def describe(self) -> dict[str, Any]:
        return {
            "providers": [entry.to_dict() for entry in self._entries],
            "skipped_manifests": list(self.skipped),
        }


def _manifest_files(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(path for path in directory.glob("*.json") if path.is_file())


def _entry_for(manifest_path: Path, data_root: Path, *, source: str) -> ProviderEntry:
    manifest = read_manifest(manifest_path)
    provider, identity = analyze_manifest(manifest)
    directory = model_dir_for(data_root, provider)
    missing = tuple(
        name for name in required_artifact_names(manifest) if not (directory / name).is_file()
    )
    return ProviderEntry(
        provider_id=str(provider["provider_id"]),
        adapter=str(provider["adapter"]),
        display_name=str(provider["display_name"]),
        manifest_path=manifest_path,
        model_dir=directory,
        device_policy=str(provider["device_policy"]),
        identity=identity,
        missing_files=missing,
        source=str(source),
    )


def registry_for(
    data_root: str | Path,
    *,
    extra_manifest_dirs: Iterable[str | Path] = (),
    include_shipped: bool = True,
) -> EmbeddingRegistry:
    """Convenience constructor used by the CLI, the startup entry and the refresh."""
    return EmbeddingRegistry.discover(
        data_root, extra_manifest_dirs=extra_manifest_dirs, include_shipped=include_shipped
    )


__all__ = [
    "MANIFESTS_DIRNAME",
    "MODELS_DIRNAME",
    "EmbeddingRegistry",
    "ProviderEntry",
    "model_manifests_dir",
    "models_dir",
    "registry_for",
    "shipped_manifest_dir",
]
