"""Preview the real person UI with navigation-only overrides and disposable data."""

from __future__ import annotations

import argparse
import json
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[4]
PREVIEW = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]


def main() -> None:
    import uvicorn
    from conftest import ApiEnv, stamp
    from p3_persons_page_fixtures import add_link, add_person, seed_library
    from PIL import Image, ImageDraw

    from photo_search.api import web
    from photo_search.persons.collections import create_collection, edit_members

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=18780)
    parser.add_argument("--production", action="store_true", help="Serve current product files without preview overrides")
    args = parser.parse_args()
    run = ROOT / ".tmp" / "P4-PERSON-NAV-PROJECT" / datetime.now(timezone.utc).strftime(
        "%Y%m%d-%H%M%S-%f"
    )
    run.mkdir(parents=True, exist_ok=False)
    token = secrets.token_urlsafe(32)
    env = ApiEnv.build(run, session_token=token)

    def draw_sample(path: Path, *, size: tuple[int, int], noise: int) -> Path:
        palette = [(93, 126, 151), (169, 127, 98), (123, 146, 117), (133, 123, 154)]
        image = Image.new("RGB", size, (236, 232, 222))
        draw = ImageDraw.Draw(image)
        for index, x in enumerate((36, 186)):
            color = palette[(noise + index) % len(palette)]
            draw.rounded_rectangle((x - 12, 110, x + 100, 226), 25, fill=color)
            draw.ellipse((x, 22, x + 84, 110), fill=(214, 175, 141))
            draw.arc((x, 17, x + 84, 100), 180, 360, fill=(61, 54, 50), width=14)
            for eye in (24, 58):
                draw.ellipse((x + eye, 58, x + eye + 4, 62), fill=(50, 50, 45))
            draw.arc((x + 24, 66, x + 60, 89), 0, 180, fill=(119, 81, 70), width=2)
        draw.text((8, 229), f"SYNTHETIC SAMPLE {noise}", fill=(40, 65, 65))
        path.parent.mkdir(parents=True, exist_ok=True)
        image.save(path, "JPEG", quality=92)
        return stamp(path)

    try:
        boxes = [(36, 22, 120, 110), (186, 22, 270, 110)]
        with patch("conftest.write_jpeg", side_effect=draw_sample):
            records = seed_library(
                env, {f"sample-{i:02d}.jpg": boxes for i in range(1, 7)}
            )
        faces = [face for record in records.values() for face in record["faces"]]
        catalog = env.service.catalog
        named = []
        for index, name in enumerate(("妈妈", "爸爸", "小雨")):
            person = add_person(catalog, name=name, avatar_face_id=faces[index])
            add_link(catalog, faces[index], person)
            named.append(person)
        for index in range(3, 7):
            person = add_person(catalog, avatar_face_id=faces[index])
            add_link(catalog, faces[index], person)
        for index in (7, 8):
            add_link(catalog, faces[index], named[index % 3], decision="SUGGESTED")
        for title, members in (("家人", named[:2]), ("一起出游", named[1:])):
            collection = create_collection(catalog, title)["collection"]
            edit_members(
                catalog,
                collection["collection_id"],
                action="add",
                person_ids=members,
                instance_token=collection["instance_token"],
            )

        original = web.read_static
        overrides = {"persons.html", "navigation.js", "navigation.css"}

        def preview_static(relative: str) -> bytes | None:
            # Exact filenames only. All other reads keep the product's path validation.
            if relative in overrides:
                return (PREVIEW / relative).read_bytes()
            return original(relative)

        if not args.production:
            web.read_static = preview_static
        launch = (
            f"http://127.0.0.1:{args.port}/api/v1/session"
            f"?next=/persons&token={token}"
        )
        (run / "launch.url").write_text(launch, encoding="utf-8")
        (run / "fixture.json").write_text(
            json.dumps({"synthetic": True, "photos": 6, "named": 3, "unnamed": 4}),
            encoding="utf-8",
        )
        (ROOT / ".tmp" / "P4-PERSON-NAV-PROJECT" / "latest-run.txt").write_text(
            str(run), encoding="utf-8"
        )
        print(f"Synthetic project preview ready on 127.0.0.1:{args.port}", flush=True)
        print("Fresh single-use link saved in isolated run/launch.url", flush=True)
        uvicorn.run(env.app, host="127.0.0.1", port=args.port, access_log=False)
    finally:
        env.close()


if __name__ == "__main__":
    main()
