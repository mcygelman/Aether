"""ARA-09C C1 — adversarial tests for the canonical context contract.

Stdlib unittest only
(pytest-compatible: every test is a plain method on a TestCase subclass).

Coverage:
  * canonical serialization determinism and stable digests;
  * atom / tombstone / budget / manifest schema validation;
  * truth/lifecycle conflict rules;
  * dependency closure and cycle detection;
  * omission-reason vocabulary;
  * budget-fit enforcement;
  * adversarial fixtures: stale+current fact pair, refuted path with
    tombstone, protected-field mutation, non-canonical payload, duplicate
    atom IDs, negative budget.

Run:  python -m unittest test_context_atom_contract -v
   or: python -m pytest test_context_atom_contract.py -v
"""

from __future__ import annotations

import unittest
from dataclasses import replace

from aether_bridge.context_atom_contract import (
    CONTRACT_VERSION,
    CONTEXT_CLASSES,
    LIFECYCLE_STATES,
    OMISSION_REASONS,
    TRUTH_STATUSES,
    BudgetLedger,
    CanonicalError,
    ContextAtom,
    ContextManifest,
    OmissionRecord,
    Tombstone,
    ValidationFailure,
    build_manifest,
    canonical_digest,
    canonical_serialize,
    compute_protected_field_digests,
    validate_atom,
    validate_manifest,
)

HEX64 = "a" * 64
HEX64_B = "b" * 64


def make_atom(atom_id="atom-1", **overrides) -> ContextAtom:
    """A valid baseline atom; override any field via keyword arguments."""
    base = dict(
        atom_id=atom_id,
        source_pointer="project/RoadMaps/ARA-09C.md#invariant-1",
        source_digest=HEX64,
        provenance={
            "origin": "operator-relay",
            "retrieved_at": "2026-08-27T19:38:47Z",
            "retrieved_by": "mobius",
        },
        truth_status="VERIFIED_CURRENT",
        significance=3,
        lifecycle_state="ACTIVE",
        context_class="LOSSLESS_STRUCTURED",
        dependencies=(),
        expires_at=None,
        estimated_tokens=120,
        payload={"claim": "baseline"},
        protected_fields=(),
        created_at="2026-08-27T19:38:47Z",
        superseded_by=None,
        note=None,
    )
    base.update(overrides)
    if "protected_field_digests" not in overrides:
        base["protected_field_digests"] = compute_protected_field_digests(
            base["payload"],
            [name for name in base["protected_fields"] if name in base["payload"]],
        )
    return ContextAtom(**base)


def make_budget(usable=4000, **overrides) -> BudgetLedger:
    """A valid ledger whose usable_input_budget is exactly `usable`."""
    base = dict(
        model_window_tokens=usable + 2000,
        bootstrap_cost_tokens=500,
        output_reserve_tokens=500,
        tool_result_reserve_tokens=500,
        safety_margin_tokens=500,
    )
    base.update(overrides)
    return BudgetLedger(**base)


def codes(failures) -> set:
    return {f.code for f in failures}


def subjects(failures, code) -> set:
    return {f.subject for f in failures if f.code == code}


# ---------------------------------------------------------------------------
# Canonical serialization and digests
# ---------------------------------------------------------------------------

class TestCanonicalSerialization(unittest.TestCase):
    def test_key_order_independence(self):
        a = {"z": 1, "a": [1, 2, {"y": True, "x": None}], "m": "s"}
        b = {"m": "s", "a": [1, 2, {"x": None, "y": True}], "z": 1}
        self.assertEqual(canonical_serialize(a), canonical_serialize(b))
        self.assertEqual(canonical_digest(a), canonical_digest(b))

    def test_tuple_serializes_like_list(self):
        self.assertEqual(
            canonical_serialize({"k": (1, 2)}),
            canonical_serialize({"k": [1, 2]}),
        )

    def test_ascii_escaping_is_stable(self):
        s = canonical_serialize({"k": "caf\u00e9 \u2260 \u2200"})
        self.assertEqual(s, b'{"k":"caf\\u00e9 \\u2260 \\u2200"}')

    def test_nan_rejected(self):
        with self.assertRaises(CanonicalError):
            canonical_serialize({"k": float("nan")})

    def test_inf_rejected(self):
        with self.assertRaises(CanonicalError):
            canonical_serialize([float("inf")])

    def test_non_string_keys_rejected(self):
        with self.assertRaises(CanonicalError):
            canonical_serialize({1: "x"})

    def test_non_canonical_type_rejected(self):
        with self.assertRaises(CanonicalError):
            canonical_serialize({"k": object()})

    def test_deep_nesting_rejected(self):
        value = "leaf"
        for _ in range(70):
            value = [value]
        with self.assertRaises(CanonicalError):
            canonical_serialize(value)

    def test_digest_is_sha256_hex(self):
        d = canonical_digest({"k": 1})
        self.assertEqual(len(d), 64)
        int(d, 16)  # must be valid hex

    def test_digest_changes_with_value(self):
        self.assertNotEqual(canonical_digest({"k": 1}), canonical_digest({"k": 2}))


# ---------------------------------------------------------------------------
# Atom validation
# ---------------------------------------------------------------------------

class TestAtomValidation(unittest.TestCase):
    def test_valid_atom_has_no_failures(self):
        self.assertEqual(validate_atom(make_atom()), [])

    def test_valid_atom_with_dependency(self):
        a = make_atom("atom-a", dependencies=("atom-b",))
        b = make_atom("atom-b")
        self.assertEqual(validate_atom(a, known_atom_ids={"atom-a", "atom-b"}), [])

    def test_bad_id_rejected(self):
        for bad in ("", "ab", "-lead", "has space", "x" * 129):
            with self.subTest(bad=bad):
                self.assertIn("ID_INVALID", codes(validate_atom(make_atom(atom_id=bad))))

    def test_empty_source_pointer_rejected(self):
        self.assertIn(
            "SOURCE_POINTER_INVALID",
            codes(validate_atom(make_atom(source_pointer="   "))),
        )

    def test_bad_source_digest_rejected(self):
        for bad in ("", "xyz", "A" * 64, "a" * 63):
            with self.subTest(bad=bad):
                self.assertIn(
                    "DIGEST_INVALID",
                    codes(validate_atom(make_atom(source_digest=bad))),
                )

    def test_provenance_missing_keys_rejected(self):
        self.assertIn(
            "PROVENANCE_INCOMPLETE",
            codes(validate_atom(make_atom(provenance={"origin": "x"}))),
        )

    def test_provenance_bad_retrieved_at_rejected(self):
        prov = {
            "origin": "x",
            "retrieved_at": "yesterday",
            "retrieved_by": "mobius",
        }
        self.assertIn(
            "PROVENANCE_INCOMPLETE",
            codes(validate_atom(make_atom(provenance=prov))),
        )

    def test_provenance_actor_fields_must_be_nonempty_strings(self):
        prov = {
            "origin": "",
            "retrieved_at": "2026-08-27T19:38:47Z",
            "retrieved_by": None,
        }
        self.assertIn(
            "PROVENANCE_INCOMPLETE",
            codes(validate_atom(make_atom(provenance=prov))),
        )

    def test_unknown_vocabularies_rejected(self):
        f = validate_atom(make_atom(
            truth_status="MAYBE",
            lifecycle_state="DOCKED",
            context_class="VIBRANT",
        ))
        self.assertEqual(
            len([x for x in f if x.code == "VOCAB_UNKNOWN_VALUE"]), 3)

    def test_significance_bounds(self):
        self.assertIn("SCHEMA_BAD_TYPE", codes(validate_atom(make_atom(significance=0))))
        self.assertIn("SCHEMA_BAD_TYPE", codes(validate_atom(make_atom(significance=6))))
        # bool is an int subclass and must be rejected
        self.assertIn("SCHEMA_BAD_TYPE", codes(validate_atom(make_atom(significance=True))))

    def test_negative_tokens_rejected(self):
        self.assertIn(
            "SCHEMA_BAD_TYPE",
            codes(validate_atom(make_atom(estimated_tokens=-1))),
        )

    def test_bad_expiry_rejected(self):
        self.assertIn(
            "EXPIRY_INVALID",
            codes(validate_atom(make_atom(expires_at="2026-13-99"))),
        )

    def test_dependency_on_unknown_atom_rejected(self):
        f = validate_atom(
            make_atom("atom-a", dependencies=("ghost",)),
            known_atom_ids={"atom-a"},
        )
        self.assertIn("DEPENDENCY_MISSING", codes(f))

    def test_self_dependency_rejected(self):
        f = validate_atom(
            make_atom("atom-a", dependencies=("atom-a",)),
            known_atom_ids={"atom-a"},
        )
        self.assertIn("DEPENDENCY_CYCLE", codes(f))

    def test_refuted_must_be_tombstoned_or_expired(self):
        f = validate_atom(make_atom(truth_status="REFUTED", lifecycle_state="ACTIVE"))
        self.assertIn("TRUTH_LIFECYCLE_CONFLICT", codes(f))
        ok = validate_atom(make_atom(truth_status="REFUTED", lifecycle_state="TOMBSTONED"))
        self.assertEqual(ok, [])

    def test_superseded_must_not_be_active_and_must_name_successor(self):
        f = validate_atom(make_atom(truth_status="SUPERSEDED", lifecycle_state="ACTIVE"))
        self.assertIn("TRUTH_LIFECYCLE_CONFLICT", codes(f))
        f = validate_atom(make_atom(truth_status="SUPERSEDED", lifecycle_state="SUPERSEDED"))
        self.assertIn("TRUTH_LIFECYCLE_CONFLICT", codes(f))
        ok = validate_atom(make_atom(
            truth_status="SUPERSEDED",
            lifecycle_state="SUPERSEDED",
            superseded_by="atom-next",
        ))
        self.assertEqual(ok, [])

    def test_burnt_class_requires_refuted_or_superseded(self):
        f = validate_atom(make_atom(context_class="BURNT_OR_REFUTED"))
        self.assertIn("TRUTH_LIFECYCLE_CONFLICT", codes(f))
        ok = validate_atom(make_atom(
            context_class="BURNT_OR_REFUTED",
            truth_status="REFUTED",
            lifecycle_state="TOMBSTONED",
        ))
        self.assertEqual(ok, [])

    def test_protected_field_missing_from_payload_rejected(self):
        f = validate_atom(make_atom(
            payload={"claim": "x"},
            protected_fields=("claim", "missing_field"),
        ))
        self.assertIn("PROTECTED_FIELD_MUTATED", codes(f))
        self.assertIn("missing_field", subjects(f, "PROTECTED_FIELD_MUTATED"))

    def test_non_canonical_payload_rejected(self):
        f = validate_atom(make_atom(payload={"k": float("nan")}))
        self.assertIn("SCHEMA_BAD_TYPE", codes(f))

    def test_protected_value_digest_mismatch_rejected(self):
        original_payload = {"instruction": "must not publish"}
        bound = compute_protected_field_digests(original_payload, ("instruction",))
        atom = make_atom(
            "atom-protected-value",
            payload={"instruction": "may publish"},
            protected_fields=("instruction",),
            protected_field_digests=bound,
        )
        self.assertIn("PROTECTED_FIELD_MUTATED", codes(validate_atom(atom)))

    def test_exact_protected_requires_a_bound_field(self):
        atom = make_atom("atom-exact", context_class="EXACT_PROTECTED")
        self.assertIn("PROTECTED_FIELD_MUTATED", codes(validate_atom(atom)))

    def test_malformed_payload_returns_failure_not_exception(self):
        atom = make_atom("atom-malformed", payload=None)
        self.assertIn("SCHEMA_BAD_TYPE", codes(validate_atom(atom)))


# ---------------------------------------------------------------------------
# Manifest validation
# ---------------------------------------------------------------------------

class TestManifestValidation(unittest.TestCase):
    def test_valid_manifest_passes(self):
        atoms = [make_atom("atom-a"), make_atom("atom-b", estimated_tokens=80)]
        m = build_manifest(
            "manifest-1",
            make_budget(usable=1000),
            atoms,
            ["atom-a", "atom-b"],
        )
        self.assertEqual(validate_manifest(m, atoms), [])

    def test_contract_version_mismatch_rejected(self):
        atoms = [make_atom("atom-a")]
        m = build_manifest("manifest-1", make_budget(), atoms, ["atom-a"])
        m = ContextManifest(
            manifest_id=m.manifest_id,
            contract_version="ara09c-c1-v0.0.9",
            budget=m.budget,
            included_atom_ids=m.included_atom_ids,
            omissions=m.omissions,
            tombstones=m.tombstones,
            compression_lineage=m.compression_lineage,
            verifier_outcome=m.verifier_outcome,
            verifier_detail=m.verifier_detail,
            total_included_tokens=m.total_included_tokens,
        )
        self.assertIn("SCHEMA_BAD_TYPE", codes(validate_manifest(m, atoms)))

    def test_negative_usable_budget_rejected(self):
        atoms = [make_atom("atom-a")]
        m = build_manifest(
            "manifest-1",
            make_budget(usable=-5),
            atoms,
            ["atom-a"],
        )
        self.assertIn("BUDGET_LEDGER_INCONSISTENT", codes(validate_manifest(m, atoms)))

    def test_negative_reserve_rejected(self):
        atoms = [make_atom("atom-a")]
        m = build_manifest(
            "manifest-1",
            make_budget(usable=1000, output_reserve_tokens=-1),
            atoms,
            ["atom-a"],
        )
        self.assertIn("BUDGET_LEDGER_INCONSISTENT", codes(validate_manifest(m, atoms)))

    def test_duplicate_atom_ids_rejected(self):
        a1 = make_atom("atom-a")
        a2 = make_atom("atom-a", payload={"claim": "imposter"})
        m = build_manifest("manifest-1", make_budget(), [a1, a2], ["atom-a"])
        self.assertIn("ATOM_ID_DUPLICATE", codes(validate_manifest(m, [a1, a2])))

    def test_included_unknown_atom_rejected(self):
        atoms = [make_atom("atom-a")]
        m = build_manifest("manifest-1", make_budget(), atoms, ["ghost"])
        self.assertIn("SCHEMA_MISSING_FIELD", codes(validate_manifest(m, atoms)))

    def test_dependency_closure_enforced(self):
        a = make_atom("atom-a", dependencies=("atom-b",))
        b = make_atom("atom-b")
        m = build_manifest("manifest-1", make_budget(), [a, b], ["atom-a"])
        f = validate_manifest(m, [a, b])
        self.assertIn("DEPENDENCY_MISSING", codes(f))
        self.assertIn("atom-a", subjects(f, "DEPENDENCY_MISSING"))

    def test_dependency_cycle_rejected(self):
        a = make_atom("atom-a", dependencies=("atom-b",))
        b = make_atom("atom-b", dependencies=("atom-a",))
        m = build_manifest("manifest-1", make_budget(), [a, b], ["atom-a", "atom-b"])
        f = validate_manifest(m, [a, b])
        self.assertIn("DEPENDENCY_CYCLE", codes(f))
        self.assertEqual(subjects(f, "DEPENDENCY_CYCLE"), {"atom-a"})

    def test_superseded_target_must_exist(self):
        stale = make_atom(
            "atom-stale",
            truth_status="SUPERSEDED",
            lifecycle_state="SUPERSEDED",
            superseded_by="atom-missing",
        )
        manifest = build_manifest(
            "manifest-1", make_budget(), [stale], [],
            omissions=[OmissionRecord("atom-stale", "SUPERSEDED")],
        )
        self.assertIn("DEPENDENCY_MISSING", codes(validate_manifest(manifest, [stale])))

    def test_budget_fit_enforced(self):
        a = make_atom("atom-a", estimated_tokens=5000)
        m = build_manifest("manifest-1", make_budget(usable=1000), [a], ["atom-a"])
        self.assertIn("BUDGET_LEDGER_INCONSISTENT", codes(validate_manifest(m, [a])))

    def test_total_included_tokens_consistency(self):
        atoms = [make_atom("atom-a", estimated_tokens=100),
                 make_atom("atom-b", estimated_tokens=50)]
        m = build_manifest("manifest-1", make_budget(), atoms, ["atom-a", "atom-b"])
        self.assertEqual(m.total_included_tokens, 150)
        m = ContextManifest(
            manifest_id=m.manifest_id,
            contract_version=m.contract_version,
            budget=m.budget,
            included_atom_ids=m.included_atom_ids,
            omissions=m.omissions,
            tombstones=m.tombstones,
            compression_lineage=m.compression_lineage,
            verifier_outcome=m.verifier_outcome,
            verifier_detail=m.verifier_detail,
            total_included_tokens=999,
        )
        self.assertIn("BUDGET_LEDGER_INCONSISTENT", codes(validate_manifest(m, atoms)))

    def test_omission_reason_vocabulary(self):
        atoms = [make_atom("atom-a")]
        m = build_manifest(
            "manifest-1", make_budget(), atoms, [],
            omissions=[OmissionRecord("atom-a", "BECAUSE_WE_FELT_LIKE_IT")],
        )
        self.assertIn("OMISSION_REASON_INVALID", codes(validate_manifest(m, atoms)))

    def test_omission_of_unknown_atom_rejected(self):
        atoms = [make_atom("atom-a")]
        m = build_manifest(
            "manifest-1", make_budget(), atoms, ["atom-a"],
            omissions=[OmissionRecord("ghost", "LOW_RELEVANCE")],
        )
        self.assertIn("SCHEMA_MISSING_FIELD", codes(validate_manifest(m, atoms)))

    def test_include_omit_overlap_rejected(self):
        atoms = [make_atom("atom-a")]
        m = build_manifest(
            "manifest-1", make_budget(), atoms, ["atom-a"],
            omissions=[OmissionRecord("atom-a", "LOW_RELEVANCE")],
        )
        self.assertIn("SCHEMA_BAD_TYPE", codes(validate_manifest(m, atoms)))

    def test_tombstone_incomplete_rejected(self):
        atoms = [make_atom("atom-a")]
        t = Tombstone(
            tombstone_id="tomb-1",
            rejected_claim="   ",
            rejection_reason="refuted by evidence",
            evidence_pointer="logs/2026-08-27.md",
            evidence_digest=HEX64,
            replacement_atom_id=None,
            created_at="2026-08-27T19:38:47Z",
        )
        m = build_manifest("manifest-1", make_budget(), atoms, ["atom-a"],
                           tombstones=[t])
        self.assertIn("TOMBSTONE_INCOMPLETE", codes(validate_manifest(m, atoms)))

    def test_tombstone_bad_evidence_digest_rejected(self):
        atoms = [make_atom("atom-a")]
        t = Tombstone(
            tombstone_id="tomb-1",
            rejected_claim="claim",
            rejection_reason="refuted by evidence",
            evidence_pointer="logs/2026-08-27.md",
            evidence_digest="not-a-digest",
            replacement_atom_id=None,
            created_at="2026-08-27T19:38:47Z",
        )
        m = build_manifest("manifest-1", make_budget(), atoms, ["atom-a"],
                           tombstones=[t])
        self.assertIn("DIGEST_INVALID", codes(validate_manifest(m, atoms)))

    def test_tombstone_unknown_replacement_rejected(self):
        atoms = [make_atom("atom-a")]
        t = Tombstone(
            tombstone_id="tomb-1",
            rejected_claim="claim",
            rejection_reason="refuted by evidence",
            evidence_pointer="logs/2026-08-27.md",
            evidence_digest=HEX64,
            replacement_atom_id="ghost",
            created_at="2026-08-27T19:38:47Z",
        )
        m = build_manifest("manifest-1", make_budget(), atoms, ["atom-a"],
                           tombstones=[t])
        self.assertIn("DEPENDENCY_MISSING", codes(validate_manifest(m, atoms)))

    def test_verifier_outcome_vocabulary(self):
        atoms = [make_atom("atom-a")]
        m = build_manifest("manifest-1", make_budget(), atoms, ["atom-a"])
        m = ContextManifest(
            manifest_id=m.manifest_id,
            contract_version=m.contract_version,
            budget=m.budget,
            included_atom_ids=m.included_atom_ids,
            omissions=m.omissions,
            tombstones=m.tombstones,
            compression_lineage=m.compression_lineage,
            verifier_outcome="MAYBE",
            verifier_detail=None,
            total_included_tokens=m.total_included_tokens,
        )
        self.assertIn("SCHEMA_BAD_TYPE", codes(validate_manifest(m, atoms)))

    def test_packet_digest_is_deterministic_and_verified(self):
        atoms = [make_atom("atom-a"), make_atom("atom-b")]
        first = build_manifest("manifest-1", make_budget(), atoms, ["atom-a", "atom-b"])
        second = build_manifest("manifest-1", make_budget(), atoms, ["atom-a", "atom-b"])
        self.assertEqual(first.packet_digest, second.packet_digest)
        self.assertEqual(validate_manifest(first, atoms), [])
        tampered = replace(first, packet_digest="f" * 64)
        self.assertIn("MANIFEST_DIGEST_MISMATCH", codes(validate_manifest(tampered, atoms)))

    def test_inactive_atom_cannot_be_included(self):
        refuted = make_atom(
            "atom-refuted",
            truth_status="REFUTED",
            lifecycle_state="TOMBSTONED",
            context_class="BURNT_OR_REFUTED",
        )
        manifest = build_manifest("manifest-1", make_budget(), [refuted], ["atom-refuted"])
        self.assertIn("TRUTH_LIFECYCLE_CONFLICT", codes(validate_manifest(manifest, [refuted])))

    def test_every_atom_requires_visible_disposition(self):
        atoms = [make_atom("atom-a"), make_atom("atom-b")]
        manifest = build_manifest("manifest-1", make_budget(), atoms, ["atom-a"])
        self.assertIn("SCHEMA_MISSING_FIELD", codes(validate_manifest(manifest, atoms)))

    def test_duplicate_included_id_rejected(self):
        atoms = [make_atom("atom-a")]
        manifest = build_manifest("manifest-1", make_budget(), atoms, ["atom-a", "atom-a"])
        self.assertIn("ATOM_ID_DUPLICATE", codes(validate_manifest(manifest, atoms)))

    def test_malformed_budget_returns_failure_not_exception(self):
        atoms = [make_atom("atom-a")]
        manifest = build_manifest("manifest-1", BudgetLedger("bad", 0, 0, 0, 0), atoms, ["atom-a"])
        self.assertIn("BUDGET_LEDGER_INCONSISTENT", codes(validate_manifest(manifest, atoms)))

    def test_tombstone_metadata_is_validated(self):
        atom = make_atom("atom-a")
        tombstone = Tombstone(
            tombstone_id="bad id",
            rejected_claim=None,
            rejection_reason="reason",
            evidence_pointer=None,
            evidence_digest=None,
            replacement_atom_id=None,
            created_at="2026-99-99",
            truth_status="UNKNOWN",
        )
        manifest = build_manifest(
            "manifest-1", make_budget(), [atom], ["atom-a"], tombstones=[tombstone]
        )
        got = codes(validate_manifest(manifest, [atom]))
        self.assertTrue({
            "ID_INVALID", "TOMBSTONE_INCOMPLETE", "SOURCE_POINTER_INVALID",
            "DIGEST_INVALID", "EXPIRY_INVALID", "TRUTH_LIFECYCLE_CONFLICT",
        }.issubset(got))

    def test_malformed_omission_returns_failure_not_exception(self):
        atom = make_atom("atom-a")
        manifest = build_manifest(
            "manifest-1", make_budget(), [atom], [],
            omissions=[OmissionRecord(None, None, detail=7)],
        )
        self.assertIn("SCHEMA_BAD_TYPE", codes(validate_manifest(manifest, [atom])))

    def test_malformed_compression_lineage_is_rejected(self):
        atom = make_atom("atom-a")
        manifest = build_manifest("manifest-1", make_budget(), [atom], ["atom-a"])
        malformed = replace(manifest, compression_lineage=("not-an-object",))
        self.assertIn("SCHEMA_BAD_TYPE", codes(validate_manifest(malformed, [atom])))


# ---------------------------------------------------------------------------
# Adversarial fixtures (scenario-level)
# ---------------------------------------------------------------------------

class TestAdversarialFixtures(unittest.TestCase):
    def test_stale_and_current_fact_pair(self):
        """A stale fact superseded by a current one: the pair must validate
        together, the stale atom must be non-ACTIVE, and the current atom
        must be includable while the stale one is omitted with a reason."""
        stale = make_atom(
            "fact-old",
            truth_status="SUPERSEDED",
            lifecycle_state="SUPERSEDED",
            superseded_by="fact-new",
            payload={"claim": "budget is 2000 tokens"},
        )
        current = make_atom(
            "fact-new",
            payload={"claim": "budget is 4000 tokens"},
        )
        self.assertEqual(validate_atom(stale, known_atom_ids={"fact-old", "fact-new"}), [])
        self.assertEqual(validate_atom(current, known_atom_ids={"fact-old", "fact-new"}), [])

        m = build_manifest(
            "manifest-pair",
            make_budget(usable=1000),
            [stale, current],
            ["fact-new"],
            omissions=[OmissionRecord("fact-old", "SUPERSEDED",
                                      detail="replaced by fact-new")],
        )
        self.assertEqual(validate_manifest(m, [stale, current]), [])

    def test_refuted_path_with_tombstone(self):
        """A refuted path: the refuted atom must be TOMBSTONED, the
        tombstone must carry evidence, and the manifest must record both."""
        refuted = make_atom(
            "path-refuted",
            truth_status="REFUTED",
            lifecycle_state="TOMBSTONED",
            context_class="BURNT_OR_REFUTED",
            payload={"claim": "compression may widen authority"},
        )
        replacement = make_atom(
            "path-replacement",
            payload={"claim": "compression never grants authority"},
        )
        tomb = Tombstone(
            tombstone_id="tomb-refuted",
            rejected_claim="compression may widen authority",
            rejection_reason="violates invariant 6; refuted by operator review",
            evidence_pointer="logs/2026-08-27-review.md",
            evidence_digest=HEX64_B,
            replacement_atom_id="path-replacement",
            created_at="2026-08-27T19:38:47Z",
        )
        self.assertEqual(
            validate_atom(refuted, known_atom_ids={"path-refuted", "path-replacement"}), [])
        m = build_manifest(
            "manifest-refuted",
            make_budget(usable=1000),
            [refuted, replacement],
            ["path-replacement"],
            omissions=[OmissionRecord("path-refuted", "REFUTED",
                                      detail="see tomb-refuted")],
            tombstones=[tomb],
        )
        self.assertEqual(validate_manifest(m, [refuted, replacement]), [])

    def test_protected_field_mutation_detected(self):
        """A protected field removed from the payload must be flagged as a
        fidelity violation, not silently accepted."""
        atom = make_atom(
            "atom-protected",
            payload={"exact_quote": "The loop closes."},
            protected_fields=("exact_quote",),
        )
        self.assertEqual(validate_atom(atom), [])
        mutated = make_atom(
            "atom-protected",
            payload={"exact_quote": "The loop closes."},
            protected_fields=("exact_quote", "exact_digest"),
        )
        f = validate_atom(mutated)
        self.assertIn("PROTECTED_FIELD_MUTATED", codes(f))
        self.assertIn("exact_digest", subjects(f, "PROTECTED_FIELD_MUTATED"))

    def test_non_canonical_payload_rejected(self):
        """NaN in a payload is not canonically serializable and must fail
        validation with a structured failure, not an uncaught exception."""
        atom = make_atom("atom-nan", payload={"value": float("nan")})
        f = validate_atom(atom)
        self.assertIn("SCHEMA_BAD_TYPE", codes(f))

    def test_duplicate_atom_ids_rejected(self):
        """Two atoms claiming the same ID must be flagged, even if one is
        a byte-identical copy."""
        a = make_atom("twin")
        b = make_atom("twin")
        m = build_manifest("manifest-twin", make_budget(), [a, b], ["twin"])
        f = validate_manifest(m, [a, b])
        self.assertIn("ATOM_ID_DUPLICATE", codes(f))

    def test_negative_budget_rejected(self):
        """A ledger whose reserves exceed the model window must fail,
        regardless of what the included set costs."""
        ledger = make_budget(usable=-1)
        self.assertEqual(ledger.usable_input_budget, -1)
        atoms = [make_atom("atom-a")]
        m = build_manifest("manifest-neg", ledger, atoms, ["atom-a"])
        self.assertIn("BUDGET_LEDGER_INCONSISTENT", codes(validate_manifest(m, atoms)))

    def test_full_adversarial_manifest_fails_with_expected_codes(self):
        """One manifest violating many rules at once: every violation must
        surface as a structured failure with the right code and subject."""
        bad_atom = make_atom(
            "bad-atom",
            source_digest="nope",
            truth_status="REFUTED",
            lifecycle_state="ACTIVE",
            estimated_tokens=10**6,
        )
        m = build_manifest(
            "manifest-bad",
            make_budget(usable=100),
            [bad_atom],
            ["bad-atom"],
            omissions=[OmissionRecord("bad-atom", "NOT_A_REASON")],
        )
        f = validate_manifest(m, [bad_atom])
        got = codes(f)
        for expected in (
            "DIGEST_INVALID",
            "TRUTH_LIFECYCLE_CONFLICT",
            "BUDGET_LEDGER_INCONSISTENT",
            "OMISSION_REASON_INVALID",
            "SCHEMA_BAD_TYPE",  # include/omit overlap
        ):
            self.assertIn(expected, got)


# ---------------------------------------------------------------------------
# Contract surface sanity
# ---------------------------------------------------------------------------

class TestContractSurface(unittest.TestCase):
    def test_vocabularies_are_closed_and_disjoint_where_expected(self):
        self.assertEqual(len(CONTEXT_CLASSES), len(set(CONTEXT_CLASSES)))
        self.assertEqual(len(TRUTH_STATUSES), len(set(TRUTH_STATUSES)))
        self.assertEqual(len(LIFECYCLE_STATES), len(set(LIFECYCLE_STATES)))
        self.assertEqual(len(OMISSION_REASONS), len(set(OMISSION_REASONS)))
        self.assertIn("BURNT_OR_REFUTED", CONTEXT_CLASSES)
        self.assertIn("REFUTED", TRUTH_STATUSES)
        self.assertIn("TOMBSTONED", LIFECYCLE_STATES)

    def test_contract_version_shape(self):
        self.assertTrue(CONTRACT_VERSION.startswith("ara09c-c1-"))

    def test_validation_failure_is_structured(self):
        vf = ValidationFailure("ID_INVALID", "s", "msg")
        self.assertEqual(vf.to_dict(), {"code": "ID_INVALID", "subject": "s", "message": "msg"})

    def test_atom_digest_is_stable(self):
        a = make_atom("digest-a")
        self.assertEqual(a.digest(), canonical_digest(a.to_dict()))
        self.assertEqual(a.digest(), make_atom("digest-a").digest())


if __name__ == "__main__":
    unittest.main(verbosity=2)
