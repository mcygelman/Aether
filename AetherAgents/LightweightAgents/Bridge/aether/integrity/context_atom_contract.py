"""ARA-09C C1 — canonical context atom and manifest contract.

This module defines the production C1 data and validation boundary for
ARA-09C (Shared Semantic Context Governance):

  * strict schemas for context atoms, tombstones, budgets, and manifests;
  * canonical serialization (deterministic, key-sorted, no floats-as-strings
    ambiguity) and stable SHA-256 digests;
  * protected / truth / lifecycle state vocabularies;
  * provenance, expiry, and dependency rules;
  * omission reasons and validation-failure reporting;
  * a deterministic validator that emits structured failures.

Design constraints honored here (from the ARA-09C roadmap):
  - invariant 1: protected meaning is never semantically compressed;
  - invariant 5: omission is visible (manifest records omissions + reasons);
  - invariant 6: compression never grants authority (no field in this
    contract can widen scope; authority is carried only as data);
  - invariant 8: physical limits are explicit (budget ledger is data, not
    assumption);
  - invariant 9: full evidence remains externally reachable (atoms carry
    source pointers + digests, never recursive evidence copies);
  - fidelity policy: exact protected fields use canonical serialization and
    digest comparison.

Python 3.9+ stdlib only. No third-party imports.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

__all__ = [
    "CONTRACT_VERSION",
    "CONTEXT_CLASSES",
    "TRUTH_STATUSES",
    "LIFECYCLE_STATES",
    "OMISSION_REASONS",
    "VALIDATION_FAILURE_CODES",
    "CanonicalError",
    "ValidationError",
    "canonical_serialize",
    "canonical_digest",
    "compute_protected_field_digests",
    "ContextAtom",
    "Tombstone",
    "RecordRef",
    "BudgetLedger",
    "OmissionRecord",
    "ValidationFailure",
    "ContextManifest",
    "validate_atom",
    "validate_manifest",
    "build_manifest",
]

CONTRACT_VERSION = "ara09c-c1-v1.1.0"
LEGACY_CONTRACT_VERSION = "ara09c-c1-v1.0.0"

# ---------------------------------------------------------------------------
# Vocabularies (closed sets; unknown values are validation failures)
# ---------------------------------------------------------------------------

CONTEXT_CLASSES: Tuple[str, ...] = (
    "EXACT_PROTECTED",
    "LOSSLESS_STRUCTURED",
    "SEMANTIC_COMPRESSIBLE",
    "EPHEMERAL",
    "BURNT_OR_REFUTED",
    "ARCHIVE_ONLY",
)

TRUTH_STATUSES: Tuple[str, ...] = (
    "VERIFIED_CURRENT",
    "OPERATOR_ASSERTED",
    "OBSERVED_UNVERIFIED",
    "HYPOTHESIS",
    "SUPERSEDED",
    "REFUTED",
    "UNKNOWN",
)

LIFECYCLE_STATES: Tuple[str, ...] = (
    "ACTIVE",
    "PENDING_EXPIRY",
    "EXPIRED",
    "SUPERSEDED",
    "ARCHIVED",
    "TOMBSTONED",
)

OMISSION_REASONS: Tuple[str, ...] = (
    "BUDGET_EXCEEDED",
    "EXPIRED",
    "SUPERSEDED",
    "REFUTED",
    "EPHEMERAL_TURN_ENDED",
    "ARCHIVE_ONLY_NOT_RETRIEVED",
    "DUPLICATE_OF_INCLUDED_ATOM",
    "LOW_RELEVANCE",
    "PROVENANCE_INCOMPLETE",
    "POLICY_EXCLUDED",
)

VALIDATION_FAILURE_CODES: Tuple[str, ...] = (
    "SCHEMA_MISSING_FIELD",
    "SCHEMA_UNKNOWN_FIELD",
    "SCHEMA_BAD_TYPE",
    "VOCAB_UNKNOWN_VALUE",
    "ID_INVALID",
    "SOURCE_POINTER_INVALID",
    "DIGEST_INVALID",
    "DIGEST_MISMATCH",
    "PROVENANCE_INCOMPLETE",
    "EXPIRY_INVALID",
    "DEPENDENCY_MISSING",
    "DEPENDENCY_CYCLE",
    "TRUTH_LIFECYCLE_CONFLICT",
    "PROTECTED_FIELD_MUTATED",
    "BUDGET_LEDGER_INCONSISTENT",
    "OMISSION_REASON_INVALID",
    "MANIFEST_DIGEST_MISMATCH",
    "ATOM_ID_DUPLICATE",
    "TOMBSTONE_INCOMPLETE",
    "SEMANTIC_FIDELITY_VIOLATION",
)

_ATOM_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,127}$")
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ISO_DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?(Z|[+-]\d{2}:\d{2})$")


# ---------------------------------------------------------------------------
# Canonical serialization and digests
# ---------------------------------------------------------------------------

class CanonicalError(ValueError):
    """Raised when a value cannot be canonically serialized."""


def canonical_serialize(value: Any) -> bytes:
    """Deterministic canonical JSON serialization.

    Rules (stable across processes and platforms):
      * objects: keys sorted lexicographically (byte order of UTF-8 keys);
      * separators: no whitespace (",", ":");
      * ensure_ascii=True so output bytes are independent of locale;
      * floats must be finite (NaN/Inf are rejected — they are not
        canonically representable);
      * only JSON-compatible types are accepted (str, int, bool, None,
        finite float, list, dict);
      * dict keys must be strings.
    """
    def _check(obj: Any, depth: int = 0) -> None:
        if depth > 64:
            raise CanonicalError("nesting depth exceeds 64")
        if obj is None or isinstance(obj, (bool, int, str)):
            return
        if isinstance(obj, float):
            if obj != obj or obj in (float("inf"), float("-inf")):
                raise CanonicalError("non-finite float is not canonically serializable")
            return
        if isinstance(obj, (list, tuple)):
            for item in obj:
                _check(item, depth + 1)
            return
        if isinstance(obj, dict):
            for key, item in obj.items():
                if not isinstance(key, str):
                    raise CanonicalError("object keys must be strings")
                _check(item, depth + 1)
            return
        raise CanonicalError(f"non-canonical type: {type(obj).__name__}")

    _check(value)
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return text.encode("utf-8")


def canonical_digest(value: Any) -> str:
    """SHA-256 hex digest of the canonical serialization of `value`."""
    return hashlib.sha256(canonical_serialize(value)).hexdigest()


def compute_protected_field_digests(
    payload: Mapping[str, Any], protected_fields: Sequence[str]
) -> Dict[str, str]:
    """Bind each protected field to its canonical value digest.

    The caller must obtain this binding from the protected source record;
    recomputing it after a mutation would not establish fidelity.
    """
    return {name: canonical_digest(payload[name]) for name in protected_fields}


# ---------------------------------------------------------------------------
# Dataclasses (schemas)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ContextAtom:
    """One candidate unit of active context.

    Every atom carries the full C1 field set required by the roadmap:
    stable ID, source pointer, provenance, truth status, significance,
    lifecycle state, dependencies, freshness/expiry, estimated token cost,
    and handling class.
    """

    atom_id: str
    source_pointer: str
    source_digest: str
    provenance: Dict[str, Any]
    truth_status: str
    significance: int  # 1..5 (5 = highest)
    lifecycle_state: str
    context_class: str
    dependencies: Tuple[str, ...]
    expires_at: Optional[str]  # ISO date/datetime or None (no expiry)
    estimated_tokens: int
    payload: Dict[str, Any]
    protected_fields: Tuple[str, ...] = ()
    protected_field_digests: Dict[str, str] = field(default_factory=dict)
    created_at: Optional[str] = None
    superseded_by: Optional[str] = None
    note: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "atom_id": self.atom_id,
            "source_pointer": self.source_pointer,
            "source_digest": self.source_digest,
            "provenance": dict(self.provenance),
            "truth_status": self.truth_status,
            "significance": self.significance,
            "lifecycle_state": self.lifecycle_state,
            "context_class": self.context_class,
            "dependencies": list(self.dependencies),
            "expires_at": self.expires_at,
            "estimated_tokens": self.estimated_tokens,
            "payload": dict(self.payload),
            "protected_fields": list(self.protected_fields),
            "protected_field_digests": dict(self.protected_field_digests),
            "created_at": self.created_at,
            "superseded_by": self.superseded_by,
            "note": self.note,
        }

    def digest(self) -> str:
        return canonical_digest(self.to_dict())


@dataclass(frozen=True)
class RecordRef:
    """Immutable record identity; resolution is owned by the controller."""

    record_id: str
    revision: int
    sha256: str

    def to_dict(self) -> Dict[str, Any]:
        if not isinstance(self.record_id, str) or not self.record_id.strip() \
                or type(self.revision) is not int or self.revision < 0 \
                or not isinstance(self.sha256, str) or not _DIGEST_RE.fullmatch(self.sha256):
            raise ValueError("invalid record reference")
        return {"record_id": self.record_id, "revision": self.revision, "sha256": self.sha256}


@dataclass(frozen=True)
class Tombstone:
    """Compact evidence-linked record of a burnt/refuted path.

    Invariant 3: the full failed reasoning is excluded from the active
    packet; the tombstone records what was rejected, why, the supporting
    evidence pointer, and any replacement.
    """

    tombstone_id: str
    rejected_claim: str
    rejection_reason: str
    evidence_pointer: str
    evidence_digest: str
    replacement_atom_id: Optional[str]
    created_at: str
    truth_status: str = "REFUTED"

    record_version: int = 1
    kind: str = "CLAIM_REFUTATION"
    failure_code: Optional[str] = None
    action_fingerprint: Optional[str] = None
    scope_digest: Optional[str] = None
    reopening_condition: Optional[RecordRef] = None
    failure_receipt_ref: Optional[RecordRef] = None

    def validate_extensions(self) -> None:
        if type(self.record_version) is not int or self.record_version not in (1, 2):
            raise ValueError("unsupported tombstone record version")
        extensions = (self.failure_code, self.action_fingerprint, self.scope_digest,
                      self.reopening_condition, self.failure_receipt_ref)
        if self.record_version == 1:
            if self.kind != "CLAIM_REFUTATION" or any(v is not None for v in extensions):
                raise ValueError("v1 tombstone cannot contain extensions")
            return
        if self.kind not in ("CLAIM_REFUTATION", "ACTION_FAILURE", "ACTION_UNCERTAIN"):
            raise ValueError("unknown tombstone kind")
        codes = ("PRECONDITION_FALSE", "ACCESS_DENIED", "TARGET_CHANGED", "VALIDATION_FAILED",
                 "TEST_FAILED", "TIMEOUT", "OUTCOME_UNKNOWN", "CLAIM_COUNTEREXAMPLE", "LEGACY_UNSCOPED")
        if self.failure_code is not None and self.failure_code not in codes:
            raise ValueError("unknown failure code")
        for value in (self.action_fingerprint, self.scope_digest):
            if value is not None and (not isinstance(value, str) or not _DIGEST_RE.fullmatch(value)):
                raise ValueError("invalid tombstone digest")
        for ref in (self.reopening_condition, self.failure_receipt_ref):
            if ref is not None:
                if not isinstance(ref, RecordRef):
                    raise ValueError("invalid tombstone record reference")
                ref.to_dict()
        if self.kind != "CLAIM_REFUTATION":
            if any(v is None for v in (self.failure_code, self.action_fingerprint,
                                      self.scope_digest, self.failure_receipt_ref)):
                raise ValueError("action tombstone requires failure, scope, action and receipt")
            if self.failure_receipt_ref.sha256 != self.evidence_digest:
                raise ValueError("receipt and evidence digests differ")
        allowed_truth = (("REFUTED",) if self.kind == "CLAIM_REFUTATION" else
                         ("OBSERVED_UNVERIFIED",) if self.kind == "ACTION_UNCERTAIN" else
                         ("OBSERVED_UNVERIFIED", "REFUTED"))
        if self.truth_status not in allowed_truth:
            raise ValueError("tombstone kind and truth status conflict")

    def to_dict(self) -> Dict[str, Any]:
        self.validate_extensions()
        legacy = {
            "tombstone_id": self.tombstone_id,
            "rejected_claim": self.rejected_claim,
            "rejection_reason": self.rejection_reason,
            "evidence_pointer": self.evidence_pointer,
            "evidence_digest": self.evidence_digest,
            "replacement_atom_id": self.replacement_atom_id,
            "created_at": self.created_at,
            "truth_status": self.truth_status,
        }

        if self.record_version == 1:
            return legacy
        return dict(legacy, record_version=2, kind=self.kind, failure_code=self.failure_code,
                    action_fingerprint=self.action_fingerprint, scope_digest=self.scope_digest,
                    reopening_condition=(self.reopening_condition.to_dict()
                                         if self.reopening_condition else None),
                    failure_receipt_ref=(self.failure_receipt_ref.to_dict()
                                         if self.failure_receipt_ref else None))

    def digest(self) -> str:
        return canonical_digest(self.to_dict())


@dataclass(frozen=True)
class BudgetLedger:
    """Explicit physical limits (invariant 8).

    All values are token counts. usable_input_budget is derived, not
    assumed: it equals model_window minus the four reserves.
    """

    model_window_tokens: int
    bootstrap_cost_tokens: int
    output_reserve_tokens: int
    tool_result_reserve_tokens: int
    safety_margin_tokens: int

    @property
    def usable_input_budget(self) -> int:
        return (
            self.model_window_tokens
            - self.bootstrap_cost_tokens
            - self.output_reserve_tokens
            - self.tool_result_reserve_tokens
            - self.safety_margin_tokens
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "model_window_tokens": self.model_window_tokens,
            "bootstrap_cost_tokens": self.bootstrap_cost_tokens,
            "output_reserve_tokens": self.output_reserve_tokens,
            "tool_result_reserve_tokens": self.tool_result_reserve_tokens,
            "safety_margin_tokens": self.safety_margin_tokens,
            "usable_input_budget": self.usable_input_budget,
        }


@dataclass(frozen=True)
class OmissionRecord:
    """Visible omission (invariant 5): what was left out and why."""

    atom_id: str
    reason: str
    detail: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "atom_id": self.atom_id,
            "reason": self.reason,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class ValidationFailure:
    """Structured validation failure (never a bare exception in reports)."""

    code: str
    subject: str
    message: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "subject": self.subject,
            "message": self.message,
        }


@dataclass(frozen=True)
class ContextManifest:
    """The emitted context manifest (pipeline step 10).

    Records: packet digest, budget ledger, included atom IDs, omitted
    atoms/categories and reasons, compression lineage, active tombstones,
    and verifier outcome.
    """

    manifest_id: str
    contract_version: str
    budget: BudgetLedger
    included_atom_ids: Tuple[str, ...]
    omissions: Tuple[OmissionRecord, ...]
    tombstones: Tuple[Tombstone, ...]
    compression_lineage: Tuple[Dict[str, Any], ...]
    verifier_outcome: str  # "PASS" | "FAIL" | "PENDING"
    verifier_detail: Optional[str] = None
    total_included_tokens: int = 0
    packet_digest: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "manifest_id": self.manifest_id,
            "contract_version": self.contract_version,
            "budget": self.budget.to_dict(),
            "included_atom_ids": list(self.included_atom_ids),
            "omissions": [o.to_dict() for o in self.omissions],
            "tombstones": [t.to_dict() for t in self.tombstones],
            "compression_lineage": [dict(c) for c in self.compression_lineage],
            "verifier_outcome": self.verifier_outcome,
            "verifier_detail": self.verifier_detail,
            "total_included_tokens": self.total_included_tokens,
            "packet_digest": self.packet_digest,
        }

    def digest(self) -> str:
        return canonical_digest(self.to_dict())


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

class ValidationError(Exception):
    """Raised by strict helpers; validators themselves return failure lists."""

    def __init__(self, failures: Sequence[ValidationFailure]):
        self.failures = list(failures)
        super().__init__("; ".join(f.code for f in self.failures))


def _is_iso(value: str) -> bool:
    if _ISO_DATE_RE.match(value):
        try:
            date.fromisoformat(value)
        except ValueError:
            return False
        return True
    if _ISO_DATETIME_RE.match(value):
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return False
        return True
    return False


def _check_provenance(prov: Any, subject: str, failures: List[ValidationFailure]) -> None:
    if not isinstance(prov, dict):
        failures.append(ValidationFailure(
            "SCHEMA_BAD_TYPE", subject, "provenance must be an object"))
        return
    for required in ("origin", "retrieved_at", "retrieved_by"):
        if required not in prov:
            failures.append(ValidationFailure(
                "PROVENANCE_INCOMPLETE", subject,
                f"provenance missing required key: {required}"))
        elif required != "retrieved_at" and (
                not isinstance(prov[required], str) or not prov[required].strip()):
            failures.append(ValidationFailure(
                "PROVENANCE_INCOMPLETE", subject,
                f"provenance.{required} must be a non-empty string"))
    if "retrieved_at" in prov and not (
            isinstance(prov["retrieved_at"], str) and _is_iso(prov["retrieved_at"])):
        failures.append(ValidationFailure(
            "PROVENANCE_INCOMPLETE", subject,
            "provenance.retrieved_at must be an ISO date/datetime string"))


def validate_atom(atom: ContextAtom,
                  known_atom_ids: Optional[Sequence[str]] = None) -> List[ValidationFailure]:
    """Validate one atom. Returns a (possibly empty) list of failures.

    `known_atom_ids` is the set of atom IDs that exist in the current
    universe (atoms + tombstone replacements); when provided, dependency
    targets must be resolvable.
    """
    failures: List[ValidationFailure] = []
    s = atom.atom_id

    payload_is_mapping = isinstance(atom.payload, dict)
    payload = atom.payload if payload_is_mapping else {}
    if not payload_is_mapping:
        failures.append(ValidationFailure(
            "SCHEMA_BAD_TYPE", s, "payload must be an object"))

    protected_fields_valid = isinstance(atom.protected_fields, (list, tuple))
    protected_fields = atom.protected_fields if protected_fields_valid else ()
    if not protected_fields_valid or any(not isinstance(name, str) for name in protected_fields):
        failures.append(ValidationFailure(
            "SCHEMA_BAD_TYPE", s, "protected_fields must be a list of strings"))
        protected_fields = tuple(name for name in protected_fields if isinstance(name, str))

    protected_digests_valid = isinstance(atom.protected_field_digests, dict)
    protected_digests = atom.protected_field_digests if protected_digests_valid else {}
    if not protected_digests_valid:
        failures.append(ValidationFailure(
            "SCHEMA_BAD_TYPE", s, "protected_field_digests must be an object"))

    # --- ID ---
    if not isinstance(atom.atom_id, str) or not _ATOM_ID_RE.match(atom.atom_id):
        failures.append(ValidationFailure(
            "ID_INVALID", s, f"atom_id does not match {_ATOM_ID_RE.pattern}"))

    # --- source pointer + digest (invariant 9) ---
    if not isinstance(atom.source_pointer, str) or not atom.source_pointer.strip():
        failures.append(ValidationFailure(
            "SOURCE_POINTER_INVALID", s, "source_pointer must be a non-empty string"))
    if not isinstance(atom.source_digest, str) or not _DIGEST_RE.match(atom.source_digest):
        failures.append(ValidationFailure(
            "DIGEST_INVALID", s, "source_digest must be a 64-char lowercase hex SHA-256"))

    # --- provenance ---
    _check_provenance(atom.provenance, s, failures)

    # --- vocabularies ---
    if atom.truth_status not in TRUTH_STATUSES:
        failures.append(ValidationFailure(
            "VOCAB_UNKNOWN_VALUE", s,
            f"truth_status {atom.truth_status!r} not in {TRUTH_STATUSES}"))
    if atom.lifecycle_state not in LIFECYCLE_STATES:
        failures.append(ValidationFailure(
            "VOCAB_UNKNOWN_VALUE", s,
            f"lifecycle_state {atom.lifecycle_state!r} not in {LIFECYCLE_STATES}"))
    if atom.context_class not in CONTEXT_CLASSES:
        failures.append(ValidationFailure(
            "VOCAB_UNKNOWN_VALUE", s,
            f"context_class {atom.context_class!r} not in {CONTEXT_CLASSES}"))

    # --- significance / tokens ---
    if not isinstance(atom.significance, int) or isinstance(atom.significance, bool) \
            or not (1 <= atom.significance <= 5):
        failures.append(ValidationFailure(
            "SCHEMA_BAD_TYPE", s, "significance must be an integer in 1..5"))
    if not isinstance(atom.estimated_tokens, int) or isinstance(atom.estimated_tokens, bool) \
            or atom.estimated_tokens < 0:
        failures.append(ValidationFailure(
            "SCHEMA_BAD_TYPE", s, "estimated_tokens must be a non-negative integer"))

    # --- expiry ---
    if atom.expires_at is not None and not (
            isinstance(atom.expires_at, str) and _is_iso(atom.expires_at)):
        failures.append(ValidationFailure(
            "EXPIRY_INVALID", s, "expires_at must be None or an ISO date/datetime"))
    if atom.created_at is not None and not (
            isinstance(atom.created_at, str) and _is_iso(atom.created_at)):
        failures.append(ValidationFailure(
            "EXPIRY_INVALID", s, "created_at must be None or an ISO date/datetime"))

    # --- dependencies ---
    if not isinstance(atom.dependencies, (list, tuple)):
        failures.append(ValidationFailure(
            "SCHEMA_BAD_TYPE", s, "dependencies must be a list of atom IDs"))
    else:
        for dep in atom.dependencies:
            if not isinstance(dep, str) or not _ATOM_ID_RE.match(dep):
                failures.append(ValidationFailure(
                    "DEPENDENCY_MISSING", s, f"invalid dependency ID: {dep!r}"))
            elif known_atom_ids is not None and dep not in known_atom_ids:
                failures.append(ValidationFailure(
                    "DEPENDENCY_MISSING", s,
                    f"dependency {dep!r} not present in atom universe"))
        if atom.atom_id in atom.dependencies:
            failures.append(ValidationFailure(
                "DEPENDENCY_CYCLE", s, "atom depends on itself"))

    # --- truth/lifecycle coherence (fidelity policy) ---
    if atom.truth_status == "REFUTED" and atom.lifecycle_state not in ("TOMBSTONED", "EXPIRED"):
        failures.append(ValidationFailure(
            "TRUTH_LIFECYCLE_CONFLICT", s,
            "REFUTED atoms must be TOMBSTONED or EXPIRED, not active"))
    if atom.truth_status == "SUPERSEDED" and atom.lifecycle_state == "ACTIVE":
        failures.append(ValidationFailure(
            "TRUTH_LIFECYCLE_CONFLICT", s,
            "SUPERSEDED atoms must not be ACTIVE"))
    if atom.truth_status == "SUPERSEDED" and atom.superseded_by is None:
        failures.append(ValidationFailure(
            "TRUTH_LIFECYCLE_CONFLICT", s,
            "SUPERSEDED atoms must name superseded_by"))
    if atom.superseded_by is not None:
        if not isinstance(atom.superseded_by, str) or not _ATOM_ID_RE.match(atom.superseded_by):
            failures.append(ValidationFailure(
                "DEPENDENCY_MISSING", s, "superseded_by must be a valid atom ID"))
        elif known_atom_ids is not None and atom.superseded_by not in known_atom_ids:
            failures.append(ValidationFailure(
                "DEPENDENCY_MISSING", s,
                f"superseded_by target {atom.superseded_by!r} is absent"))
    if atom.context_class == "BURNT_OR_REFUTED" and atom.truth_status not in ("REFUTED", "SUPERSEDED"):
        failures.append(ValidationFailure(
            "TRUTH_LIFECYCLE_CONFLICT", s,
            "BURNT_OR_REFUTED class requires REFUTED or SUPERSEDED truth status"))

    if atom.context_class == "EXACT_PROTECTED" and not protected_fields:
        failures.append(ValidationFailure(
            "PROTECTED_FIELD_MUTATED", s,
            "EXACT_PROTECTED atoms must declare at least one protected field"))

    # --- protected fields must exist and match source-bound digests (invariant 1) ---
    if len(protected_fields) != len(set(protected_fields)):
        failures.append(ValidationFailure(
            "SCHEMA_BAD_TYPE", s, "protected_fields must not contain duplicates"))
    for pf in protected_fields:
        if pf not in payload:
            failures.append(ValidationFailure(
                "PROTECTED_FIELD_MUTATED", pf,
                f"protected field {pf!r} not present in payload"))
            continue
        expected = protected_digests.get(pf)
        if not isinstance(expected, str) or not _DIGEST_RE.match(expected):
            failures.append(ValidationFailure(
                "DIGEST_INVALID", pf,
                f"protected field {pf!r} lacks a valid source-bound digest"))
            continue
        try:
            actual = canonical_digest(payload[pf])
        except CanonicalError as exc:
            failures.append(ValidationFailure(
                "SCHEMA_BAD_TYPE", pf,
                f"protected field {pf!r} is not canonically serializable: {exc}"))
            continue
        if actual != expected:
            failures.append(ValidationFailure(
                "PROTECTED_FIELD_MUTATED", pf,
                f"protected field {pf!r} does not match its source-bound digest"))

    invalid_digest_keys = [key for key in protected_digests if not isinstance(key, str)]
    if invalid_digest_keys:
        failures.append(ValidationFailure(
            "SCHEMA_BAD_TYPE", s, "protected_field_digests keys must be strings"))
    unexpected_digests = set(
        key for key in protected_digests if isinstance(key, str)
    ) - set(protected_fields)
    if unexpected_digests:
        failures.append(ValidationFailure(
            "SCHEMA_UNKNOWN_FIELD", s,
            f"protected digests without declared fields: {sorted(unexpected_digests)}"))

    # --- canonical serializability of payload ---
    try:
        canonical_serialize(atom.to_dict())
    except (CanonicalError, TypeError, ValueError) as exc:
        failures.append(ValidationFailure(
            "SCHEMA_BAD_TYPE", s, f"payload not canonically serializable: {exc}"))

    return failures


def _detect_dependency_cycle(atoms: Mapping[str, ContextAtom]) -> List[str]:
    """Return atom IDs involved in dependency cycles (empty if none)."""
    cycles: set = set()
    for start in atoms:
        stack = [(start, [start])]
        seen_local: set = set()
        while stack:
            node, path = stack.pop()
            if node in seen_local:
                continue
            seen_local.add(node)
            for dep in atoms.get(node, ContextAtom(
                    "x", "", "", {}, "UNKNOWN", 1, "ACTIVE", "EPHEMERAL",
                    (), None, 0, {})).dependencies:
                if dep == start:
                    if node != start:
                        cycles.add(min(path))
                elif dep in atoms:
                    stack.append((dep, path + [dep]))
    return sorted(cycles)


def validate_manifest(manifest: ContextManifest,
                      atoms: Sequence[ContextAtom]) -> List[ValidationFailure]:
    """Validate a manifest against its atom set. Returns failure list."""
    failures: List[ValidationFailure] = []
    m = manifest.manifest_id

    if not isinstance(m, str) or not _ATOM_ID_RE.match(m):
        failures.append(ValidationFailure(
            "ID_INVALID", str(m), "manifest_id must use the stable ID syntax"))

    if isinstance(manifest.included_atom_ids, (list, tuple)):
        included_ids = tuple(manifest.included_atom_ids)
    else:
        failures.append(ValidationFailure(
            "SCHEMA_BAD_TYPE", str(m), "included_atom_ids must be a list of strings"))
        included_ids = ()
    if any(not isinstance(aid, str) for aid in included_ids):
        failures.append(ValidationFailure(
            "SCHEMA_BAD_TYPE", str(m), "included_atom_ids must contain only strings"))
        included_ids = tuple(aid for aid in included_ids if isinstance(aid, str))
    if len(included_ids) != len(set(included_ids)):
        failures.append(ValidationFailure(
            "ATOM_ID_DUPLICATE", str(m), "included_atom_ids must not contain duplicates"))

    if isinstance(manifest.omissions, (list, tuple)):
        omissions = tuple(manifest.omissions)
    else:
        failures.append(ValidationFailure(
            "SCHEMA_BAD_TYPE", str(m), "omissions must be a list"))
        omissions = ()
    if isinstance(manifest.tombstones, (list, tuple)):
        tombstones = tuple(manifest.tombstones)
    else:
        failures.append(ValidationFailure(
            "SCHEMA_BAD_TYPE", str(m), "tombstones must be a list"))
        tombstones = ()
    if not isinstance(manifest.compression_lineage, (list, tuple)):
        failures.append(ValidationFailure(
            "SCHEMA_BAD_TYPE", str(m), "compression_lineage must be a list of objects"))
    else:
        for entry in manifest.compression_lineage:
            if not isinstance(entry, dict):
                failures.append(ValidationFailure(
                    "SCHEMA_BAD_TYPE", str(m),
                    "compression_lineage entries must be objects"))
                continue
            try:
                canonical_serialize(entry)
            except CanonicalError as exc:
                failures.append(ValidationFailure(
                    "SCHEMA_BAD_TYPE", str(m),
                    f"compression_lineage entry is not canonical: {exc}"))

    # --- contract version ---
    if manifest.contract_version not in (CONTRACT_VERSION, LEGACY_CONTRACT_VERSION):
        failures.append(ValidationFailure(
            "SCHEMA_BAD_TYPE", m,
            f"contract_version {manifest.contract_version!r} != {CONTRACT_VERSION!r}"))

    # --- budget ledger consistency (invariant 8) ---
    b = manifest.budget
    if not isinstance(b, BudgetLedger):
        failures.append(ValidationFailure(
            "SCHEMA_BAD_TYPE", str(m), "budget must be a BudgetLedger"))
        b = BudgetLedger(0, 0, 0, 0, 0)
    budget_values_valid = True
    for name in ("model_window_tokens", "bootstrap_cost_tokens",
                 "output_reserve_tokens", "tool_result_reserve_tokens",
                 "safety_margin_tokens"):
        v = getattr(b, name)
        if not isinstance(v, int) or isinstance(v, bool) or v < 0:
            budget_values_valid = False
            failures.append(ValidationFailure(
                "BUDGET_LEDGER_INCONSISTENT", m, f"{name} must be a non-negative integer"))
    if budget_values_valid and b.usable_input_budget < 0:
        failures.append(ValidationFailure(
            "BUDGET_LEDGER_INCONSISTENT", m,
            "usable_input_budget is negative; reserves exceed model window"))

    # --- atom universe checks ---
    atom_by_id: Dict[str, ContextAtom] = {}
    valid_atoms: List[ContextAtom] = []
    for atom in atoms:
        if not isinstance(atom, ContextAtom):
            failures.append(ValidationFailure(
                "SCHEMA_BAD_TYPE", str(m), "atom set contains a non-ContextAtom value"))
            continue
        valid_atoms.append(atom)
        if not isinstance(atom.atom_id, str):
            continue
        if atom.atom_id in atom_by_id:
            failures.append(ValidationFailure(
                "ATOM_ID_DUPLICATE", atom.atom_id, "duplicate atom_id in atom set"))
        atom_by_id[atom.atom_id] = atom

    known_ids = set(atom_by_id)
    for atom in valid_atoms:
        failures.extend(validate_atom(atom, known_ids))

    cycles = _detect_dependency_cycle(atom_by_id)
    for cid in cycles:
        failures.append(ValidationFailure(
            "DEPENDENCY_CYCLE", cid, "dependency cycle detected"))

    # --- included IDs must exist ---
    for aid in included_ids:
        if aid not in atom_by_id:
            failures.append(ValidationFailure(
                "SCHEMA_MISSING_FIELD", m, f"included atom {aid!r} not in atom set"))

    # --- dependency closure of included set ---
    included = set(included_ids)
    for aid in included:
        atom = atom_by_id.get(aid)
        if atom is None:
            continue
        for dep in atom.dependencies:
            if dep not in included and dep in atom_by_id:
                failures.append(ValidationFailure(
                    "DEPENDENCY_MISSING", aid,
                    f"dependency {dep!r} is in the atom set but not included"))

        if atom.context_class in ("BURNT_OR_REFUTED", "ARCHIVE_ONLY") or \
                atom.truth_status in ("SUPERSEDED", "REFUTED") or \
                atom.lifecycle_state in ("EXPIRED", "SUPERSEDED", "ARCHIVED", "TOMBSTONED"):
            failures.append(ValidationFailure(
                "TRUTH_LIFECYCLE_CONFLICT", aid,
                "inactive, burnt/refuted, or archive-only atoms cannot be included"))

    # --- budget: included tokens must fit usable budget (invariant 8) ---
    total = sum(atom_by_id[a].estimated_tokens for a in included if a in atom_by_id)
    if budget_values_valid and total > b.usable_input_budget:
        failures.append(ValidationFailure(
            "BUDGET_LEDGER_INCONSISTENT", m,
            f"included tokens {total} exceed usable budget {b.usable_input_budget}"))
    if not isinstance(manifest.total_included_tokens, int) or \
            isinstance(manifest.total_included_tokens, bool) or \
            manifest.total_included_tokens != total:
        failures.append(ValidationFailure(
            "BUDGET_LEDGER_INCONSISTENT", m,
            f"total_included_tokens {manifest.total_included_tokens} != computed {total}"))

    # --- omissions: reason vocabulary + subject must be a known atom ---
    valid_omissions: List[OmissionRecord] = []
    for om in omissions:
        if not isinstance(om, OmissionRecord):
            failures.append(ValidationFailure(
                "SCHEMA_BAD_TYPE", str(m), "omissions contain a non-OmissionRecord value"))
            continue
        if not isinstance(om.atom_id, str):
            failures.append(ValidationFailure(
                "SCHEMA_BAD_TYPE", str(m), "omission atom_id must be a string"))
            continue
        if not isinstance(om.reason, str):
            failures.append(ValidationFailure(
                "SCHEMA_BAD_TYPE", om.atom_id, "omission reason must be a string"))
            continue
        if om.detail is not None and not isinstance(om.detail, str):
            failures.append(ValidationFailure(
                "SCHEMA_BAD_TYPE", om.atom_id, "omission detail must be a string or None"))
        valid_omissions.append(om)
        if om.reason not in OMISSION_REASONS:
            failures.append(ValidationFailure(
                "OMISSION_REASON_INVALID", om.atom_id,
                f"reason {om.reason!r} not in {OMISSION_REASONS}"))
        if om.atom_id not in atom_by_id and om.atom_id not in included:
            failures.append(ValidationFailure(
                "SCHEMA_MISSING_FIELD", m,
                f"omission references unknown atom {om.atom_id!r}"))

    # --- included atoms must not be omitted (and vice versa) ---
    omitted_ids = {o.atom_id for o in valid_omissions}
    if len(omitted_ids) != len(valid_omissions):
        failures.append(ValidationFailure(
            "ATOM_ID_DUPLICATE", str(m), "an atom may have only one omission record"))
    overlap = included & omitted_ids
    if overlap:
        failures.append(ValidationFailure(
            "SCHEMA_BAD_TYPE", m,
            f"atoms both included and omitted: {sorted(overlap)}"))

    unaccounted = known_ids - included - omitted_ids
    if unaccounted:
        failures.append(ValidationFailure(
            "SCHEMA_MISSING_FIELD", m,
            f"atoms lack an inclusion or omission decision: {sorted(unaccounted)}"))

    # --- tombstones ---
    for t in tombstones:
        if not isinstance(t, Tombstone):
            failures.append(ValidationFailure(
                "SCHEMA_BAD_TYPE", str(m), "tombstones contain a non-Tombstone value"))
            continue
        if manifest.contract_version == LEGACY_CONTRACT_VERSION and t.record_version != 1:
            failures.append(ValidationFailure("SCHEMA_BAD_TYPE", str(t.tombstone_id),
                                              "legacy manifest cannot carry v2 tombstones"))
        try:
            t.validate_extensions()
        except ValueError as exc:
            failures.append(ValidationFailure("SCHEMA_BAD_TYPE", str(t.tombstone_id), str(exc)))
        if not isinstance(t.tombstone_id, str) or not _ATOM_ID_RE.match(t.tombstone_id):
            failures.append(ValidationFailure(
                "ID_INVALID", str(t.tombstone_id), "invalid tombstone_id"))
        if not isinstance(t.rejected_claim, str) or not t.rejected_claim.strip() or \
                not isinstance(t.rejection_reason, str) or not t.rejection_reason.strip():
            failures.append(ValidationFailure(
                "TOMBSTONE_INCOMPLETE", t.tombstone_id,
                "tombstone must state rejected claim and rejection reason"))
        if not isinstance(t.evidence_digest, str) or not _DIGEST_RE.match(t.evidence_digest):
            failures.append(ValidationFailure(
                "DIGEST_INVALID", t.tombstone_id,
                "tombstone evidence_digest must be 64-char lowercase hex"))
        if not isinstance(t.evidence_pointer, str) or not t.evidence_pointer.strip():
            failures.append(ValidationFailure(
                "SOURCE_POINTER_INVALID", t.tombstone_id,
                "tombstone evidence_pointer must be non-empty"))
        if t.replacement_atom_id is not None and (
                not isinstance(t.replacement_atom_id, str)
                or t.replacement_atom_id not in atom_by_id):
            failures.append(ValidationFailure(
                "DEPENDENCY_MISSING", t.tombstone_id,
                f"replacement atom {t.replacement_atom_id!r} not in atom set"))
        if t.record_version == 1 and t.truth_status != "REFUTED":
            failures.append(ValidationFailure(
                "TRUTH_LIFECYCLE_CONFLICT", t.tombstone_id,
                "tombstone truth_status must be REFUTED"))
        if not isinstance(t.created_at, str) or not _is_iso(t.created_at):
            failures.append(ValidationFailure(
                "EXPIRY_INVALID", t.tombstone_id,
                "tombstone created_at must be an ISO date/datetime"))

    # --- verifier outcome vocabulary ---
    if manifest.verifier_outcome not in ("PASS", "FAIL", "PENDING"):
        failures.append(ValidationFailure(
            "SCHEMA_BAD_TYPE", m,
            f"verifier_outcome {manifest.verifier_outcome!r} not in PASS/FAIL/PENDING"))

    try:
        expected_packet_digest = canonical_digest([
            atom_by_id[aid].to_dict() for aid in included_ids if aid in atom_by_id
        ])
    except (CanonicalError, TypeError, ValueError) as exc:
        failures.append(ValidationFailure(
            "SCHEMA_BAD_TYPE", m, f"included packet is not canonical: {exc}"))
    else:
        if not isinstance(manifest.packet_digest, str) or not _DIGEST_RE.match(manifest.packet_digest):
            failures.append(ValidationFailure(
                "DIGEST_INVALID", m, "packet_digest must be a lowercase SHA-256"))
        elif manifest.packet_digest != expected_packet_digest:
            failures.append(ValidationFailure(
                "MANIFEST_DIGEST_MISMATCH", m,
                "packet_digest does not match the canonical included packet"))

    return failures


def build_manifest(
    manifest_id: str,
    budget: BudgetLedger,
    atoms: Sequence[ContextAtom],
    included_atom_ids: Sequence[str],
    omissions: Sequence[OmissionRecord] = (),
    tombstones: Sequence[Tombstone] = (),
    compression_lineage: Sequence[Mapping[str, Any]] = (),
    verifier_outcome: str = "PENDING",
    verifier_detail: Optional[str] = None,
) -> ContextManifest:
    """Construct a manifest and compute total_included_tokens deterministically."""
    atom_by_id = {a.atom_id: a for a in atoms}
    total = sum(atom_by_id[a].estimated_tokens for a in included_atom_ids
                if a in atom_by_id)
    packet_digest = canonical_digest([
        atom_by_id[a].to_dict() for a in included_atom_ids if a in atom_by_id
    ])
    return ContextManifest(
        manifest_id=manifest_id,
        contract_version=CONTRACT_VERSION,
        budget=budget,
        included_atom_ids=tuple(included_atom_ids),
        omissions=tuple(omissions),
        tombstones=tuple(tombstones),
        compression_lineage=tuple(dict(c) for c in compression_lineage),
        verifier_outcome=verifier_outcome,
        verifier_detail=verifier_detail,
        total_included_tokens=total,
        packet_digest=packet_digest,
    )
