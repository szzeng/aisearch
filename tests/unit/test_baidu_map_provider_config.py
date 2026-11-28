from __future__ import annotations

import pytest

import photo_search.baidu_map_provider_config as baidu
from photo_search import map_provider_config as tianditu


def test_baidu_settings_are_separate_from_tianditu(tmp_path):
    root = tmp_path / "data"
    tianditu.write(root, key="old-map-key", default_layer="imagery")
    old = (root / "settings" / "map-provider.json").read_bytes()
    assert baidu.read(root) == {"schema_version": 1, "provider": "baidu", "ak": "", "default_layer": "normal"}
    baidu.write(root, ak="fake-ak", default_layer="hybrid")
    baidu.write(root, ak="new-fake-ak", default_layer="satellite")
    assert baidu.read(root)["ak"] == "new-fake-ak"
    baidu.write(root, clear_key=True)
    assert baidu.read(root)["ak"] == ""
    assert baidu.read(root)["default_layer"] == "satellite"
    assert (root / "settings" / "map-provider.json").read_bytes() == old


@pytest.mark.parametrize("value", ["", " space", "a\nb", "x" * 513])
def test_invalid_ak_does_not_replace_old_value(tmp_path, value):
    baidu.write(tmp_path, ak="fake-ak")
    with pytest.raises(baidu.MapProviderConfigError):
        baidu.write(tmp_path, ak=value)
    assert baidu.read(tmp_path)["ak"] == "fake-ak"


def test_corrupt_settings_are_not_overwritten(tmp_path):
    target = tmp_path / "settings" / "map-provider-baidu.json"
    target.parent.mkdir()
    target.write_text('{"ak":', encoding="utf-8")
    old = target.read_bytes()
    with pytest.raises(baidu.MapProviderConfigError):
        baidu.write(tmp_path, ak="fake-ak")
    assert target.read_bytes() == old


@pytest.mark.parametrize("value", [0, None, False, [], {}])
def test_wrong_ak_type_is_corrupt_and_preserved(tmp_path, value):
    import json

    target = tmp_path / "settings" / "map-provider-baidu.json"
    target.parent.mkdir()
    target.write_text(json.dumps({"schema_version": 1, "provider": "baidu", "ak": value,
                                  "default_layer": "normal"}), encoding="utf-8")
    old = target.read_bytes()
    with pytest.raises(baidu.MapProviderConfigError):
        baidu.write(tmp_path, ak="fake-ak")
    assert target.read_bytes() == old


def test_replace_failure_preserves_settings(tmp_path, monkeypatch):
    baidu.write(tmp_path, ak="old-fake")
    target = tmp_path / "settings" / "map-provider-baidu.json"
    old = target.read_bytes()
    def fail(*_args):
        raise OSError("controlled failure")
    monkeypatch.setattr(baidu.os, "replace", fail)
    with pytest.raises(OSError):
        baidu.write(tmp_path, ak="new-fake")
    assert target.read_bytes() == old
    assert not list(target.parent.glob(".map-provider-baidu-*.tmp"))
