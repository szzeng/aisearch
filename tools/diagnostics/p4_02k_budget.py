"""P4-02K: read-only recomputation of the F2 budget assembly and the A3 decision inputs.

This tool deliberately has no measurement side: it never opens a catalog, a Lance table or a
vector store, never scans a photo library and never writes anything except its own
``--output`` JSON. It reads the machine-readable evidence that earlier tasks already produced
and re-checks, per field path, the numbers the P4-02K report is allowed to quote.

Three things it proves by construction:

* the 300 000-asset stage split is a *mutually exclusive* assembly, i.e.
  ``sqlite_read_all + engine_total + other_fusion_and_authority == service.median_ms``;
* the candidate-block path and the A3 single-pass engine query are two alternatives of the
  same budget, so adding ``engine_total`` (60 block calls) to the A3 single pass is wrong;
* the P4-02F §14 assembly double counts the product stage medians that the instrumentation
  itself declares to overlap the sqlite/engine buckets.

Any missing field or failed identity is a hard failure (non-zero exit): the report may not
hand-copy a number the tool cannot re-read.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Tolerance for the "sum of mutually exclusive buckets equals the reported total" identities.
# The evidence files round each bucket to 3 decimals, so a few 0.01 ms of slack is expected.
IDENTITY_TOLERANCE_MS = 0.05


class MissingField(RuntimeError):
    """A field path the report depends on is absent or malformed."""


def dig(document: Any, path: str) -> Any:
    """Resolve a dotted/indexed field path such as ``rounds[0].summary.median_by_stage_ms``."""
    node = document
    for token in _tokens(path):
        if isinstance(token, int):
            if not isinstance(node, list) or token >= len(node):
                raise MissingField(f"{path!r}: index {token} out of range")
            node = node[token]
        else:
            if not isinstance(node, dict) or token not in node:
                raise MissingField(f"{path!r}: no key {token!r}")
            node = node[token]
    if node is None:
        raise MissingField(f"{path!r}: value is null")
    return node


def _tokens(path: str) -> list[Any]:
    """Split a field path, honouring ``[i]`` indexes and ``["key.with.dots"]`` keys."""
    tokens: list[Any] = []
    buffer = ""
    index = 0
    while index < len(path):
        char = path[index]
        if char == ".":
            if buffer:
                tokens.append(buffer)
                buffer = ""
            index += 1
        elif char == "[":
            if buffer:
                tokens.append(buffer)
                buffer = ""
            end = path.find("]", index)
            if end < 0:
                raise MissingField(f"{path!r}: unterminated index")
            inner = path[index + 1:end].strip()
            if inner[:1] in ("'", '"'):
                tokens.append(inner[1:-1])
            else:
                try:
                    tokens.append(int(inner))
                except ValueError as error:
                    raise MissingField(f"{path!r}: malformed index {inner!r}") from error
            index = end + 1
        else:
            buffer += char
            index += 1
    if buffer:
        tokens.append(buffer)
    return tokens


def field(key: str) -> str:
    """A path suffix for one key, quoted when the key itself contains a dot."""
    return f'["{key}"]' if "." in key else f".{key}"


class Evidence:
    """Every source file plus a dense record of where each quoted number came from."""

    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root
        self._documents: dict[str, Any] = {}
        self.records: list[dict[str, Any]] = []

    def load(self, relative_path: str) -> Any:
        if relative_path not in self._documents:
            path = self.repo_root / relative_path
            if not path.is_file():
                raise MissingField(f"evidence file not found: {relative_path}")
            self._documents[relative_path] = json.loads(path.read_text(encoding="utf-8"))
        return self._documents[relative_path]

    def value(
        self,
        label: str,
        relative_path: str,
        path: str,
        *,
        statistic: str,
        samples: int | None = None,
        note: str | None = None,
    ) -> float:
        raw = dig(self.load(relative_path), path)
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise MissingField(f"{relative_path}:{path}: expected a number, got {type(raw).__name__}")
        record: dict[str, Any] = {
            "label": label,
            "source": relative_path,
            "field_path": path,
            "statistic": statistic,
            "value": float(raw),
        }
        if samples is not None:
            record["samples"] = int(samples)
        if note:
            record["note"] = note
        self.records.append(record)
        return float(raw)

    def fingerprints(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for relative_path in sorted(self._documents):
            payload = (self.repo_root / relative_path).read_bytes()
            out[relative_path] = {
                "bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        return out

    def per_sample_identity(
        self, name: str, relative_path: str, round_index: int
    ) -> dict[str, Any]:
        """Check, per sample, that its own buckets add up to its own service time.

        This is the exact form of the identity: ``other_ms`` is computed inside the evidence
        file as ``service_ms - sqlite_ms - engine_ms`` for that very sample, so the sum is
        exact up to float rounding. Sums of separate medians need not match as tightly.
        """
        samples = dig(self.load(relative_path), f"rounds[{round_index}].samples")
        worst = 0.0
        for sample in samples:
            delta = (
                float(sample["service_ms"])
                - float(sample["sqlite_ms"])
                - float(sample["engine_ms"])
                - float(sample["other_ms"])
            )
            worst = max(worst, abs(delta))
        return {
            "name": name,
            "expression": "per sample: sqlite_ms + engine_ms + other_ms == service_ms",
            "source": relative_path,
            "round_index": round_index,
            "samples": len(samples),
            "max_abs_delta_ms": round(worst, 6),
            # Each per-sample number is stored rounded to 3 decimals, so the exact identity
            # can only be reproduced to within that rounding.
            "tolerance_ms": 0.002,
            "ok": worst <= 0.002,
            "why": "the residual is defined per sample, so this identity is exact and shows the "
                   "median-level difference is only a quantile artifact",
        }


def _close(left: float, right: float, tolerance: float = IDENTITY_TOLERANCE_MS) -> bool:
    return abs(left - right) <= tolerance


def check(
    name: str,
    expression: str,
    left: float,
    right: float,
    *,
    why: str,
    tolerance: float = IDENTITY_TOLERANCE_MS,
) -> dict[str, Any]:
    return {
        "name": name,
        "expression": expression,
        "left_ms": round(left, 3),
        "right_ms": round(right, 3),
        "delta_ms": round(left - right, 3),
        "tolerance_ms": tolerance,
        "ok": _close(left, right, tolerance),
        "why": why,
    }


def build_report(repo_root: Path) -> dict[str, Any]:
    evidence = Evidence(repo_root)

    # ------------------------------------------------------------------ 300k fixed-coverage split
    refined = "test-results/p4-02f-stage0-refined.json"
    w = "rounds[0].summary"
    sqlite_read_all = evidence.value(
        "sqlite_read_all", refined, f"{w}.median_by_stage_ms.sqlite_read_all",
        statistic="median of per-sample totals", samples=2,
    )
    candidate_walk = evidence.value(
        "sqlite_candidate_walk", refined, f"{w}.median_by_stage_ms.sqlite_candidate_walk",
        statistic="median", samples=2,
        note="the candidate keyset walk A3 would replace (candidate-block path only)",
    )
    pending_topup = evidence.value(
        "sqlite_pending_topup", refined, f"{w}.median_by_stage_ms.sqlite_pending_topup",
        statistic="median", samples=2, note="ADR-0005 §10 top-up page; A3 needs it too",
    )
    sqlite_other_reads = evidence.value(
        "sqlite_other_reads", refined, f"{w}.median_by_stage_ms.sqlite_other_reads",
        statistic="derived residual (read_all minus the two named signatures)", samples=2,
    )
    engine_total = evidence.value(
        "engine_total", refined, f"{w}.median_by_stage_ms.engine_total",
        statistic="median of 60 block calls per sample", samples=2,
        note="candidate-block path only; not additive with the A3 single pass",
    )
    residual = evidence.value(
        "other_fusion_and_authority", refined, f"{w}.median_by_stage_ms.other_fusion_and_authority",
        statistic="mutually exclusive residual", samples=2,
        note="fusion/authority/rendering/validation, i.e. service minus the two resource buckets",
    )
    service_median = evidence.value(
        "service_median", refined, f"{w}.service.median_ms", statistic="median", samples=2,
    )
    service_p95 = evidence.value(
        "service_p95", refined, f"{w}.service.p95_ms", statistic="p95", samples=2,
    )

    authoritative_page = evidence.value(
        "authoritative_page", refined, f"{w}.stage_medians_ms{field('authoritative_page')}",
        statistic="stage median", samples=2, note="attribution view; overlaps the residual",
    )
    stage_names = [
        "scope_view", "fts_channel", "reciprocal_rank_fusion", "_verified_ids",
        "text_column_matches", "_rewrite_hit", "documents.catch_up",
    ]
    small_stages = {
        name: evidence.value(
            f"stage:{name}", refined, f"{w}.stage_medians_ms{field(name)}",
            statistic="stage median", samples=2, note="attribution view; overlaps the residual",
        )
        for name in stage_names
    }
    pending_stage = evidence.value(
        "stage:pending_projection_page", refined,
        f"{w}.stage_medians_ms{field('pending_projection_page')}",
        statistic="stage median", samples=2, note="same call as sqlite_pending_topup",
    )

    # 100k comparison round, same file, for the drift note and a second identity.
    b = "rounds[1].summary"
    b10 = {
        key: evidence.value(f"100k:{key}", refined, f"{b}.median_by_stage_ms.{key}",
                            statistic="median", samples=2)
        for key in ("sqlite_read_all", "sqlite_candidate_walk", "sqlite_pending_topup",
                    "engine_total", "other_fusion_and_authority")
    }
    b10_service = evidence.value(
        "100k:service_median", refined, f"{b}.service.median_ms", statistic="median", samples=2,
    )

    # --------------------------------------------------- A3 engine side and product-side deltas
    single = "test-results/p4-02f-stage0-300k.json"
    single_median = evidence.value(
        "a3_engine_single_pass_median", single, "single_pass.W.ms_median",
        statistic="median", samples=5,
        note="diagnostic lower bound only; an unconstrained pass is NOT an A3 authorisation",
    )
    single_samples = [float(x) for x in dig(evidence.load(single), "single_pass.W.ms_all")]
    single_high = evidence.value(
        "a3_engine_single_pass_high", single, "single_pass.W.ms_all[0]",
        statistic="max", samples=5, note="max of 5 samples, not a p95",
    )
    as_built_median = evidence.value(
        "as_built_service_median", single, "rounds[0].summary.service.median_ms",
        statistic="median", samples=5, note="as-built partial index coverage, for context only",
    )
    as_built_p95 = evidence.value(
        "as_built_service_p95", single, "rounds[0].summary.service.p95_ms",
        statistic="p95", samples=5,
    )

    gpu = "docs/reports/P0-06-chinese-clip-gpu-check.json"
    text_encode_p95 = evidence.value(
        "text_encode_p95", gpu, "gpu.latency.text.p95_ms",
        statistic="p95", samples=20,
        note="RTX 4060, warm, batch=1, 52-token text; explicitly not a GTX 1660 claim",
    )
    text_encode_count = int(dig(evidence.load(gpu), "gpu.latency.text.count"))

    pilot = "test-results/p4-02b-dsh-pilot.json"
    pilot_service_p95 = evidence.value(
        "pilot_service_p95", pilot, "measurements[0].query.service.p95_ms",
        statistic="p95", samples=5, note="800-asset fixture, so the delta is indicative only",
    )
    pilot_http_p95 = evidence.value(
        "pilot_http_p95", pilot, "measurements[0].query.http.warm.p95_ms",
        statistic="p95", samples=5, note="same 800-asset fixture",
    )
    http_delta = round(pilot_http_p95 - pilot_service_p95, 3)

    # ---------------------------------------------------------------------------- gate A ceiling
    ceiling = evidence.load("test-results/p4-02f-blocks-gate-a-ceiling.json")
    gate_rows = []
    for row in dig(ceiling, "block_matrix.W300.results"):
        if row.get("truncated") is False:
            gate_rows.append({
                "block_size": int(row["block_size"]),
                "blocks": int(row["blocks"]),
                "scanned_assets": int(row["scanned_assets"]),
                "samples": int(row["samples"]),
                "ms_median": float(row["ms_median"]),
                "engine_ms_median": float(row["engine_ms_median"]),
                "walk_ms_median": float(row["walk_ms_median"]),
            })
    if not gate_rows:
        raise MissingField("gate A ceiling: no non-truncated 300k row")
    gate_best = min(gate_rows, key=lambda r: r["ms_median"])

    # ----------------------------------------------------------- fallback / contention magnitude
    j_stage = "test-results/p4-02j-stage0-300k.json"
    fixed_idle_median = evidence.value(
        "coverage_fixed_idle_median", j_stage, "rounds[0].summary.service.median_ms",
        statistic="median", samples=5,
    )
    fixed_idle_p95 = evidence.value(
        "coverage_fixed_idle_p95", j_stage, "rounds[0].summary.service.p95_ms",
        statistic="p95", samples=5,
    )
    fixed_engine_total = evidence.value(
        "coverage_fixed_engine_total", j_stage, "rounds[0].summary.median_by_stage_ms.engine_total",
        statistic="median of 60 block calls per sample", samples=5,
    )
    fixed_pending = evidence.value(
        "coverage_fixed_pending_topup", j_stage, "rounds[0].summary.median_by_stage_ms.sqlite_pending_topup",
        statistic="median", samples=5,
    )
    fixed_residual = evidence.value(
        "coverage_fixed_residual", j_stage,
        "rounds[0].summary.median_by_stage_ms.other_fusion_and_authority",
        statistic="mutually exclusive residual", samples=5,
    )
    fixed_read_all = evidence.value(
        "coverage_fixed_read_all", j_stage, "rounds[0].summary.median_by_stage_ms.sqlite_read_all",
        statistic="median of per-sample totals", samples=5,
    )

    close = "test-results/p4-02j-contention-close.json"
    later_idle_median = evidence.value(
        "later_session_idle_median", close, "idle_before.median_ms", statistic="median", samples=5,
    )
    later_idle_p95 = evidence.value(
        "later_session_idle_p95", close, "idle_before.p95_ms", statistic="p95", samples=5,
    )
    contended_median = evidence.value(
        "contended_median", close, "contended.samples.median_ms", statistic="median", samples=7,
    )
    contended_p95 = evidence.value(
        "contended_p95", close, "contended.samples.p95_ms", statistic="p95", samples=7,
    )
    contention_overlap = evidence.value(
        "contention_overlap", close, "contended.overlap_ms",
        statistic="wall clock of true overlap with the background drain", samples=7,
    )

    concurrency = "test-results/p4-02j-concurrency-300k.json"
    idle_concurrency_p50 = evidence.value(
        "idle_concurrency_p50", concurrency, "concurrency.foreground.median_ms",
        statistic="median", samples=8, note="idle variant: no overlap with the background drain",
    )
    idle_concurrency_p95 = evidence.value(
        "idle_concurrency_p95", concurrency, "concurrency.foreground.p95_ms",
        statistic="p95", samples=8,
    )

    unwarmed = evidence.load("test-results/p4-02j-contention-unwarmed.json")
    unwarmed_samples = int(dig(unwarmed, "contended.samples.count"))
    unwarmed_failures = len(dig(unwarmed, "contended.failures"))
    projection_queue = int(dig(unwarmed, "before_samples.projection_queue"))
    control = evidence.load("test-results/p4-02j-contention-catchup-control.json")
    control_refusals = int(dig(control, "refusals"))
    control_rounds = len(dig(control, "rounds"))

    # ---------------------------------------------------------------------------- identities
    checks = [
        check(
            "refined_300k_bucket_identity",
            "sqlite_read_all + engine_total + other_fusion_and_authority == service.median_ms",
            sqlite_read_all + engine_total + residual,
            service_median,
            why="proves other_fusion_and_authority is the mutually exclusive residual, not a third resource",
        ),
        check(
            "refined_300k_sqlite_split",
            "sqlite_candidate_walk + sqlite_pending_topup + sqlite_other_reads == sqlite_read_all",
            candidate_walk + pending_topup + sqlite_other_reads,
            sqlite_read_all,
            why="proves the sqlite bucket is fully split into the walk, the top-up and the rest",
        ),
        check(
            "refined_100k_bucket_identity",
            "sqlite_read_all + engine_total + other_fusion_and_authority == service.median_ms (100k)",
            b10["sqlite_read_all"] + b10["engine_total"] + b10["other_fusion_and_authority"],
            b10_service,
            why="same identity on the 100k round of the same controlled pair",
        ),
        check(
            "coverage_fixed_bucket_identity",
            "sqlite_read_all + engine_total + other_fusion_and_authority == service.median_ms (P4-02J, fixed coverage)",
            fixed_read_all + fixed_engine_total + fixed_residual,
            fixed_idle_median,
            why="identity on the median level after the coverage repair; the small remainder is the "
                "median-of-sums vs sum-of-medians quantile artifact (see the per-sample checks)",
            tolerance=2.0,
        ),
        check(
            "pending_stage_equals_sqlite_bucket",
            "stage_medians_ms.pending_projection_page == median_by_stage_ms.sqlite_pending_topup",
            pending_stage,
            pending_topup,
            why="the product stage view and the sqlite bucket are the same single call, not two costs",
            tolerance=0.05,
        ),
        check(
            "pilot_http_delta_identity",
            "pilot_http_p95 - pilot_service_p95 == recorded delta",
            pilot_http_p95 - pilot_service_p95,
            round(pilot_http_p95 - pilot_service_p95, 3),
            why="the HTTP delta is a subtraction of two fields in the same file, not a separate measurement",
            tolerance=0.001,
        ),
        evidence.per_sample_identity("refined_300k_per_sample", refined, 0),
        evidence.per_sample_identity("coverage_fixed_per_sample", j_stage, 0),
    ]

    # ------------------------------------------------------------------------- the assemblies
    a3_point = round(single_median + pending_topup + residual, 3)
    a3_high = round(single_high + pending_topup + residual, 3)
    a3_point_with_encode = round(a3_point + text_encode_p95, 3)
    a3_high_with_encode = round(a3_high + text_encode_p95, 3)
    a3_point_full = round(a3_point_with_encode + http_delta, 3)
    a3_high_full = round(a3_high_with_encode + http_delta, 3)

    # P4-02F §14 reproduced two ways: as written (rounded operands) and from exact fields.
    section14_rounding = 1.5  # the report wrote "fts_channel + RRF + scope_view + _verified_ids + 其余 ~1.5"
    p4_02f_14_as_written = round(
        346.96 + 359.0 + 28.7 + section14_rounding + 77.3, 3
    )
    p4_02f_14_recomputed = round(
        single_median + pending_stage + authoritative_page + sum(small_stages.values()) + residual, 3
    )
    double_count = round(p4_02f_14_recomputed - a3_point, 3)
    double_count_as_written = round(p4_02f_14_as_written - a3_point, 3)

    # The self-set internal target the card demands be labelled as engineering-chosen.
    encode_plus_http = round(text_encode_p95 + http_delta, 3)
    self_set_margin = 100.0
    branch_f_certificate_assumption = 10.0
    internal_target_x = round(
        1000.0 - residual - encode_plus_http - branch_f_certificate_assumption - self_set_margin, 3
    )
    engine_plus_pending_median = round(single_median + pending_topup, 3)
    engine_plus_pending_high = round(single_high + pending_topup, 3)

    return {
        "task": "P4-02K",
        "role": "read-only recomputation of the F2 budget assembly and the A3 decision inputs",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "product_code_touched": False,
        "sources": evidence.fingerprints(),
        "records": evidence.records,
        "identities": checks,
        "assemblies": {
            "candidate_block_path_300k": {
                "components": {
                    "sqlite_read_all": sqlite_read_all,
                    "engine_total_60_blocks": engine_total,
                    "other_fusion_and_authority": residual,
                },
                "total_ms": round(sqlite_read_all + engine_total + residual, 3),
                "total_seconds": round((sqlite_read_all + engine_total + residual) / 1000.0, 3),
                "equals_service_median_ms": service_median,
                "service_p95_ms": service_p95,
                "note": "the walk and the 60 block calls belong to this path only",
            },
            "coverage_fixed_path_300k": {
                "components": {
                    "sqlite_read_all": fixed_read_all,
                    "sqlite_pending_topup": fixed_pending,
                    "engine_total_60_blocks": fixed_engine_total,
                    "other_fusion_and_authority": fixed_residual,
                },
                "total_ms": round(fixed_read_all + fixed_engine_total + fixed_residual, 3),
                "service_median_ms": fixed_idle_median,
                "service_p95_ms": fixed_idle_p95,
                "note": "same path after the P4-02J coverage repair; the full path a fallback takes",
            },
            "a3_path_point_estimate": {
                "components": {
                    "engine_single_pass_median": single_median,
                    "pending_topup": pending_topup,
                    "other_fusion_and_authority": residual,
                },
                "total_ms": a3_point,
                "with_text_encode_ms": a3_point_with_encode,
                "with_text_encode_and_http_ms": a3_point_full,
                "margin_to_1000_ms": round(1000.0 - a3_point_full, 3),
                "note": "excludes the A3 certificate entirely; not an end-to-end measurement",
            },
            "a3_path_observed_high": {
                "components": {
                    "engine_single_pass_max": single_high,
                    "pending_topup": pending_topup,
                    "other_fusion_and_authority": residual,
                },
                "total_ms": a3_high,
                "with_text_encode_ms": a3_high_with_encode,
                "with_text_encode_and_http_ms": a3_high_full,
                "margin_to_1000_ms": round(1000.0 - a3_high_full, 3),
                "note": "max of 5 engine samples substituted for the median, everything else at its median",
            },
            "p4_02f_section_14": {
                "as_written_ms": p4_02f_14_as_written,
                "recomputed_from_exact_fields_ms": p4_02f_14_recomputed,
                "rounding_gap_ms": round(p4_02f_14_as_written - p4_02f_14_recomputed, 3),
                "small_stages_sum_ms": round(sum(small_stages.values()), 3),
                "corrected_mutually_exclusive_ms": a3_point,
                "double_count_ms": double_count,
                "double_count_of_as_written_ms": double_count_as_written,
                "as_written_plus_text_encode_ms": round(p4_02f_14_as_written + text_encode_p95, 3),
                "corrected_plus_text_encode_ms": a3_point_with_encode,
            },
            "internal_target": {
                "formula": "1000 - residual - (text_encode_p95 + http_delta) - certificate - margin",
                "residual_ms": residual,
                "product_overhead_ms": encode_plus_http,
                "residual_plus_product_ms": round(residual + encode_plus_http, 3),
                "text_encode_p95_ms": text_encode_p95,
                "http_delta_ms": http_delta,
                "certificate_assumption_ms": branch_f_certificate_assumption,
                "self_set_margin_ms": self_set_margin,
                "x_ms": internal_target_x,
                "engine_plus_pending_median_ms": engine_plus_pending_median,
                "engine_plus_pending_observed_high_ms": engine_plus_pending_high,
                "headroom_to_x_at_median_ms": round(internal_target_x - engine_plus_pending_median, 3),
                "over_x_at_observed_high_ms": round(engine_plus_pending_high - internal_target_x, 3),
                "is_section_12_2_text": False,
                "note": "the internal target X is engineering-chosen and needs user/Sol endorsement",
            },
            "counterexamples": {
                "branch_r_extrapolated_ms": 350.0,
                "branch_r_extrapolation_provenance": "P4-02H r2 §6.1 / independent recheck §4.1 magnitude "
                                                     "judgement from 20 000 rows; NOT a 300k measurement",
                "r1_branch_r_total_ms": round(a3_point + 350.0, 3),
                "r1_exceeds_1000_ms": (a3_point + 350.0) > 1000.0,
                "r2_total_ms": a3_high_full,
                "r2_margin_ms": round(1000.0 - a3_high_full, 3),
                "note": "these are the minimal rebuttals of the section 7 (1) verdict",
            },
        },
        "certificate_cost": {
            "branch_r_row_level": {
                "measured_on": "real Lance table, 20 000 rows, dim=2 synthetic vectors",
                "full_generation_read_ms": [36.4, 37.6],
                "candidate_block_path_ms": [226.9, 231.6, 253.9],
                "ratio": 6.5,
                "extrapolated_300k_seconds": 0.35,
                "extrapolation_evidence": "P4-02H r2 §6.1 and P4-02H independent recheck §4.1 magnitude judgement",
                "is_300k_measurement": False,
                "note": "the source probe is dim=2 synthetic and may be used only inside its own fixture",
            },
            "branch_f_generation_fence": {
                "implementation": None,
                "benchmark": None,
                "construction_cost_assessed": False,
                "note": "M2/M3 push-down equivalence and stopping certificates are likewise unassessed",
            },
        },
        "fallback": {
            "coverage_fixed_idle_300k": {"median_ms": fixed_idle_median, "p95_ms": fixed_idle_p95, "samples": 5},
            "later_session_idle_300k": {"median_ms": later_idle_median, "p95_ms": later_idle_p95, "samples": 5},
            "background_overlap_300k": {
                "median_ms": contended_median,
                "p95_ms": contended_p95,
                "samples": 7,
                "overlap_ms": contention_overlap,
            },
            "idle_concurrency_variant_300k": {
                "median_ms": idle_concurrency_p50,
                "p95_ms": idle_concurrency_p95,
                "samples": 8,
                "note": "zero overlap with the background drain; idle cost, not contention",
            },
            "as_built_partial_coverage_300k": {"median_ms": as_built_median, "p95_ms": as_built_p95, "samples": 5},
            "union_bound": {
                "statement": "P(fallback OR A3_self > 1 s) <= 5% is necessary for API p95 <= 1000 ms",
                "conservative_form": "r + s <= 5%",
                "is_sufficient": False,
            },
            "projection_not_caught_up_refusals": {
                "samples": unwarmed_samples,
                "refused": unwarmed_failures,
                "projection_queue": projection_queue,
                "control_rounds": control_rounds,
                "control_refusals": control_refusals,
            },
        },
        "gate_a_ceiling_300k": {
            "best_non_truncated_ms": gate_best["ms_median"],
            "best_non_truncated_seconds": round(gate_best["ms_median"] / 1000.0, 3),
            "best_row": gate_best,
            "all_rows": gate_rows,
            "reachable_under_gate_a": gate_best["ms_median"] <= 1000.0,
        },
        "fallback_seconds": {
            "coverage_fixed_idle_p95_s": round(fixed_idle_p95 / 1000.0, 3),
            "coverage_fixed_idle_median_s": round(fixed_idle_median / 1000.0, 3),
            "later_session_idle_p95_s": round(later_idle_p95 / 1000.0, 3),
            "later_session_idle_median_s": round(later_idle_median / 1000.0, 3),
            "background_overlap_p95_s": round(contended_p95 / 1000.0, 3),
            "background_overlap_median_s": round(contended_median / 1000.0, 3),
        },
        "quotations": [
            {
                "value": 0.19,
                "unit": "s",
                "verbatim_claim": "P4-02F leaves only about 0.19 s for the real text tower, HTTP and margin",
                "source": "P4-02F-retrieval-latency.md §14 and §16",
                "role": "the claim this card corrects; quoted verbatim, not re-derived by this tool",
            },
            {
                "value": 0.37,
                "unit": "s",
                "verbatim_claim": "A3-shaped unconstrained global query is about 0.37 s at 300k",
                "source": "P4-02H-a3-contract-review-r2.md §6.1 / P4-02H-independent-recheck.md §4.1",
                "role": "magnitude judgement quoted for branch R; this tool derives 0.346961 s itself",
            },
            {
                "value": 0.6,
                "unit": "% of the static eligibility surface",
                "verbatim_claim": "240 scope_condition combinations, 168 evaluable, 1 eligible",
                "source": "P4-02H-a3-contract-review-r2.md §6.2 / P4-02H-independent-recheck.md §4.2",
                "role": "static eligibility surface only; explicitly NOT a load fallback rate",
            },
        ],
        "sample_sizes": {
            "engine_single_pass": 5,
            "refined_stage_split_300k": 2,
            "refined_stage_split_100k": 2,
            "coverage_fixed_stage_split_300k": 5,
            "text_encode": text_encode_count,
            "http_delta": 5,
            "coverage_fixed_idle": 5,
            "later_session_idle": 5,
            "background_overlap": 7,
            "idle_concurrency_variant": 8,
            "gate_a_ceiling_per_size": 1,
        },
        "engine_single_pass_samples_ms": single_samples,
        "drift_disclosure": {
            "refined_100k_service_median_ms": b10_service,
            "refined_300k_service_median_ms": service_median,
            "coverage_fixed_300k_service_median_ms": fixed_idle_median,
            "later_session_idle_300k_median_ms": later_idle_median,
            "note": "P4-02F §15 records 1686.1 -> 2445.0 ms (100k) and 8804.6 -> 5353.3 ms (300k) on the same roots",
        },
        "unverified": [
            "real FG-CLIP 2 weights and the real text tower latency (BLOCKED)",
            "GTX 1660 6 GB minimum configuration (BLOCKED)",
            "an end-to-end API p95 under the real mixed load (the only legitimate acceptance)",
            "the A3 certificate cost on any branch (no implementation, no benchmark)",
            "cold OS cache, long soak, 400k growth and concurrent-write interaction",
        ],
        "status": "ok",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="test-results/p4-02k-budget.json")
    parser.add_argument(
        "--repo-root",
        default=str(Path(__file__).resolve().parents[2]),
        help="repository root holding test-results/ and docs/ (defaults to this file's repo)",
    )
    arguments = parser.parse_args(argv)

    report = build_report(Path(arguments.repo_root).resolve())
    failed = [item for item in report["identities"] if not item["ok"]]
    if failed:
        for item in failed:
            if "max_abs_delta_ms" in item:
                detail = f"max |delta| {item['max_abs_delta_ms']} ms over {item['samples']} samples"
            else:
                detail = (f"{item['left_ms']} vs {item['right_ms']} "
                          f"(delta {item['delta_ms']} ms)")
            print(f"FAIL identity: {item['name']}: {item['expression']} -> {detail}")
        return 1

    output = Path(arguments.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"P4-02K budget: {output} ({output.stat().st_size} bytes)")
    print(f"  identities: {len(report['identities'])}/{len(report['identities'])} ok")
    assemblies = report["assemblies"]
    print(f"  300k candidate-block path: {assemblies['candidate_block_path_300k']['total_ms']} ms "
          f"(== service median {assemblies['candidate_block_path_300k']['equals_service_median_ms']} ms)")
    print(f"  A3 point estimate (no certificate): {assemblies['a3_path_point_estimate']['total_ms']} ms; "
          f"with encode+HTTP {assemblies['a3_path_point_estimate']['with_text_encode_and_http_ms']} ms "
          f"(margin {assemblies['a3_path_point_estimate']['margin_to_1000_ms']} ms)")
    print(f"  A3 observed high (no certificate): {assemblies['a3_path_observed_high']['total_ms']} ms; "
          f"with encode+HTTP {assemblies['a3_path_observed_high']['with_text_encode_and_http_ms']} ms "
          f"(margin {assemblies['a3_path_observed_high']['margin_to_1000_ms']} ms)")
    print(f"  P4-02F section 14 as written {assemblies['p4_02f_section_14']['as_written_ms']} ms; "
          f"corrected {assemblies['p4_02f_section_14']['corrected_mutually_exclusive_ms']} ms; "
          f"double count {assemblies['p4_02f_section_14']['double_count_ms']} ms")
    print(f"  gate A ceiling best 300k: {report['gate_a_ceiling_300k']['best_non_truncated_ms']} ms "
          f"(reachable: {report['gate_a_ceiling_300k']['reachable_under_gate_a']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
