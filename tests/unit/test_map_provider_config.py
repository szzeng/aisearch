from __future__ import annotations

import json
import os

import pytest

from photo_search.map_provider_config import MapProviderConfigError, read, write


def test_map_provider_config_persists_replaces_and_clears_key(tmp_path):
    root = tmp_path / "data"
    assert read(root)["key"] == ""
    saved = write(root, key="fake-tianditu-key-001", default_layer="imagery")
    assert saved["key"] == "fake-tianditu-key-001"
    assert read(root) == saved
    path = root / "settings" / "map-provider.json"
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600
    write(root, clear_key=True)
    assert read(root)["key"] == ""
    assert read(root)["default_layer"] == "imagery"


@pytest.mark.parametrize("value", ["", " leading", "trailing ", "has\nline", "a\tb", "x" * 513])
def test_map_provider_config_rejects_invalid_key_without_replacing_old_value(tmp_path, value):
    root = tmp_path / "data"
    write(root, key="preserve-me")
    with pytest.raises(MapProviderConfigError):
        write(root, key=value)
    assert read(root)["key"] == "preserve-me"


def test_map_provider_config_corruption_is_visible_and_not_overwritten(tmp_path):
    root = tmp_path / "data"
    path = root / "settings" / "map-provider.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"key":"unreadable', encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(MapProviderConfigError):
        read(root)
    with pytest.raises(MapProviderConfigError):
        write(root, key="replacement")
    assert path.read_bytes() == before


def test_map_provider_config_write_failure_preserves_existing_file(monkeypatch, tmp_path):
    import photo_search.map_provider_config as provider

    root = tmp_path / "data"
    write(root, key="old-key")
    original = (root / "settings" / "map-provider.json").read_bytes()

    def fail_replace(*_args, **_kwargs):
        raise OSError("controlled replace failure")

    monkeypatch.setattr(provider.os, "replace", fail_replace)
    with pytest.raises(OSError):
        write(root, key="new-key")
    assert (root / "settings" / "map-provider.json").read_bytes() == original
    assert not list((root / "settings").glob(".map-provider-*.tmp"))


def test_map_provider_config_rejects_symlinked_settings_file(tmp_path):
    root = tmp_path / "data"
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps({"schema_version": 1, "provider": "tianditu", "key": "fake", "default_layer": "vector"}), encoding="utf-8")
    (root / "settings").mkdir(parents=True)
    try:
        (root / "settings" / "map-provider.json").symlink_to(outside)
    except OSError as exc:
        if os.name == "nt" and getattr(exc, "winerror", None) == 1314:
            pytest.skip("Windows symlink privilege is unavailable in this environment")
        raise
    with pytest.raises(MapProviderConfigError):
        read(root)
