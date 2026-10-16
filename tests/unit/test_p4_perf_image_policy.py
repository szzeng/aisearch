"""SQL suffix eligibility agrees with the filename policy across all formats."""
import random
import sqlite3

from photo_search.catalog.image_policy import IMAGE_EXTENSIONS, image_location_sql, is_image_path


def test_suffix_sql_handles_every_format_and_deceptive_paths():
    rng = random.Random(20261015)
    names = ["a", "a.jpg.txt", "folder.jpg/name", "folder.PNG\\name", "照片.jpg ",
             "照片.ｊｐｇ", "照片.jpeg.old", "照片jpg", "a.İPG", "a.jpɡ", "a.jpg/", ""]
    for suffix in IMAGE_EXTENSIONS:
        names.extend(["照片/目录/影像" + suffix, "folder\\IMG" + suffix.upper(), suffix,
                      "a*[?]'\"" + suffix, "a" + suffix + ".backup"])
    for _ in range(1000):
        name = "".join(rng.choice("abC.XYZ/\\照片 ") for _ in range(rng.randint(0, 50)))
        names.append(name + rng.choice([".jpg", ".JpEg", ".PNG", ".mov", ".zip", ".txt", ""]))
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE TABLE l(id INTEGER PRIMARY KEY,relative_path TEXT)")
        conn.executemany("INSERT INTO l VALUES(?,?)", enumerate(names))
        actual = [r[0] for r in conn.execute(f"SELECT id FROM l WHERE {image_location_sql()} ORDER BY id")]
        assert actual == [i for i, name in enumerate(names) if is_image_path(name)]
    finally:
        conn.close()
