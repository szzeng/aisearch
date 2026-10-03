"""Serve a disposable three-photo album for browser checks; no real photos or models."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests"))


def main() -> None:
    import uvicorn
    from conftest import stamp
    from p2_search_api_fixtures import build_search_env
    from p3_persons_page_fixtures import add_link, add_person, insert_face
    from PIL import Image, ImageDraw

    from photo_search.app import create_app
    from photo_search.search.profile import activate_profile

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8874)
    args = parser.parse_args()
    fixture_stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    folder = ROOT / "tmp" / "p4-person-ux-a-browser" / fixture_stamp

    def photo(path, *, index):
        image = Image.new("RGB", (640, 480), (246, 240, 230))
        draw = ImageDraw.Draw(image)
        draw.text((15, 440), f"SYNTHETIC PHOTO {index} - no real faces or model", fill=(40, 40, 40))
        for x, color in [(30, (215, 80, 60)), (330, (55, 105, 205))]:
            draw.ellipse((x, 40, x + 150, 200), fill=color)
            draw.ellipse((x + 40, 90, x + 52, 102), fill="white")
            draw.ellipse((x + 98, 90, x + 110, 102), fill="white")
            draw.arc((x + 40, 105, x + 110, 165), 0, 180, fill="white", width=4)
        draw.ellipse((200, 260, 350, 420), fill=(80, 150, 70))
        path.parent.mkdir(parents=True, exist_ok=True)
        image.save(path, "JPEG", quality=95)
        return stamp(path)

    with patch("p2_search_fixtures.write_jpeg", side_effect=photo):
        env = build_search_env(folder)
    catalog = env.catalog
    faces = []
    for asset in env.assets.values():
        row = catalog.read_one("SELECT sha256 FROM assets WHERE asset_id = ?", (asset,))
        faces.append(
            [
                insert_face(
                    catalog,
                    asset_id=asset,
                    sha256=row["sha256"],
                    ordinal=ordinal,
                    bbox=(30 + ordinal * 300, 40, 180 + ordinal * 300, 200),
                )
                for ordinal in range(2)
            ]
        )
    dad = add_person(catalog, name="爸爸", avatar_face_id=faces[0][0])
    mom = add_person(catalog, name="妈妈", avatar_face_id=faces[0][1])
    group = add_person(catalog)  # Reproduce the real automatic group's missing stored avatar.
    shared_asset = list(env.assets.values())[1]
    shared_sha = catalog.read_one("SELECT sha256 FROM assets WHERE asset_id = ?", (shared_asset,))[
        "sha256"
    ]
    extra_face = insert_face(
        catalog, asset_id=shared_asset, sha256=shared_sha, ordinal=2, bbox=(200, 260, 350, 420)
    )
    another = add_person(catalog)
    add_link(catalog, extra_face, another)
    add_link(catalog, faces[0][0], dad)
    add_link(catalog, faces[0][1], mom)
    add_link(catalog, faces[1][0], dad)
    add_link(catalog, faces[1][1], group)
    add_link(catalog, faces[2][1], mom)
    add_link(catalog, faces[2][0], dad, decision="SUGGESTED")
    profile = env.create_profile("synthetic-person-browser")
    activate_profile(catalog, profile_id=profile)
    settings = replace(
        env.settings, bind_port=args.port, session_token="synthetic-person-album-demo"
    )
    app = create_app(settings=settings, encoder_factory=lambda: env.encoder)
    evidence = {
        "synthetic": True,
        "model_loaded": False,
        "assets": env.assets,
        "people": {"dad": dad, "mom": mom, "unnamed": group, "another_unnamed": another},
        "faces": faces,
    }
    (folder / "fixture.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"Three synthetic photos ready on 127.0.0.1:{args.port}; no real library/model.", flush=True
    )
    try:
        uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False)
    finally:
        env.context.close()
        env.library.close()


if __name__ == "__main__":
    main()
