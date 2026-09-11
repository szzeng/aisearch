"""Build the 150-query Chinese daily-life evaluation set with fixed annotation rules.

The queries are written by the P0-06 executor. Their ground truth is derived from attributes
that do **not** come from the model under evaluation: capture time (EXIF), geometry, exposure
and colour statistics, InsightFace face counts and RapidOCR text presence. Every query carries
an explicit rule, and the builder refuses to emit a query whose positive set is empty, tiny or
so large that the metric would be meaningless.

    uv run python tools/evaluation/build_query_set.py --report-only
    uv run python tools/evaluation/build_query_set.py            # writes the dataset JSON

Tuning and holdout are separated at query level; the images form one shared corpus because an
attribute-grounded image can honestly be relevant to queries in both splits.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))

from corpus import default_evaluation_dir, read_json, write_json  # noqa: E402
from runtime_common import REPOSITORY_ROOT  # noqa: E402

Rule = tuple
MIN_POSITIVES = 3
MAX_POSITIVE_RATIO = 0.40
DEFAULT_DATASET = "chinese-clip-eval-dataset.json"
REPO_SUMMARY = REPOSITORY_ROOT / "docs/reports/P0-06-eval-query-set.json"
AUTHORIZATION = (
    "User authorization 2026-09-11: P0-06 evaluation may read D:\\SZ\\朋友, D:\\SZ\\canon, "
    "D:\\SZ\\vsco, D:\\SZ\\网络 and D:\\SZ\\pic as the sample source; read-only, no modification, "
    "and 150 Chinese queries written by the executor."
)
ANNOTATION_RULES = (
    "Ground truth is rule-derived from signals independent of the evaluated model: EXIF capture "
    "time (file mtime when EXIF is absent), pixel geometry, exposure/colour statistics over a "
    "384 px RGB thumbnail, InsightFace face count and RapidOCR text presence. grade 2 = strict "
    "rule, grade 1 = documented neighbouring band. Thresholds are fixed before any model run and "
    "are chosen on the tuning queries only; labels are not human judgements but objective "
    "attribute matches, which is the accuracy limit of this evaluation."
)


def _compare(rule: Rule, image: dict[str, Any]) -> bool:
    kind = rule[0]
    if kind == "in":
        return image.get(rule[1]) in rule[2]
    if kind == "ge":
        value = image.get(rule[1])
        return value is not None and value >= rule[2]
    if kind == "le":
        value = image.get(rule[1])
        return value is not None and value <= rule[2]
    if kind == "between":
        value = image.get(rule[1])
        return value is not None and rule[2] <= value <= rule[3]
    if kind == "eq":
        return image.get(rule[1]) == rule[2]
    if kind == "flag":
        return bool(image.get(rule[1]))
    if kind == "nflag":
        return not bool(image.get(rule[1]))
    if kind == "and":
        return all(_compare(part, image) for part in rule[1])
    if kind == "or":
        return any(_compare(part, image) for part in rule[1])
    raise ValueError(f"unknown rule: {rule!r}")


def matches(rule: Rule | None, images: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    if rule is None:
        return []
    return [image for image in images if _compare(rule, image)]


def night(hour: int) -> bool:
    return hour >= 19 or hour <= 4


NIGHT = ("or", [("ge", "hour", 19), ("le", "hour", 4)])
DARK = ("le", "brightness", 0.30)
BRIGHT = ("ge", "brightness", 0.55)
WITH_FACES = ("ge", "face_count", 1)
NO_FACES = ("le", "face_count", 0)
WITH_TEXT = ("ge", "ocr_chars", 1)


def query_table() -> list[dict[str, Any]]:
    """150 invented Chinese daily-life queries: 50 tuning + 100 holdout."""
    entries: list[tuple[str, str, str, Rule, Rule | None]] = [
        # --- capture time -------------------------------------------------------------
        ("晚上拍的照片", "tuning", NIGHT, ("between", "hour", 17, 18)),
        ("深夜拍的照片", "holdout", ("or", [("ge", "hour", 22), ("le", "hour", 3)]), ("between", "hour", 20, 21)),
        ("清晨拍的照片", "holdout", ("between", "hour", 5, 7), ("eq", "hour", 8)),
        ("早上拍的照片", "tuning", ("between", "hour", 6, 9), ("eq", "hour", 10)),
        ("上午拍的照片", "holdout", ("between", "hour", 9, 11), ("eq", "hour", 12)),
        ("中午拍的照片", "holdout", ("between", "hour", 12, 13), ("eq", "hour", 11)),
        ("下午拍的照片", "tuning", ("between", "hour", 14, 16), ("eq", "hour", 13)),
        ("傍晚拍的照片", "holdout", ("between", "hour", 17, 18), ("eq", "hour", 19)),
        ("黄昏时分的照片", "tuning", ("between", "hour", 17, 19), ("eq", "hour", 20)),
        ("天刚亮时拍的照片", "holdout", ("between", "hour", 5, 6), ("eq", "hour", 7)),
        ("夏天的照片", "tuning", ("in", "month", [6, 7, 8]), None),
        ("冬天的照片", "holdout", ("in", "month", [12, 1, 2]), None),
        ("春天的照片", "holdout", ("in", "month", [3, 4, 5]), None),
        ("秋天的照片", "tuning", ("in", "month", [9, 10, 11]), None),
        ("周末拍的照片", "tuning", ("in", "weekday", [5, 6]), None),
        ("工作日白天的照片", "holdout", ("and", [("in", "weekday", [0, 1, 2, 3, 4]), ("between", "hour", 10, 14)]), None),
        ("2018年拍的照片", "holdout", ("eq", "year", 2018), None),
        ("2019年拍的照片", "tuning", ("eq", "year", 2019), None),
        ("2020年拍的照片", "holdout", ("eq", "year", 2020), None),
        ("2021年拍的照片", "holdout", ("eq", "year", 2021), None),
        ("2022年拍的照片", "tuning", ("eq", "year", 2022), None),
        ("2023年拍的照片", "holdout", ("eq", "year", 2023), None),
        ("2024年拍的照片", "holdout", ("eq", "year", 2024), None),
        ("2025年拍的照片", "tuning", ("eq", "year", 2025), None),
        ("今年拍的照片", "holdout", ("eq", "year", 2026), None),
        ("一月份拍的照片", "holdout", ("eq", "month", 1), None),
        ("五月份拍的照片", "tuning", ("eq", "month", 5), None),
        ("十月份拍的照片", "holdout", ("eq", "month", 10), None),
        ("十二月拍的照片", "holdout", ("eq", "month", 12), None),
        ("七月拍的照片", "holdout", ("eq", "month", 7), None),
        # --- geometry -----------------------------------------------------------------
        ("竖着拍的照片", "tuning", ("eq", "orientation", "portrait"), None),
        ("横版的日常照片", "holdout", ("and", [("eq", "orientation", "landscape"), ("between", "hour", 10, 16)]), None),
        ("正方形构图的照片", "holdout", ("eq", "orientation", "square"), None),
        ("全景照片", "holdout", ("ge", "aspect", 1.8), ("ge", "aspect", 1.5)),
        ("很宽的全景照片", "tuning", ("ge", "aspect", 2.2), ("ge", "aspect", 1.8)),
        ("竖版长图的照片", "holdout", ("le", "aspect", 0.6), ("le", "aspect", 0.75)),
        ("分辨率很高的照片", "tuning", ("ge", "pixels", 30000000), ("ge", "pixels", 20000000)),
        ("大尺寸的高清照片", "holdout", ("ge", "bytes", 8000000), ("ge", "bytes", 5000000)),
        ("小文件的照片", "holdout", ("le", "bytes", 500000), ("le", "bytes", 900000)),
        ("文件很小的照片", "holdout", ("le", "bytes", 300000), ("le", "bytes", 500000)),
        # --- exposure and colour ------------------------------------------------------
        ("明亮的照片", "tuning", BRIGHT, ("ge", "brightness", 0.50)),
        ("很亮的照片", "holdout", ("ge", "brightness", 0.65), ("ge", "brightness", 0.55)),
        ("昏暗的照片", "tuning", DARK, ("le", "brightness", 0.35)),
        ("很暗的照片", "holdout", ("le", "brightness", 0.18), DARK),
        ("亮度中等的照片", "holdout", ("between", "brightness", 0.42, 0.48), ("between", "brightness", 0.40, 0.50)),
        ("对比强烈的照片", "holdout", ("ge", "contrast", 0.29), ("ge", "contrast", 0.25)),
        ("灰蒙蒙的照片", "tuning", ("le", "contrast", 0.13), ("le", "contrast", 0.17)),
        ("色彩鲜艳的照片", "tuning", ("ge", "saturation", 0.45), ("ge", "saturation", 0.35)),
        ("颜色很艳的照片", "holdout", ("ge", "saturation", 0.55), ("ge", "saturation", 0.45)),
        ("颜色很淡的照片", "holdout", ("le", "saturation", 0.14), ("le", "saturation", 0.20)),
        ("接近黑白的照片", "tuning", ("ge", "grey_ratio", 0.75), ("ge", "grey_ratio", 0.6)),
        ("几乎黑白的照片", "holdout", ("ge", "grey_ratio", 0.9), ("ge", "grey_ratio", 0.75)),
        ("偏红的照片", "tuning", ("ge", "redness", 0.10), ("ge", "redness", 0.06)),
        ("红色很多的照片", "holdout", ("ge", "redness", 0.18), ("ge", "redness", 0.10)),
        ("偏蓝的照片", "tuning", ("ge", "blueness", 0.06), ("ge", "blueness", 0.03)),
        ("蓝色很明显的照片", "holdout", ("ge", "blueness", 0.12), ("ge", "blueness", 0.06)),
        ("偏绿的照片", "holdout", ("ge", "greenness", 0.05), ("ge", "greenness", 0.03)),
        ("绿色植物很多的照片", "tuning", ("ge", "greenness", 0.10), ("ge", "greenness", 0.05)),
        ("暖色调的照片", "tuning", ("ge", "warmth", 0.12), ("ge", "warmth", 0.08)),
        ("冷色调的照片", "holdout", ("le", "warmth", -0.08), ("le", "warmth", -0.03)),
        ("偏黄的照片", "holdout", ("ge", "warmth", 0.20), ("ge", "warmth", 0.14)),
        ("蓝天下拍的照片", "holdout", ("ge", "blueness", 0.10), ("ge", "blueness", 0.06)),
        ("草坪和绿树的照片", "holdout", ("ge", "greenness", 0.08), ("ge", "greenness", 0.05)),
        ("夕阳西下的照片", "tuning", ("and", [("between", "hour", 17, 19), ("ge", "warmth", 0.10)]), ("and", [("between", "hour", 16, 20), ("ge", "warmth", 0.05)])),
        ("高饱和又明亮的照片", "holdout", ("and", [("ge", "saturation", 0.45), ("ge", "brightness", 0.5)]), ("and", [("ge", "saturation", 0.35), ("ge", "brightness", 0.45)])),
        ("暗淡的低调照片", "holdout", ("and", [DARK, ("ge", "contrast", 0.15)]), ("and", [("le", "brightness", 0.35), ("ge", "contrast", 0.12)])),
        ("反差大的黑白照片", "holdout", ("and", [("ge", "grey_ratio", 0.7), ("ge", "contrast", 0.2)]), ("and", [("ge", "grey_ratio", 0.6), ("ge", "contrast", 0.15)])),
        ("蓝色天空很亮的照片", "holdout", ("and", [("ge", "blueness", 0.08), ("ge", "brightness", 0.5)]), ("and", [("ge", "blueness", 0.05), ("ge", "brightness", 0.45)])),
        # --- people -------------------------------------------------------------------
        ("有人的照片", "tuning", WITH_FACES, None),
        ("户外没有人的照片", "holdout", ("and", [NO_FACES, ("or", [("ge", "greenness", 0.05), ("ge", "blueness", 0.06)])]), ("and", [NO_FACES, ("or", [("ge", "greenness", 0.02), ("ge", "blueness", 0.03)])])),
        ("合影", "tuning", ("ge", "face_count", 2), ("ge", "face_count", 1)),
        ("多人的合照", "holdout", ("ge", "face_count", 3), ("ge", "face_count", 2)),
        ("一两个人的照片", "holdout", ("between", "face_count", 1, 2), None),
        ("单人照", "tuning", ("eq", "face_count", 1), None),
        ("人很多的热闹照片", "holdout", ("ge", "face_count", 4), ("ge", "face_count", 3)),
        ("人脸很清楚的照片", "holdout", ("ge", "max_det_score", 0.8), ("ge", "max_det_score", 0.7)),
        ("只有背景没有人的照片", "holdout", ("and", [NO_FACES, ("ge", "brightness", 0.55)]), ("and", [NO_FACES, ("ge", "brightness", 0.48)])),
        ("昏暗环境里有人", "holdout", ("and", [WITH_FACES, DARK]), ("and", [WITH_FACES, ("le", "brightness", 0.4)])),
        ("白天拍的人像", "tuning", ("and", [WITH_FACES, ("between", "hour", 9, 16)]), None),
        ("晚上拍的人像", "holdout", ("and", [WITH_FACES, NIGHT]), None),
        ("户外人像", "holdout", ("and", [WITH_FACES, ("or", [("ge", "greenness", 0.05), ("ge", "blueness", 0.05)])]), ("and", [WITH_FACES, ("ge", "saturation", 0.3)])),
        ("居家人像", "holdout", ("and", [WITH_FACES, ("between", "brightness", 0.3, 0.6), ("le", "saturation", 0.35)]), ("and", [WITH_FACES, ("le", "brightness", 0.65)])),
        ("穿深色衣服的人", "holdout", ("and", [WITH_FACES, ("le", "brightness", 0.35)]), ("and", [WITH_FACES, ("le", "brightness", 0.45)])),
        ("有清晰人脸的照片", "tuning", ("ge", "max_det_score", 0.65), ("ge", "max_det_score", 0.5)),
        ("人脸比较模糊的照片", "holdout", ("and", [("ge", "face_count", 1), ("le", "max_det_score", 0.62), ("ge", "max_det_score", 0.3)]), ("and", [("ge", "face_count", 1), ("le", "max_det_score", 0.7)])),
        # --- text in the picture ------------------------------------------------------
        ("有文字的照片", "tuning", WITH_TEXT, None),
        ("文字很多的照片", "tuning", ("ge", "ocr_chars", 60), ("ge", "ocr_chars", 30)),
        ("像截图一样的照片", "holdout", ("ge", "ocr_chars", 150), ("ge", "ocr_chars", 80)),
        ("带数字的照片", "holdout", ("flag", "ocr_has_digits"), None),
        ("带英文的照片", "holdout", ("flag", "ocr_has_latin"), None),
        ("没有文字的绿色照片", "holdout", ("and", [("le", "ocr_chars", 0), ("ge", "greenness", 0.05)]), ("and", [("le", "ocr_chars", 3), ("ge", "greenness", 0.03)])),
        ("标题文字不多的照片", "holdout", ("between", "ocr_chars", 5, 40), None),
        ("单据或价目表的照片", "holdout", ("and", [("flag", "ocr_has_digits"), ("ge", "ocr_chars", 40)]), ("and", [("flag", "ocr_has_digits"), ("ge", "ocr_chars", 20)])),
        ("路牌或标识的照片", "holdout", ("between", "ocr_chars", 3, 30), None),
        ("书页或文档的照片", "holdout", ("ge", "ocr_chars", 200), ("ge", "ocr_chars", 120)),
        ("数字和英文都有的照片", "holdout", ("and", [("flag", "ocr_has_digits"), ("flag", "ocr_has_latin")]), None),
        # --- combinations -------------------------------------------------------------
        ("晚上拍的彩色照片", "holdout", ("and", [NIGHT, ("ge", "saturation", 0.4)]), ("and", [NIGHT, ("ge", "saturation", 0.3)])),
        ("白天拍的风景照", "tuning", ("and", [("between", "hour", 12, 15), NO_FACES]), ("and", [("between", "hour", 10, 16), NO_FACES])),
        ("春天的绿色照片", "holdout", ("and", [("in", "month", [3, 4, 5]), ("ge", "greenness", 0.05)]), ("and", [("in", "month", [3, 4, 5]), ("ge", "greenness", 0.02)])),
        ("夏天的蓝天", "holdout", ("and", [("in", "month", [6, 7, 8]), ("ge", "blueness", 0.06)]), ("and", [("in", "month", [6, 7, 8]), ("ge", "blueness", 0.03)])),
        ("冬天昏暗的照片", "holdout", ("and", [("in", "month", [12, 1, 2]), DARK]), ("and", [("in", "month", [12, 1, 2]), ("le", "brightness", 0.4)])),
        ("傍晚的暖色照片", "tuning", ("and", [("between", "hour", 17, 19), ("ge", "warmth", 0.10)]), ("and", [("between", "hour", 16, 20), ("ge", "warmth", 0.05)])),
        ("清晨的明亮照片", "holdout", ("and", [("between", "hour", 5, 8), BRIGHT]), ("and", [("between", "hour", 5, 8), ("ge", "brightness", 0.45)])),
        ("深夜的暗色照片", "holdout", ("and", [("or", [("ge", "hour", 22), ("le", "hour", 3)]), DARK]), ("and", [NIGHT, ("le", "brightness", 0.4)])),
        ("竖版的人像", "holdout", ("and", [("eq", "orientation", "portrait"), WITH_FACES]), None),
        ("横版的绿色风景", "tuning", ("and", [("eq", "orientation", "landscape"), NO_FACES, ("ge", "greenness", 0.03)]), ("and", [("eq", "orientation", "landscape"), NO_FACES, ("ge", "greenness", 0.02)])),
        ("竖版的文字照片", "holdout", ("and", [("eq", "orientation", "portrait"), ("ge", "ocr_chars", 20)]), ("and", [("eq", "orientation", "portrait"), WITH_TEXT])),
        ("横版的合影", "holdout", ("and", [("eq", "orientation", "landscape"), ("ge", "face_count", 2)]), ("and", [("eq", "orientation", "landscape"), WITH_FACES])),
        ("大尺寸又明亮的照片", "holdout", ("and", [("ge", "bytes", 5000000), BRIGHT]), ("and", [("ge", "bytes", 3000000), ("ge", "brightness", 0.5)])),
        ("小尺寸又昏暗的照片", "holdout", ("and", [("le", "bytes", 900000), DARK]), ("and", [("le", "bytes", 1500000), ("le", "brightness", 0.35)])),
        ("周末拍的合影", "holdout", ("and", [("in", "weekday", [5, 6]), ("ge", "face_count", 2)]), ("and", [("in", "weekday", [5, 6]), WITH_FACES])),
        ("工作日白天的文字照片", "holdout", ("and", [("in", "weekday", [0, 1, 2, 3, 4]), ("ge", "ocr_chars", 40), ("between", "hour", 9, 17)]), ("and", [("in", "weekday", [0, 1, 2, 3, 4]), ("ge", "ocr_chars", 15)])),
        ("2019年夏天的照片", "tuning", ("and", [("eq", "year", 2019), ("in", "month", [6, 7, 8])]), None),
        ("2023年冬天的照片", "holdout", ("and", [("eq", "year", 2023), ("in", "month", [12, 1, 2])]), None),
        ("2024年有人的照片", "holdout", ("and", [("eq", "year", 2024), WITH_FACES]), None),
        ("2024年的绿色照片", "tuning", ("and", [("eq", "year", 2024), ("ge", "greenness", 0.05)]), ("and", [("eq", "year", 2024), ("ge", "greenness", 0.02)])),
        ("去年的照片", "holdout", ("eq", "year", 2025), None),
        # --- second batch: remaining daily-life queries ---------------------------------
        ("色彩饱满的照片", "tuning", ("and", [("ge", "saturation", 0.4), ("ge", "brightness", 0.4)]), ("and", [("ge", "saturation", 0.3), ("ge", "brightness", 0.35)])),
        ("光影对比明显的照片", "tuning", ("and", [("ge", "contrast", 0.30), ("between", "brightness", 0.3, 0.5)]), ("and", [("ge", "contrast", 0.27), ("between", "brightness", 0.25, 0.55)])),
        ("室内暖光的照片", "tuning", ("and", [("ge", "warmth", 0.10), ("le", "brightness", 0.5)]), ("and", [("ge", "warmth", 0.06), ("le", "brightness", 0.6)])),
        ("蓝天白云的照片", "tuning", ("and", [("ge", "blueness", 0.08), ("ge", "brightness", 0.55)]), ("and", [("ge", "blueness", 0.05), ("ge", "brightness", 0.5)])),
        ("有一点绿色的照片", "tuning", ("ge", "greenness", 0.03), None),
        ("稍微偏蓝的照片", "tuning", ("ge", "blueness", 0.04), None),
        ("有两个人以上的照片", "tuning", ("ge", "face_count", 2), None),
        ("文字很少的照片", "tuning", ("between", "ocr_chars", 1, 15), None),
        ("上午出门拍的照片", "tuning", ("between", "hour", 8, 11), None),
        ("九月份拍的照片", "tuning", ("eq", "month", 9), None),
        ("十一月份拍的照片", "tuning", ("eq", "month", 11), None),
        ("八月份拍的照片", "tuning", ("eq", "month", 8), None),
        ("中午前后的照片", "tuning", ("between", "hour", 11, 13), None),
        ("竖版的彩色照片", "tuning", ("and", [("eq", "orientation", "portrait"), ("ge", "saturation", 0.35)]), None),
        ("夜晚的暖色照片", "holdout", ("and", [NIGHT, ("ge", "warmth", 0.08)]), ("and", [NIGHT, ("ge", "warmth", 0.04)])),
        ("清晨的绿色照片", "holdout", ("and", [("between", "hour", 5, 8), ("ge", "greenness", 0.03)]), ("and", [("between", "hour", 5, 9), ("ge", "greenness", 0.02)])),
        ("下午的明亮照片", "holdout", ("and", [("between", "hour", 14, 16), ("ge", "brightness", 0.5)]), ("and", [("between", "hour", 13, 17), ("ge", "brightness", 0.45)])),
        ("傍晚拍的人像", "holdout", ("and", [("between", "hour", 17, 18), WITH_FACES]), ("and", [("between", "hour", 16, 19), WITH_FACES])),
        ("蓝天的竖版照片", "holdout", ("and", [("eq", "orientation", "portrait"), ("ge", "blueness", 0.06)]), ("and", [("eq", "orientation", "portrait"), ("ge", "blueness", 0.03)])),
        ("绿色的横版照片", "holdout", ("and", [("eq", "orientation", "landscape"), ("ge", "greenness", 0.06)]), ("and", [("eq", "orientation", "landscape"), ("ge", "greenness", 0.03)])),
        ("有文字又明亮的照片", "holdout", ("and", [WITH_TEXT, BRIGHT]), ("and", [WITH_TEXT, ("ge", "brightness", 0.45)])),
        ("文字多又昏暗的照片", "holdout", ("and", [("ge", "ocr_chars", 40), ("le", "brightness", 0.4)]), ("and", [("ge", "ocr_chars", 20), ("le", "brightness", 0.5)])),
        ("人像特写", "holdout", ("and", [("eq", "face_count", 1), ("ge", "max_det_score", 0.7)]), ("and", [("eq", "face_count", 1), ("ge", "max_det_score", 0.55)])),
        ("白天拍的多人合照", "holdout", ("and", [("ge", "face_count", 3), ("between", "hour", 9, 16)]), ("and", [("ge", "face_count", 3), ("between", "hour", 8, 17)])),
        ("三月份拍的照片", "holdout", ("eq", "month", 3), None),
        ("六月份拍的照片", "holdout", ("eq", "month", 6), None),
        ("二月份拍的照片", "holdout", ("eq", "month", 2), None),
        ("四月份拍的照片", "holdout", ("eq", "month", 4), None),
        ("阴天拍的照片", "holdout", ("and", [("ge", "grey_ratio", 0.5), ("le", "contrast", 0.2)]), ("and", [("ge", "grey_ratio", 0.4), ("le", "contrast", 0.24)])),
        ("逆光偏暗的照片", "holdout", ("and", [("le", "brightness", 0.35), ("ge", "contrast", 0.18)]), ("and", [("le", "brightness", 0.42), ("ge", "contrast", 0.15)])),
        ("颜色偏紫的晚霞", "holdout", ("and", [("between", "hour", 17, 20), ("ge", "warmth", 0.08)]), ("and", [("between", "hour", 16, 21), ("ge", "warmth", 0.04)])),
        ("周末中午拍的照片", "holdout", ("and", [("in", "weekday", [5, 6]), ("between", "hour", 11, 13)]), None),
        ("早上拍的绿色照片", "holdout", ("and", [("between", "hour", 6, 10), ("ge", "greenness", 0.03)]), ("and", [("between", "hour", 5, 11), ("ge", "greenness", 0.02)])),    ]
    return [
        {
            "id": f"q{index:03d}",
            "text": text,
            "split": split,
            "strict": strict,
            "near": near,
        }
        for index, (text, split, strict, near) in enumerate(entries, start=1)
    ]


def build(corpus: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    images = corpus["images"]
    for image in images:
        image["pixels"] = int(image["width"]) * int(image["height"])
    queries = query_table()
    problems: list[str] = []
    report: list[dict[str, Any]] = []
    dataset_queries: list[dict[str, Any]] = []
    for query in queries:
        strict = matches(query["strict"], images)
        near = matches(query["near"], images) if query["near"] else []
        relevant: dict[str, int] = {image["id"]: 2 for image in strict}
        for image in near:
            relevant.setdefault(image["id"], 1)
        positive = len(relevant)
        share = positive / max(1, len(images))
        if positive < MIN_POSITIVES:
            problems.append(f"{query['id']} {query['text']}: only {positive} positive images")
        if share > MAX_POSITIVE_RATIO:
            problems.append(
                f"{query['id']} {query['text']}: {positive} positives is {share:.0%} of the corpus"
            )
        report.append(
            {
                "id": query["id"],
                "text": query["text"],
                "split": query["split"],
                "strict_positives": len(strict),
                "graded_positives": positive,
                "corpus_share": round(share, 4),
                "rule": _render(query["strict"]),
                "near_rule": _render(query["near"]) if query["near"] else None,
            }
        )
        dataset_queries.append(
            {
                "id": query["id"],
                "text": query["text"],
                "split": query["split"],
                "relevant": relevant,
            }
        )
    policy_violations = [
        query for query in queries if query["split"] not in {"tuning", "holdout"}
    ]
    if policy_violations:  # pragma: no cover - defensive
        problems.append(f"{len(policy_violations)} queries have an unknown split")
    dataset = {
        "schema_version": 1,
        "name": "P0-06 authorized Chinese daily-life sample",
        "authorization": AUTHORIZATION,
        "annotation_rules": ANNOTATION_RULES,
        "image_split_policy": "shared",
        "images": [
            {"id": image["id"], "path": image["path"], "split": "shared"}
            for image in images
        ],
        "queries": dataset_queries,
    }
    return dataset, report, problems


def _render(rule: Rule | None) -> str:
    if rule is None:
        return ""
    kind = rule[0]
    if kind in {"ge", "le", "eq", "flag", "nflag"}:
        return " ".join(str(part) for part in rule)
    if kind == "between":
        return f"{rule[2]} <= {rule[1]} <= {rule[3]}"
    if kind == "in":
        return f"{rule[1]} in {rule[2]}"
    if kind in {"and", "or"}:
        joiner = " and " if kind == "and" else " or "
        return "(" + joiner.join(_render(part) for part in rule[1]) + ")"
    return str(rule)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=default_evaluation_dir() / "corpus.json")
    parser.add_argument("--output", type=Path, default=default_evaluation_dir() / DEFAULT_DATASET)
    parser.add_argument("--report-only", action="store_true")
    args = parser.parse_args()

    try:
        corpus = read_json(args.corpus.resolve())
    except (OSError, ValueError) as error:
        print(json.dumps({"status": "fail", "message": f"cannot read corpus: {error}"}), file=sys.stderr)
        return 1
    dataset, report, problems = build(corpus)
    missing = [
        record for record in report if record["graded_positives"] == 0
    ]
    summary = {
        "status": "fail" if problems else "ok",
        "task": "P0-06",
        "stage": "B",
        "corpus_images": len(corpus["images"]),
        "queries": len(report),
        "tuning": sum(1 for record in report if record["split"] == "tuning"),
        "holdout": sum(1 for record in report if record["split"] == "holdout"),
        "annotation_rules": ANNOTATION_RULES,
        "authorization": AUTHORIZATION,
        "problems": problems,
        "queries_report": report,
    }
    if missing:
        summary["status"] = "fail"
    if args.report_only:
        print(
            json.dumps(
                {
                    "status": summary["status"],
                    "queries": summary["queries"],
                    "tuning": summary["tuning"],
                    "holdout": summary["holdout"],
                    "problems": problems[:12],
                    "problem_count": len(problems),
                    "positive_counts": sorted(
                        record["graded_positives"] for record in report
                    )[:8],
                },
                ensure_ascii=False,
            )
        )
        return 1 if problems else 0
    if problems:
        print(json.dumps({"status": "fail", "problems": problems}, ensure_ascii=False), file=sys.stderr)
        return 1
    write_json(args.output.resolve(), dataset)
    write_json(REPO_SUMMARY, summary)
    print(
        json.dumps(
            {
                "status": "ok",
                "dataset": str(args.output.resolve()),
                "summary": str(REPO_SUMMARY),
                "queries": summary["queries"],
                "tuning": summary["tuning"],
                "holdout": summary["holdout"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
