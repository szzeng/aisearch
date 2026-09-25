"""Executable drift checks for the ADR-0012 / P4-02G A3 contract companion.

This round is a **contract freeze only**: it adds no production code, no migration and no
search behaviour, and A3 is neither implemented nor enabled. Like the P4-00A contract test,
this file (a) validates the draft-2020-12 subset the checked-in companion actually uses,
(b) pins the cross-item rules JSON Schema cannot express (the eligibility predicate, the
certificate/stop rules and the frozen counterexample matrix), and (c) proves the decision
function falls back to the complete authoritative path whenever a complete Top-K cannot be
certified.

The reference model below is the ADR text in executable form; it is deliberately **not** an
A3 implementation and asserts nothing about production behaviour that does not exist yet.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from photo_search.search.retrieval import (
    SearchScope,
    VectorBinding,
    scope_condition,
    vector_channel,
)

ROOT = Path(__file__).resolve().parents[2]
COMPANION = ROOT / "docs" / "decisions" / "contracts" / "p4-a3-contracts.v1.json"
ADR = ROOT / "docs" / "decisions" / "ADR-0012-a3-global-candidate-contract.md"

A3_WITH_CERTIFICATE = "A3_WITH_CERTIFICATE"
FALLBACK = "FALLBACK_COMPLETE_PATH"

#: ADR-0012 §6 as executable rows: case -> (decision, fallback reason, engine state, authority state).
STATE_MATRIX: dict[str, tuple[str, str | None, str, str]] = {
    "unconfirmed_row": (
        FALLBACK,
        "UNCONFIRMED_ROW_IN_ENGINE",
        "engine row present after failpoint=after_lance",
        "entry not CONFIRMED (the publish path left it PENDING_REPAIR)",
    ),
    "pending_repair": (
        FALLBACK,
        "PENDING_REPAIR_ENTRY",
        "row present from an older artifact",
        "index_entries.state = PENDING_REPAIR",
    ),
    "stale_or_duplicate_revision": (
        FALLBACK,
        "STALE_OR_DUPLICATE_REVISION",
        "defensive: engine row revision differs from the confirmation revision",
        "same asset stays CONFIRMED (out-of-order write / corruption / external replace)",
    ),
    "missing_engine_row": (
        FALLBACK,
        "MISSING_ENGINE_ROW",
        "no row (or an older one) for this asset",
        "asset is CONFIRMED",
    ),
    "tied_distance": (
        FALLBACK,
        "NO_CERTIFICATE_PROVABLE",
        "K-th and (K+1)-th distances are equal",
        "both assets are CONFIRMED",
    ),
    "hidden_offline_or_source_change": (
        FALLBACK,
        "REVISION_OR_VISIBILITY_CHANGED_BEFORE_PUBLICATION",
        "row still present",
        "visibility/source changed during the window",
    ),
    "generation_or_space_switch": (
        FALLBACK,
        "BINDING_NOT_READY",
        "rows belong to another generation/space",
        "bound generation is no longer active READY",
    ),
    "concurrent_publication": (
        FALLBACK,
        "REVISION_OR_VISIBILITY_CHANGED_BEFORE_PUBLICATION",
        "row set changed by a concurrent confirmation",
        "confirmation/revision moved before publication",
    ),
    "place_state_filter": (
        FALLBACK,
        "NOT_ELIGIBLE_EXTRA_SCOPE",
        "no place-id condition at all",
        "place_state is assigned/unassigned/manual_unassigned/suggested",
    ),
    "extra_engine_row_with_m3_certificate": (
        A3_WITH_CERTIFICATE,
        None,
        "engine also holds an unconfirmed row A (nearer) beside the confirmed row B",
        "C_sql = C_strict = {B}",
    ),
    "positive_control_certificate": (
        A3_WITH_CERTIFICATE,
        None,
        "every possible row matches C exactly",
        "C stable across the query-publication window",
    ),
}

#: The five classes the task card names explicitly, mapped to their frozen matrix rows.
CARD_COUNTEREXAMPLES = {
    "unconfirmed_row": "unconfirmed_row",
    "repair": "pending_repair",
    "old_revision": "stale_or_duplicate_revision",
    "tied_distance": "tied_distance",
    "concurrent_publication": "concurrent_publication",
}

FORBIDDEN_MECHANISMS = (
    "FIXED_OVERFETCH_MULTIPLIER",
    "FIXED_RETRY_COUNT",
    "GLOBAL_TOP_K_THEN_FILTER",
    "COUNT_ONLY_EQUALITY",
    "SAMPLING_ONLY_EQUALITY",
    "INDEX_EXISTENCE_AS_QUALIFICATION",
    "ENGINE_TRUNCATION_BEFORE_TOTAL_ORDER",
)


# --------------------------------------------------------------------- companion subset validator
def _resolve(schema: dict[str, Any], root: dict[str, Any]) -> dict[str, Any]:
    if "$ref" not in schema:
        return schema
    pointer = str(schema["$ref"])
    assert pointer.startswith("#/"), pointer
    node: Any = root
    for part in pointer[2:].split("/"):
        node = node[part]
    return node


def _errors(instance: Any, schema: dict[str, Any], root: dict[str, Any], path: str = "$") -> list[str]:
    schema = _resolve(schema, root)
    problems: list[str] = []
    if "oneOf" in schema:
        matched = 0
        for option in schema["oneOf"]:
            if not _errors(instance, option, root, path):
                matched += 1
        if matched != 1:
            problems.append(f"{path}: oneOf matched {matched} branches")
        return problems
    if "const" in schema and instance != schema["const"]:
        problems.append(f"{path}: {instance!r} != const {schema['const']!r}")
    if "enum" in schema and instance not in schema["enum"]:
        problems.append(f"{path}: {instance!r} not in enum")
    expected = schema.get("type")
    if expected == "null":
        return [] if instance is None else [f"{path}: not null"]
    if expected == "object" and not isinstance(instance, dict):
        return [f"{path}: not an object"]
    if expected == "integer" and not (isinstance(instance, int) and not isinstance(instance, bool)):
        return [f"{path}: not an integer"]
    if expected == "string" and not isinstance(instance, str):
        return [f"{path}: not a string"]
    if isinstance(instance, dict):
        for name in schema.get("required", []):
            if name not in instance:
                problems.append(f"{path}: missing required {name}")
        if schema.get("additionalProperties") is False:
            for name in instance:
                if name not in schema.get("properties", {}):
                    problems.append(f"{path}: unexpected property {name}")
        for name, child in schema.get("properties", {}).items():
            if name in instance:
                problems.extend(_errors(instance[name], child, root, f"{path}.{name}"))
    if isinstance(instance, int) and not isinstance(instance, bool):
        if "minimum" in schema and instance < int(schema["minimum"]):
            problems.append(f"{path}: below minimum")
        if "maximum" in schema and instance > int(schema["maximum"]):
            problems.append(f"{path}: above maximum")
    if isinstance(instance, str) and "minLength" in schema and len(instance) < int(schema["minLength"]):
        problems.append(f"{path}: shorter than minLength")
    return problems


@pytest.fixture(scope="module")
def companion() -> dict[str, Any]:
    return json.loads(COMPANION.read_text(encoding="utf-8"))


def contract_errors(instance: Any, definition: str, companion: dict[str, Any]) -> list[str]:
    return _errors(instance, companion["$defs"][definition], companion)


# --------------------------------------------------------------------- ADR reference model
def eligibility(**overrides: Any) -> dict[str, Any]:
    """ADR-0012 §2: the all-true eligibility record."""
    record = {
        "full_vector_binding": True,
        "semantic_query": True,
        "source_id_is_null": True,
        "availability_is_any": True,
        "place_ids_is_empty": True,
        "place_state_is_any": True,
        "person_ids_is_empty": True,
        "capture_is_null": True,
        "generation_state_ready": True,
        "space_and_dimension_match": True,
    }
    record.update(overrides)
    return record


def authority_set() -> dict[str, bool]:
    return {
        "oracle_membership_matches_confirmed_block_sql": True,
        "entry_state_confirmed": True,
        "strict_requires_one_row_per_generation_asset": True,
        "strict_requires_row_revision_equals_confirmation_revision": True,
        "strict_is_a_subset_of_oracle_membership": True,
        "mismatch_forces_fallback": True,
        "lance_visible_column_is_not_a_credential": True,
    }


def decide(
    *,
    eligible: dict[str, Any],
    certificate: dict[str, Any] | None,
    case: str,
    pushdown_proven: bool = False,
    disqualification_provable: bool = True,
) -> tuple[str, str | None]:
    """ADR-0012 §2/§4/§6: the only way to publish with A3 is an evidenced, still-valid certificate."""
    if not all(eligible.values()):
        return FALLBACK, "NOT_ELIGIBLE_EXTRA_SCOPE"
    if certificate is None or certificate.get("holds_at_publication") is not True:
        return FALLBACK, "NO_CERTIFICATE_PROVABLE"
    paired = GATE_BY_MECHANISM.get(str(certificate.get("mechanism")))
    if paired != certificate.get("engine_side_gate"):
        return FALLBACK, "NO_CERTIFICATE_PROVABLE"
    # r5 F-B: the state decides which mechanisms are admissible at all; a mechanism the row
    # does not admit (M1 on an extra-row state, anything on a defensive state) falls back.
    mechanism = str(certificate.get("mechanism"))
    if mechanism not in MECHANISM_AVAILABILITY[case]:
        return FALLBACK, "NO_CERTIFICATE_PROVABLE"
    # r6/O-3 + F-D: the mechanism's own evidence must actually be proven *and* still hold, so a
    # candidate M2 without a proven pushdown predicate (or M3 without a provable
    # disqualification) reaches the final fallback decision, not just the local gate check.
    if mechanism == "M2_PUSHDOWN_EQUIVALENCE" and not pushdown_proven:
        return FALLBACK, "NO_CERTIFICATE_PROVABLE"
    if mechanism == "M3_STOP_CERTIFICATE" and not disqualification_provable:
        return FALLBACK, "NO_CERTIFICATE_PROVABLE"
    # The row admits this mechanism *and* every invariant holds, so A3 may publish; the page
    # obligation (equal to the oracle) is asserted by matrix_row()/the publication tests.
    return A3_WITH_CERTIFICATE, None


def matrix_row(case: str) -> dict[str, Any]:
    decision, reason, engine_state, authority_state = STATE_MATRIX[case]
    return {
        "case": case,
        "engine_state": engine_state,
        "authority_state": authority_state,
        "decision": decision,
        "fallback_reason": reason,
        "published_page_equals_complete_path": True,
    }


def request(
    *,
    case: str,
    eligible: dict[str, Any] | None = None,
    certificate: dict[str, Any] | None = None,
    top_k: int = 20,
) -> dict[str, Any]:
    decision, reason = decide(
        eligible=eligible or eligibility(),
        certificate=certificate,
        case=case,
    )
    return {
        "contract_version": "p4-a3-contracts.v1",
        "rules_version": 1,
        "eligibility": eligible or eligibility(),
        "authority_set": authority_set(),
        "certificate": certificate,
        "top_k": top_k,
        "order_key": ["distance", "asset_id"],
        "decision": decision,
        "fallback_reason": reason,
    }


CERTIFICATE = {
    "mechanism": "M1_EQUIVALENT_SNAPSHOT",
    "engine_side_gate": "M1_PHYSICAL_SET_EQUALS_C_STRICT",
    "holds_at_publication": True,
    "evidence": "writer fence id + id/revision set equality inside the publication window",
}

#: ADR-0012 §3.3: the engine-side gate is per mechanism (Sol r3). Equality of the whole
#: physical row set is M1's requirement only; M3 deliberately allows extra engine rows.
GATE_BY_MECHANISM = {
    "M1_EQUIVALENT_SNAPSHOT": "M1_PHYSICAL_SET_EQUALS_C_STRICT",
    "M2_PUSHDOWN_EQUIVALENCE": "M2_PUSHDOWN_RESTRICTED_SET_EQUALS_C_STRICT",
    "M3_STOP_CERTIFICATE": "M3_STOP_CERTIFICATE_OVER_ENGINE_STREAM",
}

#: ADR-0012 §3.3/§4/§6 as executable availability: which mechanisms the *state* admits at all
#: (r5 F-B: the availability is a property of the row, not of a baked decision). Rows that admit
#: no mechanism must fall back unconditionally.
MECHANISM_AVAILABILITY: dict[str, frozenset[str]] = {
    "unconfirmed_row": frozenset({"M2_PUSHDOWN_EQUIVALENCE", "M3_STOP_CERTIFICATE"}),
    "pending_repair": frozenset({"M2_PUSHDOWN_EQUIVALENCE", "M3_STOP_CERTIFICATE"}),
    "stale_or_duplicate_revision": frozenset(),
    "missing_engine_row": frozenset(),
    "tied_distance": frozenset(),
    "hidden_offline_or_source_change": frozenset(),
    "generation_or_space_switch": frozenset(),
    "concurrent_publication": frozenset(),
    "place_state_filter": frozenset(),
    # Sol r6: M2 is admissible on extra-row states too, but only with a *proven* pushdown
    # predicate -- which this row's example explicitly does not have (evidence, not the row,
    # decides), so the decision table below still marks its M2 branch as fallback.
    "extra_engine_row_with_m3_certificate": frozenset(
        {"M2_PUSHDOWN_EQUIVALENCE", "M3_STOP_CERTIFICATE"}
    ),
    "positive_control_certificate": frozenset(
        {"M1_EQUIVALENT_SNAPSHOT", "M2_PUSHDOWN_EQUIVALENCE", "M3_STOP_CERTIFICATE"}
    ),
}


# --------------------------------------------------------------------- companion consistency
def test_companion_is_valid_json_schema_subset(companion: dict[str, Any]) -> None:
    assert companion["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert companion["$defs"]["ContractVersion"]["const"] == "p4-a3-contracts.v1"
    assert companion["$defs"]["OrderKey"]["const"] == ["distance", "asset_id"]
    assert companion["$defs"]["MaxTopK"]["const"] == 1000
    assert "NOT implemented" in companion["description"]


def test_every_frozen_matrix_case_is_decided_and_covers_the_companion_enum(
    companion: dict[str, Any],
) -> None:
    enum_cases = set(companion["$defs"]["StateMatrixRow"]["properties"]["case"]["enum"])
    assert enum_cases == set(STATE_MATRIX)
    for case in sorted(STATE_MATRIX):
        assert contract_errors(matrix_row(case), "StateMatrixRow", companion) == []


def test_companion_rejects_an_unforbidden_certificate_and_bad_top_k(
    companion: dict[str, Any],
) -> None:
    assert contract_errors(request(case="positive_control_certificate", certificate=CERTIFICATE), "A3Request", companion) == []
    for mechanism in FORBIDDEN_MECHANISMS:
        bad = request(
            case="positive_control_certificate",
            certificate={"mechanism": mechanism, "holds_at_publication": True, "evidence": "x"},
        )
        assert contract_errors(bad, "A3Request", companion), mechanism
    assert contract_errors(request(case="positive_control_certificate", certificate=CERTIFICATE, top_k=1001), "A3Request", companion)
    assert contract_errors(request(case="positive_control_certificate", certificate=CERTIFICATE, top_k=0), "A3Request", companion)


# --------------------------------------------------------------------- the frozen counterexamples
def test_the_cards_five_counterexamples_all_fall_back(companion: dict[str, Any]) -> None:
    for name, case in CARD_COUNTEREXAMPLES.items():
        decision, reason = decide(eligible=eligibility(), certificate=None, case=case)
        assert decision == FALLBACK, name
        assert reason is not None, name
        record = request(case=case, certificate=None)
        assert record["decision"] == FALLBACK, name
        assert contract_errors(record, "A3Request", companion) == [], name


def test_a_valid_certificate_is_the_only_way_to_publish_with_a3() -> None:
    decision, reason = decide(
        eligible=eligibility(),
        certificate=CERTIFICATE,
        case="positive_control_certificate",
    )
    assert (decision, reason) == (A3_WITH_CERTIFICATE, None)
    expired = dict(CERTIFICATE, holds_at_publication=False)
    assert decide(eligible=eligibility(), certificate=expired, case="positive_control_certificate") == (
        FALLBACK,
        "NO_CERTIFICATE_PROVABLE",
    )


def test_ineligible_requests_never_reach_a3() -> None:
    for field in sorted(eligibility()):
        decision, reason = decide(
            eligible=eligibility(**{field: False}),
            certificate=CERTIFICATE,
            case="positive_control_certificate",
        )
        assert decision == FALLBACK, field
        assert reason == "NOT_ELIGIBLE_EXTRA_SCOPE", field


def test_tied_distances_use_the_global_total_order_and_never_engine_truncation() -> None:
    rows = [(1.5, 7), (1.5, 3), (1.5, 11)]
    ordered = sorted(rows, key=lambda item: (item[0], item[1]))
    assert ordered == [(1.5, 3), (1.5, 7), (1.5, 11)]
    # A row that the engine dropped before the total order can never be recovered by a
    # post-hoc SQLite re-read, so the tie case is a fallback, not an "A3 plus filter".
    assert STATE_MATRIX["tied_distance"][0] == FALLBACK


def test_full_top_k_cannot_be_proven_without_a_certificate() -> None:
    """ADR-0012 §7: no certificate, no A3 page - the fallback page must equal the complete path."""
    for case in sorted(STATE_MATRIX):
        decision, reason = decide(eligible=eligibility(), certificate=None, case=case)
        assert decision == FALLBACK
        assert reason is not None
        row = matrix_row(case)
        assert row["published_page_equals_complete_path"] is True


def test_adr_records_the_rounds_boundaries() -> None:
    text = ADR.read_text(encoding="utf-8")
    assert "Accepted（契约冻结）" in text
    assert "本轮不实现、不启用 A3" in text
    assert "不得放宽 §12.2" in text
    assert "未达标" in text and "尚未证明等价" in text
    for mechanism in FORBIDDEN_MECHANISMS:
        assert mechanism not in text  # the enum lives in the companion, not duplicated in the ADR
    assert "固定倍数 overfetch" in text and "全局 Top-K 后过滤" in text


# --------------------------------------------------- real-code counterexamples (Sol F1 / F2)
def test_real_place_state_makes_the_scope_predicate_non_tautological() -> None:
    """Sol F1: ``place_state`` is independent of ``place_ids``.

    The eligibility predicate may only treat an empty ``place_ids`` as "no extra filter" when
    the *real* predicate is tautological, so this calls the product's own ``scope_condition``
    instead of trusting the contract's own reference model.
    """
    connection = sqlite3.connect(":memory:")
    try:
        tautological = [
            state
            for state in ("any", "assigned", "unassigned", "manual_unassigned", "suggested")
            if scope_condition(connection, SearchScope.build(place_state=state))[0] == "1"
        ]
    finally:
        connection.close()
    assert tautological == ["any"]
    # ... and the frozen matrix therefore refuses A3 for every non-"any" place_state.
    assert STATE_MATRIX["place_state_filter"][0] == FALLBACK
    assert eligibility(place_state_is_any=False)["place_state_is_any"] is False
    assert decide(
        eligible=eligibility(place_state_is_any=False),
        certificate=CERTIFICATE,
        case="positive_control_certificate",
    ) == (FALLBACK, "NOT_ELIGIBLE_EXTRA_SCOPE")


def test_real_oracle_computes_c_sql_and_c_strict_for_both_window_shapes() -> None:
    """Sol short re-review F2: compute ``C_sql``/``C_strict`` for both window shapes.

    (1) the **defensive** shape (a ``CONFIRMED`` entry whose engine row carries another
    revision) is *not* what ``after_lance`` produces -- the publish path leaves the entry
    ``PENDING_REPAIR``, so that asset is in neither set and ``C_sql == C_strict``;
    (2) the real window is therefore "engine has a row the oracle does not", where the
    the *M1* gate fails on the extra row (row-set equality), while M2 would need a proven
    pushdown predicate and M3 would need a stop certificate - even though the two sets are equal.
    In both shapes the *final* decision is a fallback, but for per-mechanism reasons: M1 fails
    on the row-set equality, M2 would need a proven pushdown predicate, and M3 would need a
    stop certificate; the real ``vector_channel`` proves the oracle is revision-blind.
    """
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    try:
        connection.executescript(
            "CREATE TABLE assets(asset_id INTEGER PRIMARY KEY, state TEXT);"
            "CREATE TABLE index_entries(asset_id INTEGER, generation_id TEXT, space_id TEXT, "
            "state TEXT);"
            "INSERT INTO assets(asset_id, state) VALUES (1, 'ACTIVE'), (2, 'ACTIVE');"
            "INSERT INTO index_entries VALUES "
            "(1, 'gen', 'space', 'CONFIRMED'), (2, 'gen', 'space', 'CONFIRMED');"
        )

        class _MemoryCatalog:
            def __init__(self, connection: sqlite3.Connection) -> None:
                self.connection = connection

            def read_all(self, sql: str, parameters: tuple = ()) -> list[Any]:
                return self.connection.execute(sql, tuple(parameters)).fetchall()

        class _StubIndex:
            def __init__(self, rows: list[dict[str, Any]]) -> None:
                self.rows = rows

            def search_candidates(self, **kwargs: Any) -> list[dict[str, Any]]:
                ids = {int(value) for value in kwargs["asset_ids"]}
                return [dict(row) for row in self.rows if int(row["asset_id"]) in ids]

        index = _StubIndex(
            [
                {"asset_id": 1, "distance": 0.1, "projection_revision": 2, "space_id": "space"},
                {"asset_id": 2, "distance": 0.2, "projection_revision": 1, "space_id": "space"},
            ]
        )
        engine_rows = {1: 2, 2: 1}
        entry_state = {1: "CONFIRMED", 2: "CONFIRMED"}
        confirmed_revision = {1: 1, 2: 1}
        binding = VectorBinding(
            generation_id="gen",
            space_id="space",
            dimension=8,
            processor_id="stand-in",
            state="READY",
            is_active=True,
        )
        block = vector_channel(
            _MemoryCatalog(connection),
            index=index,  # type: ignore[arg-type]
            binding=binding,
            query_vector=[0.0] * 8,
            scope=SearchScope.build(),
            top_k=1,
        )
    finally:
        connection.close()
    assert [int(row["asset_id"]) for row in block["hits"]] == [1]
    assert int(block["hits"][0]["projection_revision"]) == 2

    def sets(*, engine: dict[int, int], states: dict[int, str], revisions: dict[int, int]) -> tuple[set[int], set[int]]:
        c_sql = {asset for asset, state in states.items() if state == "CONFIRMED"}
        c_strict = {
            asset
            for asset in c_sql
            if engine.get(asset) is not None and engine[asset] == revisions[asset]
        }
        return c_sql, c_strict

    # (1) defensive shape: the confirmed entry's engine row carries a different revision.
    c_sql, c_strict = sets(engine=engine_rows, states=entry_state, revisions=confirmed_revision)
    assert (c_sql, c_strict) == ({1, 2}, {2})
    assert STATE_MATRIX["stale_or_duplicate_revision"][0] == FALLBACK

    # (2) the real after_lance shape: the entry is PENDING_REPAIR while the engine already
    # holds a row, so the asset is in neither set and the two sets are EQUAL.
    real_engine = {1: 1, 2: 1, 3: 1}  # asset 3's new row is already written
    c_sql, c_strict = sets(
        engine=real_engine,
        states={**entry_state, 3: "PENDING_REPAIR"},
        revisions={**confirmed_revision, 3: 1},
    )
    assert c_sql == c_strict == {1, 2}
    # The extra engine row fails M1's row-set equality; M2 needs a proven pushdown predicate,
    # and M3 must cover that row with a stop certificate (ADR-0012 §3.3) - not one shared gate.
    assert set(real_engine) - c_strict == {3}
    assert STATE_MATRIX["unconfirmed_row"][0] == FALLBACK
    assert matrix_row("unconfirmed_row")["published_page_equals_complete_path"] is True


def test_gate_is_per_mechanism_and_m3_covers_the_extra_engine_row_state(
    companion: dict[str, Any],
) -> None:
    """Sol r3: the engine-side gate must be per mechanism, not one global row-set equality.

    Minimal counterexample: one generation/space, SQLite confirms only B, the engine also
    holds an unconfirmed and *nearer* A, ``top_k=1``. M3 may publish B with a stop
    certificate over the engine row stream; M1 cannot (extra row) and M2 needs a proven
    pushdown predicate. The published page must equal the oracle page ``{B}``.
    """
    enum_gates = set(companion["$defs"]["EngineSideGate"]["enum"])
    assert enum_gates == set(GATE_BY_MECHANISM.values())
    assert len(companion["$defs"]["CertificateRule"]["oneOf"]) == 3
    for variant in ("M1Certificate", "M2Certificate", "M3Certificate"):
        assert companion["$defs"][variant]["required"] == [
            "mechanism",
            "engine_side_gate",
            "holds_at_publication",
            "evidence",
        ]

    c_sql = {"B"}
    c_strict = {"B"}

    physical = {"A", "B"}  # the whole engine row set of this generation/space
    # What an (independent) proven pushdown predicate would return. r7/O-3: this is a parameter,
    # so a wrong filter result must fail the M2 gate instead of passing on the fixture alone.
    filtered_row_sets = {"correct": {"B"}, "wrong": {"A"}}

    inspected = [("A", 0.10, False), ("B", 0.20, True)]  # (asset, distance, qualified)

    def gate_holds(
        mechanism: str,
        *,
        pushdown_proven: bool,
        certificate: bool,
        uninspected_distance_lower_bound: float = 0.5,
        disqualification_provable: bool = True,
        filtered: str = "correct",
    ) -> bool:
        """Reference model of §3.3: one gate per mechanism, nothing global beyond §7."""
        if not certificate:
            return False
        if mechanism == "M1_EQUIVALENT_SNAPSHOT":
            return physical == c_strict  # no extra row allowed
        if mechanism == "M2_PUSHDOWN_EQUIVALENCE":
            # r6/r7 O-3: M2 must compare the *filtered* row set with C_strict, not just claim a proof.
            return pushdown_proven and filtered_row_sets[filtered] == c_strict == c_sql
        qualified = sorted([row for row in inspected if row[2]], key=lambda row: (row[1], row[0]))
        kth = qualified[:1]
        if not kth:
            return False
        # Rejected-but-inspected rows never block (the oracle excludes them too). The
        # certificate is about the rows M3 has *not* read: their documented distance lower
        # bound must be worse than the K-th qualified row, or M3 must keep expanding.
        # r5 F-D: the K-th qualified row may only stand if the *inspected and rejected* rows
        # around it are disqualified provably (and stay so at publication).
        if not disqualification_provable:
            return False
        return uninspected_distance_lower_bound > kth[0][1]

    # M1 fails (physical {A, B} != C_strict {B}); M2 has no proven pushdown predicate for
    # this shape; M3 holds and publishes exactly the oracle page {B}. Without a certificate
    # every mechanism falls back.
    assert gate_holds("M1_EQUIVALENT_SNAPSHOT", pushdown_proven=False, certificate=True) is False
    assert gate_holds("M2_PUSHDOWN_EQUIVALENCE", pushdown_proven=False, certificate=True) is False
    assert gate_holds("M2_PUSHDOWN_EQUIVALENCE", pushdown_proven=True, certificate=True) is True
    assert (
        gate_holds(
            "M2_PUSHDOWN_EQUIVALENCE",
            pushdown_proven=True,
            certificate=True,
            filtered="wrong",
        )
        is False
    )
    assert gate_holds("M3_STOP_CERTIFICATE", pushdown_proven=False, certificate=True) is True
    assert (
        gate_holds(
            "M3_STOP_CERTIFICATE",
            pushdown_proven=False,
            certificate=True,
            disqualification_provable=False,
        )
        is False
    )
    assert (
        gate_holds(
            "M3_STOP_CERTIFICATE",
            pushdown_proven=False,
            certificate=True,
            uninspected_distance_lower_bound=0.05,
        )
        is False
    )
    assert gate_holds("M3_STOP_CERTIFICATE", pushdown_proven=False, certificate=False) is False
    # The nearer A does not block because it was *inspected* and disqualified by the same
    # authoritative view: its disqualification basis (A is not in C_sql) is provable.
    assert "A" not in c_sql and "A" not in c_strict
    assert STATE_MATRIX["extra_engine_row_with_m3_certificate"][0] == A3_WITH_CERTIFICATE
    assert matrix_row("extra_engine_row_with_m3_certificate")["published_page_equals_complete_path"]
    assert [row[0] for row in sorted(inspected, key=lambda r: (r[1], r[0])) if row[2]] == ["B"]


def test_companion_binds_each_mechanism_to_exactly_one_engine_side_gate(
    companion: dict[str, Any],
) -> None:
    """Sol r4 blocker 2: a certificate must not be able to pair M1 with M3's gate."""
    gates = list(GATE_BY_MECHANISM.values())
    for mechanism, gate in GATE_BY_MECHANISM.items():
        record = request(
            case="positive_control_certificate",
            certificate={
                "mechanism": mechanism,
                "engine_side_gate": gate,
                "holds_at_publication": True,
                "evidence": "matched pairing",
            },
        )
        assert contract_errors(record, "A3Request", companion) == [], mechanism
    bad = request(
        case="positive_control_certificate",
        certificate={
            "mechanism": "M1_EQUIVALENT_SNAPSHOT",
            "engine_side_gate": "M3_STOP_CERTIFICATE_OVER_ENGINE_STREAM",
            "holds_at_publication": True,
            "evidence": "cross pairing",
        },
    )
    assert contract_errors(bad, "A3Request", companion)
    assert decide(
        eligible=eligibility(),
        certificate={
            "mechanism": "M1_EQUIVALENT_SNAPSHOT",
            "engine_side_gate": "M3_STOP_CERTIFICATE_OVER_ENGINE_STREAM",
            "holds_at_publication": True,
        },
        case="positive_control_certificate",
    ) == (FALLBACK, "NO_CERTIFICATE_PROVABLE")
    # every cross pairing must be rejected by the schema, not only by the reference model
    for mechanism, gate in GATE_BY_MECHANISM.items():
        for other_gate in gates:
            if other_gate == gate:
                continue
            crossed = request(
                case="positive_control_certificate",
                certificate={
                    "mechanism": mechanism,
                    "engine_side_gate": other_gate,
                    "holds_at_publication": True,
                    "evidence": "cross pairing",
                },
            )
            assert contract_errors(crossed, "A3Request", companion), (mechanism, other_gate)


def test_mechanism_availability_comes_from_the_state_not_a_baked_decision() -> None:
    """r5 F-B: the row's state (not a pre-baked decision) decides which mechanisms apply."""
    assert set(MECHANISM_AVAILABILITY) == set(STATE_MATRIX)
    m3 = {
        "mechanism": "M3_STOP_CERTIFICATE",
        "engine_side_gate": "M3_STOP_CERTIFICATE_OVER_ENGINE_STREAM",
        "holds_at_publication": True,
        "evidence": "stop certificate over the engine row stream",
    }
    # M1 is NOT admitted on the extra-row state, even with a well-formed M1 certificate.
    assert decide(
        eligible=eligibility(), certificate=dict(CERTIFICATE), case="extra_engine_row_with_m3_certificate"
    ) == (FALLBACK, "NO_CERTIFICATE_PROVABLE")
    # M3 IS admitted there, and on the real window rows (1/2), with its own certificate.
    for case in ("extra_engine_row_with_m3_certificate", "unconfirmed_row", "pending_repair"):
        assert decide(eligible=eligibility(), certificate=m3, case=case) == (A3_WITH_CERTIFICATE, None)
    # ... while the defensive/other rows admit nothing, even with a well-formed M3 certificate.
    for case in (
        "stale_or_duplicate_revision",
        "missing_engine_row",
        "tied_distance",
        "hidden_offline_or_source_change",
        "generation_or_space_switch",
        "concurrent_publication",
    ):
        assert decide(eligible=eligibility(), certificate=m3, case=case) == (
            FALLBACK,
            "NO_CERTIFICATE_PROVABLE",
        )


def test_evidence_binding_and_decision_certificate_binding(companion: dict[str, Any]) -> None:
    """r6 O-1/O-2 + F-D: every certificate carries evidence, and A3 needs a real certificate."""
    # O-1: evidence is required on all three mechanism variants.
    for mechanism, gate in GATE_BY_MECHANISM.items():
        without_evidence = request(
            case="positive_control_certificate",
            certificate={
                "mechanism": mechanism,
                "engine_side_gate": gate,
                "holds_at_publication": True,
            },
        )
        assert contract_errors(without_evidence, "A3Request", companion), mechanism
    # O-2: an A3 decision without a non-null certificate is rejected by the machine contract.
    publish_without_certificate = {
        **request(case="positive_control_certificate", certificate=CERTIFICATE),
        "certificate": None,
    }
    assert contract_errors(publish_without_certificate, "A3Request", companion)
    # ... and a fallback record must name its reason (no silent fallback).
    silent_fallback = {
        **request(case="positive_control_certificate", certificate=None),
        "fallback_reason": None,
    }
    assert contract_errors(silent_fallback, "A3Request", companion)
    # F-D: an unprovable disqualification reaches the *decision*, not only the local gate.
    m3 = {
        "mechanism": "M3_STOP_CERTIFICATE",
        "engine_side_gate": "M3_STOP_CERTIFICATE_OVER_ENGINE_STREAM",
        "holds_at_publication": True,
        "evidence": "stop certificate over the engine row stream",
    }
    assert decide(
        eligible=eligibility(),
        certificate=m3,
        case="extra_engine_row_with_m3_certificate",
        disqualification_provable=False,
    ) == (FALLBACK, "NO_CERTIFICATE_PROVABLE")
    # r6 F-A residual: the same M2 evidence must be admissible on every extra-row row.
    m2 = {
        "mechanism": "M2_PUSHDOWN_EQUIVALENCE",
        "engine_side_gate": "M2_PUSHDOWN_RESTRICTED_SET_EQUALS_C_STRICT",
        "holds_at_publication": True,
        "evidence": "proven pushdown predicate restricted to C_strict",
    }
    for case in ("unconfirmed_row", "pending_repair", "extra_engine_row_with_m3_certificate"):
        assert decide(
            eligible=eligibility(), certificate=m2, case=case, pushdown_proven=True
        ) == (A3_WITH_CERTIFICATE, None), case
        assert decide(eligible=eligibility(), certificate=m2, case=case) == (
            FALLBACK,
            "NO_CERTIFICATE_PROVABLE",
        ), case
