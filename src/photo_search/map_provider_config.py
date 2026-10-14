"""Private, data-root scoped settings for the online map provider."""
from __future__ import annotations

import json
import os
import stat
import tempfile
import threading
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
LAYERS = {"vector", "imagery"}
MAX_KEY_LENGTH = 512
_WRITE_LOCK = threading.RLock()


class MapProviderConfigError(ValueError):
    """A map settings file is unsafe, invalid, or cannot be written."""


def _path(data_root: str | Path) -> Path:
    """Resolve only after rejecting reparse points in every existing component.

    The application holds its exclusive data-root lease while running. This protects the
    settings directory from another application writer; as with the catalog, an external
    process with write access to the user's data root is outside this file-level boundary.
    """
    root = Path(data_root).absolute()
    directory = root / "settings"
    target = directory / "map-provider.json"
    cursor = Path(root.anchor)
    for part in root.parts[1:]:
        cursor = cursor / part
        _reject_reparse(cursor)
    for path in (directory, target):
        _reject_reparse(path)
        try:
            info = path.lstat()
        except FileNotFoundError:
            continue
        if path == target and not stat.S_ISREG(info.st_mode):
            raise MapProviderConfigError("map settings must be a regular file")
        if path == directory and not stat.S_ISDIR(info.st_mode):
            raise MapProviderConfigError("settings path must be a directory")
    return target


def _reject_reparse(path: Path) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise MapProviderConfigError("map settings path must not be a reparse point")


def _validate_key(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > MAX_KEY_LENGTH:
        raise MapProviderConfigError("Key must be a non-empty string within the supported length")
    if value != value.strip() or any(ord(char) < 0x21 or ord(char) == 0x7f for char in value):
        raise MapProviderConfigError("Key must not contain spaces or control characters")
    return value


def read(data_root: str | Path) -> dict[str, Any]:
    target = _path(data_root)
    if not target.exists():
        return {"schema_version": SCHEMA_VERSION, "provider": "tianditu", "key": "", "default_layer": "vector"}
    try:
        body = json.loads(target.read_text(encoding="utf-8"))
        if (body.get("schema_version") != SCHEMA_VERSION or body.get("provider") != "tianditu"
                or body.get("default_layer") not in LAYERS):
            raise ValueError("unsupported settings values")
        key = _validate_key(body.get("key")) if body.get("key") else ""
        return {"schema_version": SCHEMA_VERSION, "provider": "tianditu", "key": key,
                "default_layer": body["default_layer"]}
    except (OSError, ValueError, TypeError, AttributeError, json.JSONDecodeError) as exc:
        raise MapProviderConfigError("map settings could not be read") from exc


def write(data_root: str | Path, *, key: str | None = None, default_layer: str | None = None,
          clear_key: bool = False) -> dict[str, Any]:
    # API requests may update the Key and layer preference independently. Keep their
    # read-modify-replace sequence together so an update cannot erase the other field.
    with _WRITE_LOCK:
        return _write_locked(data_root, key=key, default_layer=default_layer, clear_key=clear_key)


def _write_locked(data_root: str | Path, *, key: str | None = None, default_layer: str | None = None,
                  clear_key: bool = False) -> dict[str, Any]:
    current = read(data_root)
    if key is not None:
        current["key"] = _validate_key(key)
    elif clear_key:
        current["key"] = ""
    if default_layer is not None:
        if not isinstance(default_layer, str) or default_layer not in LAYERS:
            raise MapProviderConfigError("unsupported default map layer")
        current["default_layer"] = default_layer
    target = _path(data_root)
    target.parent.mkdir(parents=True, exist_ok=True)
    # Recheck after directory creation; reject a path swapped to a reparse point.
    target = _path(data_root)
    fd, temp_name = tempfile.mkstemp(prefix=".map-provider-", suffix=".tmp", dir=target.parent)
    temp = Path(temp_name)
    try:
        if os.name != "nt":
            os.chmod(temp, 0o600)
        # Windows uses the data-root's inherited DACL. A per-file DACL is intentionally
        # not changed here because this task's operator declined Windows ACL changes.
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(current, stream, ensure_ascii=False, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        _path(data_root)
        os.replace(temp, target)
    except Exception:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return current
