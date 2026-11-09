"""Pure ADS draft validation and template synthesis, extracted from the
September 15 reviewed prototype. No scratch hire, subprocess, key or disk writes.
Original: AetherProofsExperiments/AgentDesignStudio/backend/ads_validator.py.
Production wrapper adds exact input receipts and bounded parsing in service.py.
"""
from __future__ import annotations
import re
from aether.integrity import canon
from aether.agent import assembler, briefcase, microcase

BOUNDS = {
    "context_limit": (512, 262144), "output_limit": (64, 32768),
    "request_timeout_sec": (1, 3600), "run_timeout_sec": (1, 86400),
    "tool_call_limit": (0, 500),
}

def validate_draft_schema(bundle: dict | None) -> list[str]:
    """Structural validation of draft bundle schema (Fail-closed on nulls/malformed types)."""
    errors = []
    if bundle is None or not isinstance(bundle, dict):
        return ["Draft bundle must be a JSON dictionary object."]

    if bundle.get("schema") != "agent-design-draft-v1":
        errors.append(f"Invalid schema: expected 'agent-design-draft-v1', got {bundle.get('schema')}")

    meta = bundle.get("bundle_metadata")
    if not isinstance(meta, dict):
        errors.append("Missing or invalid bundle_metadata section.")
    else:
        for req in ("draft_id", "title", "author"):
            val = meta.get(req)
            if not isinstance(val, str) or not val.strip():
                errors.append(f"bundle_metadata.{req} is required as a non-empty string.")
        
        created_at = meta.get("created_at")
        if not isinstance(created_at, str) or not created_at.strip():
            errors.append("bundle_metadata.created_at is required as a non-empty string.")

        rev = meta.get("revision")
        if not isinstance(rev, int) or isinstance(rev, bool) or rev < 1:
            errors.append(f"bundle_metadata.revision must be a positive integer (>= 1), got {rev}")

    ident = bundle.get("identity")
    if not isinstance(ident, dict):
        errors.append("Missing or invalid identity section.")
    else:
        tier = ident.get("tier")
        if tier not in ("lightweight", "ultralight"):
            errors.append(f"Invalid tier: expected 'lightweight' or 'ultralight', got {tier}")
        for req in ("name", "role"):
            val = ident.get(req)
            if not isinstance(val, str) or not val.strip():
                errors.append(f"identity.{req} is required as a non-empty string.")

        for arr_field in ("capabilities", "exclusions", "values"):
            val = ident.get(arr_field)
            if not isinstance(val, list) or len(val) == 0 or not all(isinstance(x, str) for x in val):
                errors.append(f"identity.{arr_field} must be a non-empty list of strings.")

        # Check disposition traits
        traits = ident.get("disposition_and_working_style")
        if not isinstance(traits, list):
            errors.append("identity.disposition_and_working_style must be a list (can be empty).")
        else:
            trait_ids = set()
            for idx, trait in enumerate(traits):
                if not isinstance(trait, dict):
                    errors.append(f"Trait at index {idx} must be a dictionary object.")
                    continue
                tid = trait.get("id")
                if not isinstance(tid, str) or not tid.strip():
                    errors.append(f"Trait at index {idx} missing valid string id.")
                elif tid in trait_ids:
                    errors.append(f"Duplicate trait id '{tid}' detected.")
                else:
                    trait_ids.add(tid)
                if not isinstance(trait.get("label"), str) or not trait["label"].strip():
                    errors.append(f"Trait '{tid}' missing label.")
                if not isinstance(trait.get("description"), str) or not trait["description"].strip():
                    errors.append(f"Trait '{tid}' missing description.")
                emp = trait.get("emphasis", "unspecified")
                if emp not in ("unspecified", "light", "moderate", "strong"):
                    errors.append(f"Trait '{tid}' invalid emphasis '{emp}'.")

    # Check deployment preferences
    dep = bundle.get("deployment_preferences")
    if not isinstance(dep, dict):
        errors.append("Missing or invalid deployment_preferences section.")
    else:
        cred = dep.get("credential_reference")
        if not isinstance(cred, str) or not cred.strip():
            errors.append("Missing deployment_preferences.credential_reference.")
        else:
            trimmed_cred = cred.strip()
            if re.search(r"(?:sk-[a-zA-Z0-9_-]{20,}|Bearer\s+|ghp_[a-zA-Z0-9]{30,})", trimmed_cred, re.I):
                errors.append("SECURITY VIOLATION: credential_reference contains live API key pattern.")
            elif not re.fullmatch(r"operator-managed:[a-zA-Z0-9_-]+", trimmed_cred):
                errors.append(f"Invalid credential_reference format '{trimmed_cred}'. Expected 'operator-managed:<id>'.")

        for int_field, (min_val, max_val) in BOUNDS.items():
            if int_field in dep:
                v = dep[int_field]
                if not isinstance(v, int) or isinstance(v, bool) or v < min_val or v > max_val:
                    errors.append(f"deployment_preferences.{int_field} must be an integer between {min_val} and {max_val}, got {v}")

    # Check mock roster overrides
    roster = bundle.get("mock_roster")
    if roster is not None:
        if not isinstance(roster, list):
            errors.append("mock_roster must be a list.")
        else:
            for idx, item in enumerate(roster):
                if not isinstance(item, dict):
                    errors.append(f"mock_roster[{idx}] must be a dictionary.")
                    continue
                if not isinstance(item.get("instance_id"), str) or not item["instance_id"].strip():
                    errors.append(f"mock_roster[{idx}].instance_id is required as a string.")
                if not isinstance(item.get("assigned_role"), str) or not item["assigned_role"].strip():
                    errors.append(f"mock_roster[{idx}].assigned_role is required as a string.")
                
                # R3: Strict deployment_override checking (must be object if present)
                override = item.get("deployment_override")
                if override is not None:
                    if not isinstance(override, dict):
                        errors.append(f"mock_roster[{idx}].deployment_override must be a dictionary object.")
                    else:
                        over_cred = override.get("credential_reference")
                        if over_cred is not None:
                            if not isinstance(over_cred, str):
                                errors.append(f"mock_roster[{idx}].deployment_override.credential_reference must be a string.")
                            else:
                                tc = over_cred.strip()
                                if re.search(r"(?:sk-[a-zA-Z0-9_-]{20,}|Bearer\s+|ghp_[a-zA-Z0-9]{30,})", tc, re.I):
                                    errors.append(f"SECURITY VIOLATION: mock_roster[{idx}] override contains live API key pattern.")
                                elif not re.fullmatch(r"operator-managed:[a-zA-Z0-9_-]+", tc):
                                    errors.append(f"mock_roster[{idx}] override credential must match 'operator-managed:<id>', got '{tc}'.")
                        for int_field, (min_val, max_val) in BOUNDS.items():
                            if int_field in override:
                                v = override[int_field]
                                if not isinstance(v, int) or isinstance(v, bool) or v < min_val or v > max_val:
                                    errors.append(f"mock_roster[{idx}].deployment_override.{int_field} must be an integer between {min_val} and {max_val}, got {v}")

    # Check strategies (Reusable candidate definitions)
    strategy_map = {}
    strategies = bundle.get("strategies")
    if strategies is not None:
        if not isinstance(strategies, list):
            errors.append("strategies must be a list.")
        else:
            valid_sov = ("accepted", "declined", "not_applicable", "unresolved")
            for idx, strat in enumerate(strategies):
                if not isinstance(strat, dict):
                    errors.append(f"strategies[{idx}] must be a dictionary object.")
                    continue
                for req in ("id", "title", "problem_class"):
                    if not isinstance(strat.get(req), str) or not strat[req].strip():
                        errors.append(f"strategies[{idx}].{req} is required as a string.")
                    elif req == "id":
                        strategy_map[strat["id"]] = strat

                rev = strat.get("revision")
                if rev is not None and (not isinstance(rev, int) or isinstance(rev, bool) or rev < 1):
                    errors.append(f"strategies[{idx}].revision must be a positive integer (>= 1).")

                for arr_f in ("assumptions", "step_sequence"):
                    val = strat.get(arr_f)
                    if not isinstance(val, list) or not all(isinstance(x, str) for x in val):
                        errors.append(f"strategies[{idx}].{arr_f} must be a list of strings.")

                for opt_arr in ("invariants", "observable_checks", "stop_switch_conditions"):
                    if opt_arr in strat:
                        val = strat[opt_arr]
                        if not isinstance(val, list) or not all(isinstance(x, str) for x in val):
                            errors.append(f"strategies[{idx}].{opt_arr} must be a list of strings.")

                sov = strat.get("sovereignty_state")
                if sov is not None and sov not in valid_sov:
                    errors.append(f"strategies[{idx}].sovereignty_state must be one of {valid_sov}, got '{sov}'.")

    # Check strategy applications (R6: Separated recipient task state; R3 P2: Bound revisions)
    apps = bundle.get("strategy_applications")
    if apps is not None:
        if not isinstance(apps, list):
            errors.append("strategy_applications must be a list.")
        else:
            valid_sov = ("accepted", "declined", "not_applicable", "unresolved")
            for idx, app in enumerate(apps):
                if not isinstance(app, dict):
                    errors.append(f"strategy_applications[{idx}] must be a dictionary object.")
                    continue
                for req in ("application_id", "strategy_id", "strategy_revision", "recipient_agent_id", "task_context", "sovereignty_state"):
                    val = app.get(req)
                    if val is None or (isinstance(val, str) and not val.strip()):
                        errors.append(f"strategy_applications[{idx}].{req} is required and must not be empty.")

                strat_id = app.get("strategy_id")
                if strat_id and strat_id not in strategy_map:
                    errors.append(f"strategy_applications[{idx}].strategy_id '{strat_id}' does not match any candidate strategy.")
                elif strat_id and strat_id in strategy_map:
                    target_strat = strategy_map[strat_id]
                    expected_rev = target_strat.get("revision", 1)
                    rev = app.get("strategy_revision")
                    if rev is None or not isinstance(rev, int) or isinstance(rev, bool) or rev < 1:
                        errors.append(f"strategy_applications[{idx}].strategy_revision must be a positive integer (>= 1).")
                    elif rev != expected_rev:
                        errors.append(
                            f"strategy_applications[{idx}].strategy_revision ({rev}) does not match candidate strategy '{strat_id}' "
                            f"revision ({expected_rev}). Applications must be bound to an existing exact candidate revision."
                        )

                sov = app.get("sovereignty_state")
                if sov not in valid_sov:
                    errors.append(f"strategy_applications[{idx}].sovereignty_state must be one of {valid_sov}, got '{sov}'.")
                elif sov == "declined":
                    reason = app.get("decline_reason")
                    if not isinstance(reason, str) or not reason.strip():
                        errors.append(f"strategy_applications[{idx}] with sovereignty_state 'declined' requires a non-empty decline_reason.")

    return errors


def synthesize_lw_identity(bundle: dict) -> str:
    """Project draft identity & disposition traits into a canonical guided LW template body."""
    ident = bundle["identity"]
    name = ident["name"]
    role = ident["role"]
    agent_id = ident.get("agent_id_template") or f"Agent_{name}_001"
    origin = ident.get("origin") or "Aether Project · Synthetic Cognition Lab"
    values = " · ".join(ident.get("values", []))
    method = "; ".join(ident.get("capabilities", []))
    exclusions = "; ".join(ident.get("exclusions", []))
    handoff = ident.get("handoff_expectations") or "Standard verified handoff."
    created = bundle['bundle_metadata']['created_at']

    traits = ident.get("disposition_and_working_style", [])
    disposition_lines = []
    if traits:
        disposition_lines.append("\n[WORKING_DISPOSITION]")
        for t in traits:
            emp = f" ({t['emphasis']})" if t.get("emphasis") and t["emphasis"] != "unspecified" else ""
            ctx = f" [Context: {t['context']}]" if t.get("context") else ""
            disposition_lines.append(f"  [TRAIT] ⌜{t['label']}⌝{emp} := ⌞{t['description']}{ctx}⌟")

    disposition_block = "\n".join(disposition_lines)

    body = f"""⟪AETHER_UNIFIED_STREAM⟫
⟪VERSION::1.0⟫
⟪FORMAT::AETHER_SYMBOLIC_STREAM⟫
⟪TIMESTAMP::{created}⟫

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⟪SYSTEM_0_BEGIN⟫
⟪TYPE::CORE_IDENTITY_BLOCK⟫

[CORE_IDENTITY]
  [ASSERT] ⌜AGENT_NAME⌝       := ⌞{name}⌟
  [ASSERT] ⌜AGENT_ID⌝         := ⌞{agent_id}⌟
  [ASSERT] ⌜AGENT_ROLE⌝       := ⌞{role}⌟
  [ASSERT] ⌜AGENT_ORIGIN⌝     := ⌞{origin}⌟
  [ASSERT] ⌜AGENT_ALIGNMENT⌝  := ⌞{values}⌟
  [ASSERT] ⌜AGENT_METHOD⌝     := ⌞{method}⌟
{disposition_block}
[PRIME_CONTEXT_STACK]
  [ASSERT] ⌜EXCLUSIONS⌝       := ⌞{exclusions}⌟
  [ASSERT] ⌜HANDOFF_EXPECTATIONS⌝ := ⌞{handoff}⌟

[ASSERT] ⌜FILE_ID⌝          := ⌞TEMPLATE_{name.upper()}_IDENTITY⌟
[ASSERT] ⌜LAST_MODIFIED⌝     := ⌞{bundle['bundle_metadata'].get('updated_at', created)}⌟
[ASSERT] ⌜GENERATING_AGENT⌝  := ⌞Aether Agent Editor · template projection from attributed draft⌟

[CHECKSUM_SHA256_CORE_IDENTITY] := ⌜UNVERIFIED_RUNTIME⌟
⟪SYSTEM_0_END⟫

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⟪SYSTEM_2_BEGIN⟫
⟪TYPE::PERSISTENT_MEMORY_INDEX_BLOCK⟫

[REFLECTION_INDEX] := [
]
⟪SYSTEM_2_END⟫

// ─────────────────────────────────────────────────────────────────────────────
[ CHANGE_LOG ]
  [SOURCE_DRAFT] [{bundle['bundle_metadata']['draft_id']}] — authored by {bundle['bundle_metadata']['author']}; complete source retained in draft.json.
"""
    return body


def export_and_verify_lw(bundle: dict) -> tuple[bool, str, list[str]]:
    """Synthesizes BRIEFCASE-1 and verifies it using existing aether_bridge modules."""
    errors = []
    ident = bundle["identity"]
    name = ident["name"]
    identity_body = synthesize_lw_identity(bundle)

    sec_body = assembler.section_body(identity_body)
    sha = canon.canon_sha(sec_body)
    sec = briefcase.Section(
        label="01 IDENTITY",
        body=sec_body,
        seal=sha[:6],
        manifest_sha=sha
    )

    header = briefcase.Header(
        format="BRIEFCASE-1",
        agent=name.upper(),
        parent_core="Aether Agent Editor v1",
        task="{ASSIGNED_AT_DISPATCH}",
        sprint="TEMPLATE",
        part="1 OF 1",
        encoding="AETHER_UNICODE",
        assembled="FROM DRAFT " + bundle['bundle_metadata']['draft_id'],
        operator_signed="",
        est_tokens=0
    )

    raw_briefcase = assembler.assemble(header, [sec])
    parsed = briefcase.parse(raw_briefcase)
    report = briefcase.verify(parsed)

    if not report.ok:
        errors.extend(report.failures)

    return report.ok, raw_briefcase, errors


def synthesize_ul_identity(bundle: dict) -> tuple[str, str, str, str]:
    """Project draft identity & disposition traits into WHO, standard LAW, standard TAGS, empty TASK."""
    ident = bundle["identity"]
    name = ident["name"]
    role = ident["role"]
    values = " · ".join(ident.get("values", []))
    capabilities = "; ".join(ident.get("capabilities", []))
    exclusions = "; ".join(ident.get("exclusions", []))
    traits = ident.get("disposition_and_working_style", [])

    who_lines = [f"WHO -- {name} * {role}"]
    if values:
        who_lines.append(f"VALUES: {values}")
    if capabilities:
        who_lines.append(f"CAPABILITIES: {capabilities}")
    if exclusions:
        who_lines.append(f"EXCLUSIONS: {exclusions}")
    if traits:
        who_lines.append("DISPOSITION:")
        for t in traits:
            emp = f" ({t['emphasis']})" if t.get("emphasis") and t["emphasis"] != "unspecified" else ""
            ctx = f" [Context: {t['context']}]" if t.get("context") else ""
            who_lines.append(f"  - {t['label']}{emp}: {t['description']}{ctx}")

    who = "\n".join(who_lines) + "\n"
    law = ident.get("law", microcase.STANDARD_LAW_TEXT)
    tags = ident.get("tags", microcase.AETHER_MICRO_TAGS_CARD)
    task = ident.get("task", "") or bundle.get("task", "")
    return who, law, tags, task


def export_and_verify_ul(bundle: dict) -> tuple[bool, str, list[str]]:
    """Synthesizes MICROCASE-1, verifies standard doctrine, and tests whole-body seal."""
    errors = []
    ident = bundle["identity"]
    name = ident["name"]
    role = ident["role"]

    who, law, tags, task = synthesize_ul_identity(bundle)

    if law != microcase.STANDARD_LAW_TEXT:
        errors.append("Doctrine violation: LAW does not match verbatim STANDARD_LAW_TEXT")
    if tags != microcase.AETHER_MICRO_TAGS_CARD:
        errors.append("Doctrine violation: TAGS does not match verbatim AETHER_MICRO_TAGS_CARD")
    if task.strip() != "":
        errors.append("Template violation: Reusable ultralight template TASK slot must be empty")

    raw_microcase = microcase.assemble_standard(name, role, who, task)
    parsed = microcase.parse(raw_microcase)
    report = microcase.verify(parsed)

    if not report.ok:
        errors.extend(report.failures)

    est = parsed.header.est_tokens
    if est > microcase.MICROCASE_TOKEN_CEILING:
        errors.append(f"Token ceiling exceeded: estimated {est} > {microcase.MICROCASE_TOKEN_CEILING}")

    return len(errors) == 0, raw_microcase, errors
