"""Campaign state box (MVP-01 contract, section 4): one PURE projection from a campaign's durable
records to what the operator sees: where it is (stage) and what is happening now (condition).

Inputs:  records, the plain structure records.load_campaign_records builds (no I/O happens here):
           campaign_id        str
           framing            {"state", "last_action", "last_decision", "source"} or None
           planning           [{"version", "status", "source"}]               oldest first
           staffing           [{"status", "operation", "source"}]             oldest first
           execution          [{"run_id", "packets", "events", "controls", "live", "artifact_issues", "source"}]
           attempts           [{"status", "request_status", "generation_id", "store", "source"}]
                              generation attempts; "store" names the record that referenced the
                              generation, and so its stage (FAMILY_STAGES)
           host_runs          [{"store", "run_id", "status", "request_id"?, "source"}]   one per run
                              folder of a native host store; parallel records
           host_histories     [{"store", "status", "request_id", "source"}]   oldest first per store
                              staffing decisions add "version", "record_sha256", "history_head_version";
                              staffing history adds "revision_ref", "version" (_revision_answered)
           closure_packets    [{"status", "packet_id", "source"}]             packet_id: None unless the
                              packet binds (its name and envelope ID are the digest of its content)
           closure_dialogue   [{"status", "integrity", "record_digest", "operation", "packet_id",
                                "presentation_sha256", "source"}]           oldest first
           closure_decisions  [{"decision", "binds", "packet_id", "presentation_sha256", "review_sha256",
                                "source"}]                                  oldest first; binds: the
                              dialogue record digests the decision was taken on
           closure_obligations [{"store", "run_id", "status", "source"}]      interruption obligations
           sources            [{"path", "sha256"}]
           problems           [{"source", "error"}]                           unreadable or malformed records
         Missing keys mean "that store does not exist yet"; optional row keys may be absent.
Outputs: campaign_state(records) -> {"campaign_id", "stage", "condition", "reason", "actions",
         "sources", "signals", "obligations"}; obligations: [{"kind", "condition", "literal",
         "source", "note"}], the unfinished work the operator must see before or at Close.
Never:   reads a file, maps an unknown literal to a known condition, or lets later progress hide
         a refused, paused, running, interrupted, uncertain or unrecognized record.

Rules (contract section 4, made concrete):
1. Every record yields a signal (literal, condition, stage, source) through vocabulary.VOCABULARY.
   Only the latest record of a history counts; an older one still counts while it is UNCERTAIN,
   UNRECOGNIZED, INTERRUPTED, RUNNING or PAUSED (NEVER_DROPPED: progress never hides those).
   An older REFUSED record is dropped by a newer one of the same history, by design: every history
   store here is a version chain (each record names its predecessor), so a newer record IS the
   repair the refusal asked for (a new plan version after REQUEST_REVISION, a new staffing record
   after INVALID_OUTPUT_HELD, an ACCEPT after a closure revision). While a REFUSED record is the
   latest, it counts.
2. A signal's position is (stage, done). A signal is superseded when a signal from ANOTHER store
   is further along and its own condition is NEEDS_YOU, WAITING or DONE: later progress makes an
   earlier decision, wait or completion moot (PLAN_APPROVED makes a held plan proposal moot), and
   nothing else. Records of the same store (two runs, two attempts) are parallel: they always compete.
3. The condition is the most demanding of the remaining signals: UNRECOGNIZED, then the contract
   order UNCERTAIN > INTERRUPTED > REFUSED > NEEDS_YOU > PAUSED > RUNNING > WAITING > DONE.
4. The stage is the furthest stage any signal reached. Actions are those of the winning signal's
   stage (DONE: the next stage's).
5. Obligations (G7) are computed from every signal, superseded or not, and from the records:
   uncertain_effect (any UNCERTAIN record), unrecognized_record, run_not_terminal (a run,
   generation or host attempt still running, paused, waiting, interrupted, activated or blocked;
   a generation still owned or frozen), artifact_not_reviewed (artifacts that differ from their
   receipts; a completed run with no closure packet; a held native contribution or publication with
   no task decision), closure_dialogue_incomplete (a closure host attempt with nothing captured; a
   closure dialogue response recorded after the last operator decision),
   interruption_obligation (a closure obligation still pending or interrupted) and
   accepted_evidence_missing (the latest decision is ACCEPT, but the packet, presentation or review
   it names is not loaded, or they do not bind: the presentation must PRESENT that packet and the
   review must REVIEW that presentation of that packet).
6. Closure is recorded when the winning condition is DONE at the Close stage. The Closed stage is
   shown only when closure is recorded AND no obligation is open: an ACCEPT literal alone, whose
   accepted evidence is gone, never shows Closed. Closure recorded with open
   obligations shows Close and the most demanding of NEEDS_YOU and the obligations' conditions,
   listing them in the reason.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from .vocabulary import (CLOSE, CLOSED, DONE, INTERRUPTED, NEEDS_YOU, PAUSED, PRECEDENCE, PREPARE, REFUSED,
                         RUNNING, STAGES, UNCERTAIN, UNRECOGNIZED, VOCABULARY, WAITING, classify)

PLAN, STAFF, AUTHORIZE, RUN, REVIEW = STAGES[1:6]

# The stage a store's records belong to when the literal itself names none.
FAMILY_STAGES = {
    "framing": PREPARE, "plan_ruling": PLAN, "planning": PLAN, "staffing": STAFF,
    "execution": RUN, "attempts": RUN, "closure_packets": REVIEW,
    "closure_dialogue": CLOSE, "closure_decisions": CLOSE, "problems": PREPARE,
    # Native host stores (records.RUN_STORES, HOST_STORES, HISTORY_STORES). The native review is the
    # plan's review: its findings feed the plan ruling, so it belongs to Plan.
    "minder-supervision": PLAN, "minder-assessment": PLAN, "minder-host-attempts": PLAN,
    "native-reviews": PLAN, "native-host-attempts": PLAN,
    "native-staffing-history": STAFF, "native-staffing-decisions": STAFF, "native-staffing-runs": STAFF,
    "desktop-staffing-host": STAFF,
    "native-staff-contributions": RUN, "native-contribution-host": RUN, "native-artifact-effects": RUN,
    "native-task-decisions": RUN, "native-staff-admission": STAFF, "native-task-budgets": AUTHORIZE,
    "native-closure-runs": CLOSE, "closure-host-attempts": CLOSE,
    "overseer_amendment": PLAN,
}
AMENDMENT_FAMILY = "overseer_amendment"
DISPOSITIONS = {"APPROVE": "APPROVED", "REJECT": "REJECTED", "REQUEST_REVISION": "REQUEST_REVISION"}
HISTORY_FAMILIES = ("native-staffing-history", "native-staffing-decisions", "native-task-decisions")
CLOSURE_FAMILIES = {"closure-host-attempts", "native-closure-runs", "closure_dialogue"}
RUN_FAMILIES = {"execution", "attempts", "planning", "minder-supervision", "minder-assessment",
                "minder-host-attempts", "native-reviews", "native-host-attempts", "native-staffing-runs",
                "desktop-staffing-host", "native-staff-contributions", "native-contribution-host",
                "native-artifact-effects"}

# A stage is DONE only on the operator's authority record (lane F, 2026-10-10): the plan ruling or
# PLAN_APPROVED, the staffing approval AND staff admission, the task budget activation, the closure
# ACCEPT. A generation's or host run's completion (normal_completed, AETHER_*NORMAL_COMPLETED,
# returned, a reviewed findings run) is work finished, not a stage completed: it never advances the
# frontier past the decision it awaits and never supersedes that decision. These families report work.
WORK_FAMILIES = RUN_FAMILIES | CLOSURE_FAMILIES | {"closure-host-attempts"}
STAGE_AWAITS = {PLAN: "the plan awaits review and your ruling",
                STAFF: "the staffing awaits your approval and staff admission",
                AUTHORIZE: "the run awaits your authorization (task budget activation)",
                RUN: "the work awaits your task decision",
                REVIEW: "the work awaits its closure packet and review"}
STAGE_ENTRY = {"HEAVYWEIGHT_SELECTED"}
# Staff completes on admission: the approval alone awaits the operator's staff admission.
STAFF_APPROVAL = {"STAFFING_APPROVED"}

ORDER = (UNRECOGNIZED,) + PRECEDENCE
SUPERSEDABLE = {NEEDS_YOU, WAITING, DONE}
NEVER_DROPPED = {UNCERTAIN, UNRECOGNIZED, INTERRUPTED, RUNNING, PAUSED}
OPEN_RUN = {RUNNING, PAUSED, WAITING, INTERRUPTED}
CLOSING_OPERATIONS = {"CLOSE", "CLOSE_REVIEW"}   # closure turns after the operator's decision (Fable, 10-12)
OPEN_EXECUTION = {"READY", "RUNNING", "PAUSED", "BLOCKED"}
OPEN_GENERATION = {"generation_owned", "generation_frozen"}
HELD_FOR_TASK_DECISION = {"CONTRIBUTION_EVIDENCE_HELD", "PUBLICATION_COMPLETE"}

MEANING = {
    NEEDS_YOU: "Needs you: the next step is your decision",
    RUNNING: "Running: work is in progress",
    WAITING: "Waiting: blocked on something named that is not you",
    PAUSED: "Paused: stopped on purpose and resumable",
    INTERRUPTED: "Interrupted: the process stopped and nothing is in flight",
    UNCERTAIN: "Uncertain: an effect's outcome is unknown; reconcile it, never replay it",
    REFUSED: "Refused: something was refused and needs the named repair",
    DONE: "Done: the stage is complete",
    UNRECOGNIZED: "Unrecognized state: a record uses a status the campaign vocabulary does not map",
}

VERB = {NEEDS_YOU: "Decide", RUNNING: "Follow", WAITING: "Follow", PAUSED: "Resume or stop",
        INTERRUPTED: "Resume or close", UNCERTAIN: "Reconcile (do not replay)", REFUSED: "Repair",
        DONE: "Continue", UNRECOGNIZED: "Inspect"}

# The existing Mission Control pages for each stage (contract section 3, "Reuses (today)").
STAGE_PAGES = {
    PREPARE: (("Campaign setup", "/projects/new"), ("Choose the lead", "/projects/lead")),
    PLAN: (("Lead and scope", "/projects/lead"), ("Plan proposals", "/projects/proposals"),
           ("Plan ruling", "/projects/plan-ruling")),
    STAFF: (("Staffing", "/projects/staffing"), ("Staffing launch", "/projects/staffing-launch"),
            ("Staff decisions", "/projects/staff-decisions")),
    AUTHORIZE: (("Authorize a run", "/projects/execution"), ("Contribution launch", "/projects/contribution-launch")),
    RUN: (("Run progress", "/projects/execution"),),
    REVIEW: (("Closure packets", "/projects/closure"), ("Review launch", "/projects/review-launch")),
    CLOSE: (("Closure dialogue", "/projects/closure-dialogue"),),
    CLOSED: (("Closure record", "/projects/closure-dialogue"),),
}


def _signal(family, literal, source, note="", stage=None) -> dict:
    """stage: the record's own stage when its store spans stages (generation attempts)."""
    home = stage or FAMILY_STAGES.get(family, PREPARE)
    found = classify(literal)
    if found is None:
        return dict(family=family, literal=literal, condition=UNRECOGNIZED, stage=home,
                    source=source, note=note or f"literal {literal!r} is not in the campaign vocabulary")
    condition, own = found
    return dict(family=family, literal=literal, condition=condition, stage=own or home,
                source=source, note=note or VOCABULARY[literal][2])


def _history(family, rows, key="status") -> List[dict]:
    """The latest row counts; an older one only while it is NEVER_DROPPED (rule 1)."""
    signals = [_signal(family, row.get(key), row.get("source", "")) for row in rows or ()]
    return signals[-1:] + [s for s in signals[:-1] if s["condition"] in NEVER_DROPPED]


def execution_status(run: dict) -> tuple:
    """(literal, note) for one run, replaying its events as staff_execution.inspect does.

    A task intent without a result is RECONCILIATION_REQUIRED unless the caller says the run is
    live in this process: an intent is not evidence that an effect did not occur.
    """
    state, done, pending, acked = "READY", 0, False, 0
    controls = [c.get("action") for c in run.get("controls") or ()]
    for event in run.get("events") or ():
        kind = event.get("kind")
        if kind == "CONTROL":
            action = controls[acked] if acked < len(controls) else None
            mapped = {"PAUSE": "PAUSED", "RESUME": "READY", "STOP": "STOPPED"}.get(action)
            if mapped is None:
                return action, f"control acknowledgment {acked + 1} names no recorded control action"
            state, acked = mapped, acked + 1
        elif kind == "START":
            pending = True
        elif kind == "RESULT":
            status = event.get("status")
            if status == "COMPLETE":
                done, pending = done + 1, False
            elif status == "UNCERTAIN":
                state = "RECONCILIATION_REQUIRED"
            else:
                return status, f"task result status {status!r} is not in the campaign vocabulary"
        elif kind == "BLOCKED":
            state = "BLOCKED"
        else:
            return kind, f"execution event kind {kind!r} is not known to the campaign projection"
    if pending and state != "RECONCILIATION_REQUIRED":
        state = "RUNNING" if run.get("live") else "RECONCILIATION_REQUIRED"
    elif state == "READY" and run.get("packets") and done >= run["packets"]:
        state = "REVIEW_READY"
    if state == "REVIEW_READY" and run.get("artifact_issues"):
        state = "ARTIFACT_REVIEW_REQUIRED"
    return state, ""


def _signals(records: dict) -> List[dict]:
    signals: List[dict] = []
    framing = records.get("framing")
    if framing is None:
        signals.append(dict(family="framing", literal=None, condition=NEEDS_YOU, stage=PREPARE,
                            source=records.get("registry_source", ""),
                            note="no framing record: set the campaign up (objective, sources, deployment)"))
    else:
        signals.append(_signal("framing", framing.get("state"), framing.get("source", "")))
        if framing.get("last_action") == "rule-on-plan":     # the latest lifecycle event is a ruling
            signals.append(_signal("plan_ruling", framing.get("last_decision"), framing.get("source", "")))
    signals += _history("planning", records.get("planning"))
    signals += _history("staffing", records.get("staffing"))
    for run in records.get("execution") or ():
        literal, note = execution_status(run)
        signals.append(_signal("execution", literal, run.get("source", ""), note))
    for row in records.get("attempts") or ():
        stage = FAMILY_STAGES.get(row.get("store"))
        signals.append(_signal("attempts", row.get("status"), row.get("source", ""), stage=stage))
        request = row.get("request_status")
        # A request of unknown outcome inside a still-open generation is never hidden by its generation.
        if row.get("status") in OPEN_GENERATION and request is not None:
            found = classify(request)
            if found is None or found[0] == UNCERTAIN:
                signals.append(_signal("attempts", request, row.get("source", ""), stage=stage))
    for row in records.get("host_runs") or ():
        signals.append(_signal(row.get("store"), row.get("status"), row.get("source", "")))
    histories = list(records.get("host_histories") or ())
    for store in HISTORY_FAMILIES + tuple(sorted({r.get("store") for r in histories} - set(HISTORY_FAMILIES),
                                                 key=str)):
        rows = [r for r in histories if r.get("store") == store]
        found = _history(store, rows)
        if store == "native-staffing-decisions" and rows and _revision_answered(rows[-1], histories):
            # A staffing REQUEST_REVISION is answered by the revised proposal that pins it (lane F, 10-11; the
            # store-local analogue of the closure rule below): the revised proposal's own signal now stands.
            found = found[1:]
        signals += found
    for row in records.get("closure_packets") or ():
        signals.append(_signal("closure_packets", row.get("status"), row.get("source", "")))
    dialogue = list(records.get("closure_dialogue") or ())
    # Closures after the decision (Fable, 10-12; closure-line-range-8): the lead's CLOSE and the reviewer's
    # CLOSE_REVIEW follow the operator's ACCEPT. They are the expected end of the campaign, never an unanswered
    # dialogue: each is DONE at Closed when its evidence is held and valid, else Needs you at Close.
    closing = [r for r in dialogue if r.get("operation") in CLOSING_OPERATIONS]
    dialogue = [r for r in dialogue if r.get("operation") not in CLOSING_OPERATIONS]
    if dialogue:
        latest = dialogue[-1]
        signals.append(_signal("closure_dialogue", latest.get("status"), latest.get("source", "")))
        if latest.get("integrity") is not None:
            signals.append(_signal("closure_dialogue", latest.get("integrity"), latest.get("source", "")))
    decisions = list(records.get("closure_decisions") or ())
    accepted = bool(decisions) and decisions[-1].get("decision") == "ACCEPT"
    for row in closing:
        complete = row.get("status") == "CLOSURE_EVIDENCE_HELD" and row.get("integrity") == "VALID_OUTPUT_HELD"
        if accepted and not complete:
            continue   # an obligation (closure_after_decision_incomplete), not a condition: the ACCEPT stands
        literal = "CLOSED_AFTER_ACCEPT" if accepted else "CLOSE_BEFORE_ACCEPT"
        signals.append(_signal("closure_dialogue", literal, row.get("source", ""),
                               note=f"{row.get('operation')}: {literal.lower().replace('_', ' ')}"))
    answered = (bool(decisions) and bool(dialogue) and decisions[-1].get("decision") != "ACCEPT"
                and dialogue[-1].get("record_digest") and dialogue[-1]["record_digest"] not in (decisions[-1].get("binds") or ()))
    if not answered:
        signals += _history("closure_decisions", decisions, key="decision")
    # else: a closure response recorded AFTER a non-ACCEPT decision answers it (lane F, 2026-10-10: a
    # REQUEST_REVISION kept showing Refused after the corrected PRESENT and passing REVIEW). The newer
    # response surfaces as the "closure_dialogue_incomplete" obligation: Close / needs you to decide.
    signals += _amendment_signals(records, signals)
    for problem in records.get("problems") or ():
        signals.append(dict(family="problems", literal=None, condition=UNRECOGNIZED, stage=PREPARE,
                            source=problem.get("source", ""), note=problem.get("error", "unreadable record")))
    return signals


def _amendment_signals(records: dict, signals: List[dict]) -> List[dict]:
    """The Overseer conversation's latest completed Propose candidate and its fate (Praxis-Fable, 10-12). A
    Sprint-boundary amendment goes through the pure sprint_amendments.projection hint (never authority): proposed is
    Needs you (rule on it); approved stays Needs you until a standing admission binds it through
    exact rebind, full staffing, or initial effective-plan staffing; refused is Refused. A Plan-stage conversation proposal has its
    own literals. Nothing here advances a stage: the signal's stage never precedes the other signals' frontier, so the
    frontier's own signals do not supersede it; at Closed nothing is shown."""
    found = records.get("amendments") or {}
    candidates = [c for c in found.get("candidates") or () if c.get("candidate_sha256")]
    if not candidates or not signals:
        return []
    frontier = STAGES[max(_position(s) for s in signals)[0]]
    if frontier == CLOSED:
        return []
    latest = max(candidates, key=lambda c: c["proposal_version"] if type(c.get("proposal_version")) is int else 0)
    rulings = [r for r in found.get("rulings") or () if r.get("candidate_sha256") == latest["candidate_sha256"]]
    source = rulings[-1].get("source", "") if rulings else latest.get("source", "")
    if rulings and rulings[-1].get("decision") not in DISPOSITIONS:
        return [dict(family=AMENDMENT_FAMILY, literal=rulings[-1].get("decision"), condition=UNRECOGNIZED,
                     stage=frontier, source=source, note="plan-change decision outside the campaign vocabulary")]
    disposition = DISPOSITIONS[rulings[-1]["decision"]] if rulings else "PROPOSED"
    ready = disposition == "APPROVED" and bool(_bound_admissions(records, rulings[-1], bool(latest.get("amends"))))
    if ready:
        return []  # Staffing/admission transition recorded; execution authority is checked elsewhere.
    if latest.get("amends"):
        from .amendment import projection
        hint = projection(PLAN if frontier == PREPARE else frontier, disposition, successor_ready=ready)
        if hint is None:
            return []
        stage = STAGES[max(STAGES.index(frontier), STAGES.index(hint["stage"]))]
        return [dict(family=AMENDMENT_FAMILY, literal=hint["signal"], condition=hint["condition"], stage=stage,
                     source=source, note=f"{hint['action']} (conversation page); the amendment hint names {hint['stage']}")]
    literal = {"PROPOSED": "CONVERSATION_PROPOSED", "APPROVED": "CONVERSATION_APPROVED"}.get(disposition, "CONVERSATION_REFUSED")
    return [_signal(AMENDMENT_FAMILY, literal, source, stage=frontier)]


def _positive(value) -> bool:
    return type(value) is int and value >= 1     # Never a bool, a float or a numeric string.


def _sha256(value) -> bool:
    return type(value) is str and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def _ordered_history(records, store):
    rows = [r for r in records.get("host_histories") or () if r.get("store") == store]
    versions = [r.get("version") for r in rows]
    if any(not _positive(v) for v in versions) or len(set(versions)) != len(versions):
        return []  # Unestablished ordering cannot clear a pending transition or obligation.
    return sorted(rows, key=lambda r: r["version"])


def _standing_staff(records):
    standing = {}
    for row in _ordered_history(records, "native-staff-admission"):
        sprint = row.get("sprint_id")
        if not isinstance(sprint, str) or not sprint or row.get("status") not in {"ADMIT", "REVOKE"}:
            return {}
        if row["status"] == "ADMIT":
            standing[sprint] = row
        else:
            standing.pop(sprint, None)
    return standing


def _bound_admissions(records, ruling, amends):
    """Read-only record linkage, never native authority. Unknown/missing bindings remain pending."""
    approved, change = ruling.get("approval_sha256"), ruling.get("record_sha256")
    if not _sha256(approved) or not _sha256(change):
        return []
    decisions = {r.get("record_sha256"): r for r in records.get("host_histories") or ()
                 if r.get("store") == "native-staffing-decisions" and r.get("status") == "STAFFING_APPROVED"
                 and _sha256(r.get("record_sha256"))}
    result = []
    for row in _standing_staff(records).values():
        a = row.get("amendment") or {}
        staff = row.get("staffing_decision_sha256")
        if (not isinstance(a, dict) or a.get("approved_sha256") != approved or a.get("plan_change_sha256") != change
                or a.get("next_sprint_id") != row["sprint_id"] or not _sha256(row.get("record_sha256"))
                or staff not in decisions or (amends and row["sprint_id"] != ruling.get("next_sprint_id"))):
            continue
        kind = a.get("binding_kind")
        if kind == ("FULL_STAFFING" if amends else "EFFECTIVE_PLAN"):
            if a.get("binding_sha256") == staff and a.get("successor_binding_request_id") is None:
                result.append(row)
        elif amends and kind == "EXACT_REBIND" and _sha256(a.get("binding_sha256")):
            matches = [b for b in (records.get("amendments") or {}).get("bindings") or ()
                       if b.get("approved_sha256") == approved and b.get("record_sha256") == a["binding_sha256"]
                       and b.get("request_id") == a.get("successor_binding_request_id")
                       and b.get("next_sprint_id") == row["sprint_id"] and b.get("original_staffing_sha256") == staff]
            if len(matches) == 1:
                result.append(row)
    return result


def _revision_answered(decision, histories) -> bool:
    """True only when a LATER staffing history record pins this exact REQUEST_REVISION decision (Praxis F2):
    its revision_ref is exactly {version, sha256}, the int version and the 64-hex digest of this decision's
    record, and the referencing record's own version follows the history head the decision was taken on.
    Anything unestablished (missing, wrong, stale, boolean or untyped fields) leaves the decision standing."""
    version, record_sha256, head = (decision.get("version"), decision.get("record_sha256"),
                                    decision.get("history_head_version"))
    if (decision.get("status") != "REQUEST_REVISION" or not _positive(version) or not _sha256(record_sha256)
            or not _positive(head)):
        return False

    def answers(row) -> bool:
        ref = row.get("revision_ref")
        return (row.get("store") == "native-staffing-history" and type(ref) is dict
                and set(ref) == {"version", "sha256"} and _positive(ref["version"]) and _sha256(ref["sha256"])
                and ref["version"] == version and ref["sha256"] == record_sha256
                and _positive(row.get("version")) and row["version"] > head)
    return any(answers(r) for r in histories)


def _entry_answered(signal, signals) -> bool:
    """The framing's stage-entry decision (lead chosen: start planning) is answered once that stage's
    own work exists; finished work no longer stands in for a decision (lane F, 2026-10-10)."""
    return (signal["family"] == "framing" and signal["literal"] in STAGE_ENTRY
            and any(t["family"] != "framing" and t["stage"] == signal["stage"] for t in signals))


def _stage_done(signal) -> bool:
    """DONE that completes its stage: an authority record, never finished work (WORK_FAMILIES). A closure applied
    after the operator's ACCEPT (CLOSED_AFTER_ACCEPT) rests on that authority record and completes Closed."""
    return signal["condition"] == DONE and (signal["family"] not in WORK_FAMILIES or signal["literal"] == "CLOSED_AFTER_ACCEPT")


def _position(signal) -> tuple:
    return STAGES.index(signal["stage"]), 1 if _stage_done(signal) else 0


def _actions(stage: str, condition: str) -> List[dict]:
    if condition == DONE and stage != CLOSED:
        target = STAGES[STAGES.index(stage) + 1]
        return [dict(label=f"Continue to {target}: {label}", href=href, stage=target)
                for label, href in STAGE_PAGES[target]]
    return [dict(label=f"{VERB[condition]}: {label}", href=href, stage=stage) for label, href in STAGE_PAGES[stage]]


def _obligation(kind, condition, literal, source, note) -> dict:
    return dict(kind=kind, condition=condition, literal=literal, source=source, note=note)


def _signal_obligation(signal, packets_exist) -> Optional[dict]:
    """The obligation one signal leaves open, or None (rule 5; the first matching kind wins)."""
    family, literal, condition = signal["family"], signal["literal"], signal["condition"]
    args = (literal, signal["source"], signal["note"])
    if condition == UNCERTAIN:
        return _obligation("uncertain_effect", UNCERTAIN, *args)
    if condition == UNRECOGNIZED:
        return _obligation("unrecognized_record", UNRECOGNIZED, *args)
    if family in CLOSURE_FAMILIES and condition in OPEN_RUN:
        return _obligation("closure_dialogue_incomplete", condition, *args)
    if (family == "execution" and literal in OPEN_EXECUTION) or (family == "attempts" and literal in OPEN_GENERATION) \
            or (family in RUN_FAMILIES and condition in OPEN_RUN):
        return _obligation("run_not_terminal", condition, *args)
    if family == "execution" and (literal == "ARTIFACT_REVIEW_REQUIRED" or (literal == "REVIEW_READY" and not packets_exist)):
        note = signal["note"] if literal == "ARTIFACT_REVIEW_REQUIRED" else "run complete; no closure packet reviews its artifacts"
        return _obligation("artifact_not_reviewed", NEEDS_YOU, literal, signal["source"], note)
    return None


def _short(value) -> str:
    return f"{value[:12]}..." if isinstance(value, str) and len(value) > 12 else repr(value)


def _accepted_evidence_issues(decision: dict, dialogue: List[dict], packets: List[dict]) -> List[str]:
    """Why an ACCEPT's exact references (native_closure_review: packet_id, presentation_sha256,
    review_sha256) do not resolve to loaded records that bind to each other; [] when they do. Only
    what the ACCEPT names is checked; the stores' own readers validate the rest."""
    packet_id, presented, reviewed = (decision.get(k) for k in ("packet_id", "presentation_sha256", "review_sha256"))
    by_digest = {row.get("record_digest"): row for row in dialogue if row.get("record_digest")}
    issues = []
    if not isinstance(packet_id, str) or not any(p.get("packet_id") == packet_id for p in packets):
        issues.append(f"closure packet {_short(packet_id)} is missing or does not match its content")
    presentation = by_digest.get(presented) if isinstance(presented, str) else None
    if presentation is None:
        issues.append(f"presentation {_short(presented)} is not a loaded closure dialogue record")
    elif presentation.get("operation") != "PRESENT" or presentation.get("packet_id") != packet_id:
        issues.append(f"presentation {_short(presented)} is not a presentation of packet {_short(packet_id)}")
    review = by_digest.get(reviewed) if isinstance(reviewed, str) else None
    if review is None:
        issues.append(f"review {_short(reviewed)} is not a loaded closure dialogue record")
    elif (review.get("operation") != "REVIEW" or review.get("packet_id") != packet_id
          or review.get("presentation_sha256") != presented):
        issues.append(f"review {_short(reviewed)} is not a review of that presentation and packet")
    return issues


def pending_supersession(treatment, next_sprint_id, source, *, producing=False) -> dict:
    """One visible obligation, also used when retaining an amendment's new work."""
    note = (f"approved supersession of {treatment.get('path')} by {treatment['new_path']}: "
            + ("its producing task has no standing ACCEPT yet" if producing else
               "no bound approved staffing task of Sprint %s produces it" % next_sprint_id))
    return _obligation("supersession_unproduced", NEEDS_YOU, "SUPERSEDE", source, note)


def _supersession_obligations(records: dict) -> List[dict]:
    """Every approved supersession remains open until its own admitted producing task has a standing ACCEPT.

    A later amendment, unrelated same-name artifact, or revoked acceptance cannot erase it.
    These are projected links only; the native stores still verify publication and execution authority.
    """
    rulings = [r for r in (records.get("amendments") or {}).get("rulings") or ()
               if r.get("decision") == "APPROVE" and r.get("artifact_treatment")]
    decisions = {r.get("record_sha256"): r for r in records.get("host_histories") or ()
                 if r.get("store") == "native-staffing-decisions" and r.get("status") == "STAFFING_APPROVED"
                 and _sha256(r.get("record_sha256"))}
    tasks = {}
    for row in _ordered_history(records, "native-task-decisions"):
        admission, task = row.get("admission_sha256"), row.get("task_id")
        if _sha256(admission) and isinstance(task, str) and task:
            tasks[(admission, task)] = row
    found = []
    for ruling in rulings:
        admitted = _bound_admissions(records, ruling, True)
        for treatment in ruling["artifact_treatment"]:
            if (not isinstance(treatment, dict) or treatment.get("action") != "supersede"
                    or not isinstance(treatment.get("new_path"), str)):
                continue
            path = treatment["new_path"].casefold()
            producing = accepted = False
            for admission in admitted:
                staff = admission["staffing_decision_sha256"]
                for task in decisions[staff].get("tasks") or ():
                    outputs = task.get("deliverables")
                    if (task.get("sprint_id") != ruling.get("next_sprint_id") or not isinstance(outputs, list)
                            or path not in [p.casefold() for p in outputs if isinstance(p, str)]):
                        continue
                    producing = True
                    row = tasks.get((admission["record_sha256"], task.get("task_id")), {})
                    if (row.get("status") == "ACCEPT" and row.get("staffing_decision_sha256") == staff
                            and path in [p.casefold() for p in row.get("artifacts") or () if isinstance(p, str)]):
                        accepted = True
            if not accepted:
                found.append(pending_supersession(treatment, ruling.get("next_sprint_id"),
                                                 ruling.get("source", ""), producing=producing))
    return found


def _obligations(records: dict, signals: List[dict]) -> List[dict]:
    """Unfinished work the operator must see before or at Close (G7)."""
    found = [o for o in (_signal_obligation(s, bool(records.get("closure_packets"))) for s in signals) if o]
    # Held native contributions and publications need a task decision (ACCEPT or REVOKE).
    decided = {r.get("request_id") for r in records.get("host_histories") or ()
               if r.get("store") == "native-task-decisions" and r.get("request_id")}
    held = {}
    for row in records.get("host_runs") or ():
        if row.get("status") not in HELD_FOR_TASK_DECISION:
            continue
        publication = row.get("store") == "native-artifact-effects"
        key = (row.get("request_id") or row.get("run_id")) if publication else row.get("run_id")
        if key and (key not in held or publication):      # a publication outranks its contribution
            held[key] = row
    for key, row in sorted(held.items()):
        if row.get('store') == 'native-staff-contributions' and row.get('staff_memory'):
            if row['staff_memory'] != 'FILED':
                found.append(_obligation('staff_memory_not_filed', NEEDS_YOU, row.get('status'), row.get('source', ''),
                                         f'Staff memory {key}: exact published closure filing is missing'))
            continue
        if key not in decided:
            found.append(_obligation("artifact_not_reviewed", NEEDS_YOU, row.get("status"), row.get("source", ""),
                                     f"{row.get('store')} {key}: held artifact with no task decision"))
    # A closure dialogue response recorded after the last operator decision.
    dialogue, decisions = list(records.get("closure_dialogue") or ()), list(records.get("closure_decisions") or ())
    closing = [r for r in dialogue if r.get("operation") in CLOSING_OPERATIONS]
    dialogue = [r for r in dialogue if r.get("operation") not in CLOSING_OPERATIONS]   # closures after ACCEPT are expected
    if decisions and decisions[-1].get("decision") == "ACCEPT":
        for row in closing:   # a held or invalid closure turn after ACCEPT: Closed, with this unfinished (Fable, 10-12)
            if not (row.get("status") == "CLOSURE_EVIDENCE_HELD" and row.get("integrity") == "VALID_OUTPUT_HELD"):
                found.append(_obligation("closure_after_decision_incomplete", NEEDS_YOU, row.get("status"),
                                         row.get("source", ""), f"{row.get('operation')} after ACCEPT is held or invalid; inspect it"))
    if dialogue and decisions and dialogue[-1].get("record_digest") \
            and dialogue[-1]["record_digest"] not in (decisions[-1].get("binds") or ()):
        found.append(_obligation("closure_dialogue_incomplete", NEEDS_YOU, dialogue[-1].get("status"),
                                 dialogue[-1].get("source", ""),
                                 "closure dialogue response recorded after the last operator decision"))
    # An ACCEPT counts only while the exact evidence it names is loaded and binds (rule 6).
    if decisions and decisions[-1].get("decision") == "ACCEPT":
        missing = _accepted_evidence_issues(decisions[-1], dialogue, list(records.get("closure_packets") or ()))
        if missing:
            found.append(_obligation("accepted_evidence_missing", NEEDS_YOU, "ACCEPT", decisions[-1].get("source", ""),
                                     "the accepted closure evidence is not inspectable: " + "; ".join(missing)))
    found += _supersession_obligations(records)
    # Open operator questions (lane F, 10-12): the approved plan names questions only the operator can answer.
    for row in records.get("operator_questions") or ():
        found.append(_obligation("operator_question_open", NEEDS_YOU, row.get("question"), row.get("source", ""),
                                 "an open operator question: record the decision on /projects/operator-decisions"))
    # Interruption obligations (closure_obligation.py): pending means execution and closure are unresolved.
    for row in records.get("closure_obligations") or ():
        literal = row.get("status")
        if literal == "pending":
            condition, note = UNCERTAIN, "closure obligation without a terminal outcome: reconcile before a new run"
        else:
            mapped = classify(literal)
            condition = UNRECOGNIZED if mapped is None else mapped[0]
            note = f"literal {literal!r} is not in the campaign vocabulary" if mapped is None else VOCABULARY[literal][2]
        if condition != DONE:
            found.append(_obligation("interruption_obligation", condition, literal, row.get("source", ""), note))
    unique = {}
    for o in found:
        key = (o["kind"], o["source"], str(o["literal"]))
        if o["kind"] == "supersession_unproduced":
            key += (o["note"],)  # One ruling can supersede more than one artifact.
        unique.setdefault(key, o)
    return list(unique.values())


# The hosts whose pool projection the campaign page may ask for (pooled_recovery.handle_pool_routes, Praxis 10-12):
# store -> the host prefix; the query is the host's own inspect query. Only open runs are listed.
POOL_HOSTS = {"native-staff-contributions": "/api/native-contribution-host/", "native-reviews": "/api/native-review-host/",
              "native-closure-runs": "/api/closure-dialogue/"}
POOL_OPEN = OPEN_RUN | {UNCERTAIN}


def _pool_queries(records: dict) -> List[dict]:
    """The pool-inspect queries of the campaign's open runs (planning by version, host runs by request id), for the
    page's one-line runtime pool projection; read-only hints, never authority or campaign advancement."""
    pid = records.get("campaign_id")
    found = []
    planning = list(records.get("planning") or ())
    if planning:
        latest = planning[-1]
        mapped = classify(latest.get("status"))
        if mapped is not None and mapped[0] in POOL_OPEN and type(latest.get("version")) is int:
            found.append(dict(store="planning", prefix="/api/planning-host/",
                              query=dict(project_id=pid, version=latest["version"]), source=latest.get("source", "")))
    for row in records.get("host_runs") or ():
        prefix = POOL_HOSTS.get(row.get("store"))
        mapped = classify(row.get("status"))
        if prefix and row.get("run_id") and mapped is not None and mapped[0] in POOL_OPEN:
            found.append(dict(store=row["store"], prefix=prefix, query=dict(project_id=pid, request_id=row["run_id"]),
                              source=row.get("source", "")))
    return found


def campaign_state(records: dict) -> Dict[str, object]:
    """The operator-visible state of one campaign, from its durable records alone."""
    records = records or {}
    signals = _signals(records)
    frontier = max(_position(s) for s in signals)
    # An open Overseer plan change (proposed, approved but unbound, refused) is the operator's decision across
    # stages: a stage-done record at the frontier never supersedes it (Fable, 10-12).
    current = [s for s in signals if s["condition"] not in SUPERSEDABLE or s["family"] == AMENDMENT_FAMILY
               or not (any(t["family"] != s["family"] and _position(t) > _position(s) for t in signals)
                       or _entry_answered(s, signals))]
    winner = min(current, key=lambda s: (ORDER.index(s["condition"]), -STAGES.index(s["stage"])))
    condition = winner["condition"]
    stage = STAGES[frontier[0]]
    obligations = _obligations(records, signals)
    literal = "no framing record" if winner["literal"] is None and winner["family"] == "framing" else winner["literal"]
    where = f" in {winner['source']}" if winner["source"] else ""
    reason = f"{MEANING[condition]}. {winner['family']}: {literal}{where} ({winner['note']})."
    focus = winner["stage"]
    decisions = list(records.get("closure_decisions") or ())
    accepted = bool(decisions) and decisions[-1].get("decision") == "ACCEPT"
    if condition == DONE and stage != CLOSE and not _stage_done(winner):
        # Finished work (a generation's or host run's completion) is not the stage's completion: the
        # stage awaits the operator's authority record on that work (lane F, 2026-10-10).
        condition = NEEDS_YOU
        reason = (f"{MEANING[NEEDS_YOU]}. {winner['family']}: {literal}{where} finished; {STAGE_AWAITS.get(stage, 'the stage awaits your decision')}.")
    elif (condition == DONE and stage == STAFF and winner["family"] == "native-staffing-decisions"
          and winner["literal"] in STAFF_APPROVAL
          and not any(t["family"] == "native-staff-admission" and t["literal"] == "ADMIT" for t in signals)):
        # Approved staffing awaits the operator's staff admission before Staff is complete.
        condition = NEEDS_YOU
        reason = f"{MEANING[NEEDS_YOU]}. Staffing is approved{where}; admit the staff to complete Staff."
    elif condition == DONE and stage == CLOSE and not accepted:
        # A finished closure run (the lead's PRESENT, a review) is not a closed campaign: only the
        # operator's ACCEPT closes it (lane F, 2026-10-10: Closed showed right after PRESENT).
        focus, condition = CLOSE, NEEDS_YOU
        reason = (f"{MEANING[NEEDS_YOU]}. Closure has been presented but not accepted: review it and "
                  f"record your decision{' (latest: ' + str(decisions[-1].get('decision')) + ')' if decisions else ''}.")
    elif condition == DONE and stage == CLOSE:        # closure is recorded and accepted (rule 6)
        focus = CLOSE
        if obligations:
            condition = min([NEEDS_YOU] + [o["condition"] for o in obligations], key=ORDER.index)
            listed = "; ".join(f"{o['kind']}: {o['literal']} in {o['source']} ({o['note']})" for o in obligations[:3])
            more = f"; and {len(obligations) - 3} more" if len(obligations) > 3 else ""
            reason = (f"{MEANING[condition]}. Closure is recorded, but {len(obligations)} unfinished "
                      f"obligation{'s' if len(obligations) > 1 else ''} must be accounted for before the campaign "
                      f"is Closed: {listed}{more}.")
        else:
            stage = focus = CLOSED
    sources = sorted({(s["path"], s["sha256"]) for s in records.get("sources") or ()})
    return dict(campaign_id=records.get("campaign_id"), stage=stage, condition=condition, reason=reason,
                actions=_actions(focus, condition),
                sources=[dict(path=p, sha256=h) for p, h in sources],
                signals=[{k: s[k] for k in ("family", "literal", "condition", "stage", "source")}
                         for s in current],
                obligations=obligations, pool_queries=_pool_queries(records))
