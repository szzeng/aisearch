from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path


def test_tianditu_web_mercator_layer_templates_and_switch_mapping() -> None:
    node = shutil.which("node")
    assert node is not None
    root = Path(__file__).resolve().parents[2]
    script = r"""
const fs=require('fs'),vm=require('vm');const context={window:{}};vm.createContext(context);
vm.runInContext(fs.readFileSync('src/photo_search/web/map-config.js','utf8'),context);
const c=context.window.AISEARCH_MAP_CONFIG,s=c.sourceTemplates('fake key/with?chars');
process.stdout.write(JSON.stringify({provider:c.provider,attribution:c.attribution,source:s,
  vector:c.layerIds('vector'),imagery:c.layerIds('imagery'),test:c.testTileUrl('fake key/with?chars')}));
"""
    result = subprocess.run([node, "-e", script], cwd=root, check=True, capture_output=True, text=True, encoding="utf-8", timeout=10)
    body = json.loads(result.stdout)
    assert body["provider"] == "tianditu"
    assert set(body["source"]) == {"td-vec", "td-cva", "td-img", "td-cia"}
    for source_id, layer in zip(body["source"], ("vec_w", "cva_w", "img_w", "cia_w"), strict=True):
        source = body["source"][source_id]
        assert source["type"] == "raster" and source["tileSize"] == 256 and source["maxzoom"] == 18
        assert f"https://t0.tianditu.gov.cn/DataServer?T={layer}&x={{x}}&y={{y}}&l={{z}}&tk=fake%20key%2Fwith%3Fchars" == source["tiles"][0]
    assert body["vector"] == ["td-vec-layer", "td-cva-layer"]
    assert body["imagery"] == ["td-img-layer", "td-cia-layer"]
    assert body["test"].endswith("?T=vec_w&x=0&y=0&l=1&tk=fake%20key%2Fwith%3Fchars")
    assert "tianditu.gov.cn" in body["attribution"]
    assert "GS（2025）1508号" in body["attribution"]
