"""Campaign vocabulary box (MVP-01 contract, section 4): the ONE declared mapping from every
internal status literal to an operator-visible condition, and where meaningful a stage.

Inputs:  a status literal exactly as the code persists or derives it (case matters:
         staff execution's READY and an autonomous job's ready are different literals).
Outputs: CONDITIONS, PRECEDENCE, STAGES, VOCABULARY {literal: (condition, stage or None, origin)},
         classify(literal) -> (condition, stage or None), or None when the literal is unmapped.
Never:   guesses. An unmapped literal is None here; the projection shows it as UNRECOGNIZED,
         naming the literal and its source, and tests/test_campaign_state.py fails on a new one.

Stage None means "the stage of the store the record came from" (state.FAMILY_STAGES, or the stage
the loader names for the record).
The origin column says which code uses the literal; it is documentation, not logic.
"""
from __future__ import annotations

from typing import Dict, Optional, Tuple

NEEDS_YOU = "NEEDS_YOU"
RUNNING = "RUNNING"
WAITING = "WAITING"
PAUSED = "PAUSED"
INTERRUPTED = "INTERRUPTED"
UNCERTAIN = "UNCERTAIN"
REFUSED = "REFUSED"
DONE = "DONE"
UNRECOGNIZED = "UNRECOGNIZED"

CONDITIONS = (NEEDS_YOU, RUNNING, WAITING, PAUSED, INTERRUPTED, UNCERTAIN, REFUSED, DONE)

# Most demanding first (contract section 4). UNRECOGNIZED is not a condition of the contract
# table; the projection puts it above all of them, because an unknown literal can hide anything.
PRECEDENCE = (UNCERTAIN, INTERRUPTED, REFUSED, NEEDS_YOU, PAUSED, RUNNING, WAITING, DONE)

STAGES = ("Prepare", "Plan", "Staff", "Authorize", "Run", "Review", "Close", "Closed")
PREPARE, PLAN, STAFF, AUTHORIZE, RUN, REVIEW, CLOSE, CLOSED = STAGES

_N, _R, _W, _P, _I, _U, _X, _D = NEEDS_YOU, RUNNING, WAITING, PAUSED, INTERRUPTED, UNCERTAIN, REFUSED, DONE

VOCABULARY: Dict[str, Tuple[str, Optional[str], str]] = {
    # --- Framing record (roadmaps/projects.py, plan_ruling_contract.py) ---
    "WMC_SELECTED": (_N, PREPARE, "framing: set up, no lead chosen yet"),
    "HEAVYWEIGHT_SELECTED": (_N, PLAN, "framing: lead chosen, plan not approved"),
    "PLAN_APPROVED": (_D, PLAN, "framing: plan ruling approved"),
    # --- Plan rulings and decisions (plan_ruling_contract.py, staffing_workflow.py, native_closure_review.py) ---
    "APPROVE": (_D, None, "ruling/decision"),
    "REJECT": (_X, None, "ruling/decision"),
    "REQUEST_REVISION": (_X, None, "ruling/decision; closure decision"),
    "ACCEPT": (_D, None, "closure operator decision (DONE at Close is Closed)"),
    # --- Overseer conversation plan changes (overseer_plan_changes.py, sprint_amendments.projection; Fable, 10-12).
    # state._amendment_signals derives these from the latest completed Propose candidate and its ruling/rebind records.
    "AMENDMENT_PROPOSED": (_N, None, "a Sprint-boundary amendment awaits your ruling on the conversation page"),
    "AMENDMENT_APPROVED_UNBOUND": (_N, AUTHORIZE, "an approved amendment awaits exact rebind or full staffing, then successor admission"),
    "AMENDMENT_REFUSED": (_X, None, "the amendment was rejected or sent back; the previous approval stands"),
    "CONVERSATION_PROPOSED": (_N, None, "a conversation plan proposal awaits native review and your ruling"),
    "CONVERSATION_APPROVED": (_N, None, "a conversation plan is approved; its staffing decision and admission are still required"),
    "CONVERSATION_REFUSED": (_X, None, "the conversation proposal was rejected or sent back"),
    "OPERATOR_ACCEPTED": (_D, CLOSE, "native closure review"),
    "RECORDED": (_D, None, "plan_rulings / staffing confirmation recorded"),
    # --- Review findings and integrity (staffing_workflow.py, plan_ruling_contract.py, closure dialogue) ---
    "PASS": (_D, None, "finding status"),
    "WARN": (_N, None, "finding status: the operator weighs it"),
    "FAIL": (_X, None, "finding status"),
    "NOT_APPLICABLE": (_D, None, "finding status"),
    "NOT_REVIEWED": (_N, None, "staffing decision without a review"),
    "VALID_BOUND_REPORT": (_N, None, "review report bound; decision is the operator's"),
    "VALID_OUTPUT_HELD": (_N, None, "valid output held for a decision"),
    "INVALID_OUTPUT_HELD": (_X, None, "output refused as invalid"),
    # --- Planning (planning_generation.py, planning_host.py) ---
    "PROPOSED": (_N, None, "planning candidate / staffing proposal awaiting a ruling"),
    "PROPOSED_HELD": (_N, PLAN, "planning result held for a ruling"),
    "AUTHORIZED": (_N, PLAN, "planning launch authorized, not started"),
    "NOT_RECORDED": (_N, None, "nothing launched yet"),
    "UNRESOLVED": (_U, None, "started, outcome not recorded"),
    "INTERRUPTED_HELD": (_I, None, "generation interrupted, held"),
    "completed_and_held": (_N, PLAN, "planning retirement held"),
    "native_persistence_verified": (_D, PLAN, "planning native closure verified"),
    # --- Staffing (staffing_workflow.py) ---
    "NEEDS_REVISION": (_X, STAFF, "staffing proposal has structural gaps"),
    "STAFFING_APPROVED": (_D, STAFF, "staffing decision approved"),
    # --- Staff execution, derived by replay (staff_execution.py inspect) ---
    "READY": (_N, RUN, "execution: activated or resumed; advance it"),
    "RUNNING": (_R, RUN, "execution: a task is in flight in this process"),
    "PAUSED": (_P, RUN, "execution: acknowledged pause"),
    "STOPPED": (_N, CLOSE, "execution: controlled stop; close it as stopped"),
    "BLOCKED": (_X, RUN, "execution: blocked before an intent; repair named"),
    "RECONCILIATION_REQUIRED": (_U, RUN, "execution: intent without result; never replayed"),
    "REVIEW_READY": (_N, REVIEW, "execution complete / native closure packet ready"),
    "ARTIFACT_REVIEW_REQUIRED": (_N, REVIEW, "execution complete but artifacts differ from receipts"),
    "COMPLETE": (_D, None, "execution task result"),
    "UNCERTAIN": (_U, None, "execution task result"),
    # --- Closure (closure_packets.py, native_closure_packets.py, native_closure_dialogue.py, native_closure_host.py) ---
    "PREPARED_PENDING_INDEPENDENT_REVIEW": (_N, REVIEW, "closure packet prepared; launch the independent review"),
    "CLOSURE_EVIDENCE_HELD": (_N, CLOSE, "closure dialogue evidence held for the operator decision"),
    # Closures after the decision (Fable, 10-12; closure-line-range-8): the lead's CLOSE and the reviewer's CLOSE_REVIEW
    # follow the operator's ACCEPT. state._signals derives these from a closure-dialogue row's operation and outcome.
    "CLOSED_AFTER_ACCEPT": (_D, CLOSED, "a lead or reviewer closure applied after the operator's ACCEPT"),
    "CLOSE_INCOMPLETE": (_N, CLOSE, "a closure turn after ACCEPT is held or invalid (reported as the closure_after_decision_incomplete obligation)"),
    "CLOSE_BEFORE_ACCEPT": (_N, CLOSE, "a closure turn recorded without a final ACCEPT; inspect"),
    "EVIDENCE_HELD": (_N, REVIEW, "native closure packet evidence held"),
    "CAPTURED_HELD": (_N, CLOSE, "native closure host captured response"),
    # --- Held evidence across runtime/review_closure and runtime/staffing (*_HELD, *UNRESOLVED*) ---
    "ASSESSMENT_PASS_HELD": (_N, None, "minder assessment held"),
    "CONTRIBUTION_EVIDENCE_HELD": (_N, None, "native contribution evidence held"),
    "FINDINGS_CAPTURED_HELD": (_N, None, "findings captured, held"),
    "NATIVE_CUSTODY_COMPLETED_HELD": (_N, None, "native custody completed, held"),
    "PROPOSED_CONTENTS_HELD": (_N, None, "proposed contents held"),
    "QUALIFICATION_EVIDENCE_HELD": (_N, None, "minder qualification evidence held"),
    "RESULT_PUBLISHED_HELD": (_N, None, "custodied result published, held"),
    "REVIEW_EVIDENCE_HELD": (_N, None, "reviewer evidence held"),
    "SCRIPTED_FINDINGS_HELD": (_N, None, "scripted findings held"),
    "STAFFING_CAPTURED_HELD": (_N, None, "native staffing captured, held"),
    "STAFFING_EVIDENCE_HELD": (_N, None, "native staffing evidence held"),
    "PARENT_RUNNING_HELD": (_R, None, "custodied contribution: parent still running"),
    "PARENT_EXITED_HELD": (_I, None, "custodied contribution: parent exited, nothing in flight"),
    "HISTORICAL_INCOMPLETE_HELD": (_I, None, "historical parent custody incomplete"),
    "HISTORICAL_NON_SUCCESS_HELD": (_X, None, "historical parent custody did not succeed"),
    "INTERRUPTED_OR_UNVERIFIED_HELD": (_U, None, "interrupted or unverified: outcome unknown"),
    "PUBLICATION_PENDING_HELD": (_U, None, "custodied publication not confirmed"),
    "SPAWN_UNCONFIRMED_HELD": (_U, None, "spawn not confirmed"),
    "NATIVE_DISPOSITION_UNRESOLVED_HELD": (_U, None, "native disposition unresolved"),
    "HELD_OR_UNRESOLVED": (_U, None, "desktop staffing host intent without outcome"),
    "UNVERIFIED_OR_UNRESOLVED": (_U, None, "outcome unverified"),
    "ACQUIRED_UNRESOLVED_NO_REPLAY": (_U, None, "acquired, outcome unknown, never replayed"),
    "UNVERIFIED_COMMIT_STATE": (_U, None, "commit state unverified"),
    # --- Completions named by the contract ---
    "AETHER_NORMAL_COMPLETED": (_D, None, "normal completion recorded"),
    "COMMITTED": (_D, None, "commit recorded"),
    # --- Generation attempts (generation_attempts.py; lower case) ---
    "generation_owned": (_R, None, "generation owned by a live worker"),
    "generation_frozen": (_N, None, "generation frozen, awaiting closure release"),
    "reconciled": (_D, None, "generation reconciled"),
    "normal_completed": (_D, None, "generation completed normally"),
    "preparing": (_R, None, "request preparing"),
    "dispatch_possible": (_U, None, "request may have been dispatched"),
    "response_recorded": (_R, None, "response recorded, outcome pending"),
    "returned": (_D, None, "request returned"),
    "unknown": (_U, None, "request outcome unknown"),
    # --- Autonomous jobs (autonomous_jobs.py JOB_STATES and STEP_STATES) ---
    "ready": (_W, None, "job ready for its controller's next step"),
    "active": (_R, None, "job active"),
    "paused": (_P, None, "job paused"),
    "reconciliation_required": (_U, None, "job step outcome unknown"),
    "complete": (_D, None, "job/step/packet complete"),
    "failed": (_X, None, "job/step failed"),
    "cancelled": (_N, CLOSE, "job cancelled; close it as cancelled"),
    "pending": (_W, None, "step waiting on earlier steps"),
    "running": (_R, None, "job step / mission packet running"),
    # --- Legacy missions and packets (mission.py), off the supported path but mapped ---
    "queued": (_W, None, "packet queued"),
    "launched": (_R, None, "packet launched"),
    "blocked": (_X, None, "packet blocked (recoverable; charter: terminal)"),
    "refused": (_X, None, "packet refused"),
    "invalid_packet": (_X, None, "packet invalid"),
    "error": (_X, None, "packet error"),
    "gate_failed": (_X, None, "packet gate failed"),
    "policy_changed": (_N, None, "execution policy changed; re-approve"),
    "superseded": (_D, None, "packet superseded by a revision"),
    "done": (_D, None, "mission done"),
    # --- Task board (roadmaps/board.py STATES) ---
    "open": (_W, None, "board task open"),
    "assigned": (_W, None, "board task assigned, not started"),
    "in_progress": (_R, None, "board task in progress"),
    "waiting_on": (_W, None, "board task waiting on a named party"),
    "closed": (_D, None, "board task closed"),
    "rejected": (_X, None, "board task rejected"),
    "reopened": (_W, None, "board task reopened for revision"),
    # --- Native host stores the loader reads (pass 2: *_supervision.py, *_host.py, native_*.py) ---
    "ADMIT": (_D, None, "reviewer / staff admission decision: admitted"),
    "REVOKE": (_X, None, "task acceptance or admission revoked"),
    "REVISION_REQUESTED": (_X, None, "native closure operator decision row: revision requested"),
    "HELD": (_I, None, "native closure host: launched, nothing captured, not running in this process"),
    "UNRESOLVED_NO_REPLAY": (_U, None, "contribution host attempt without a recorded outcome; never replayed"),
    "PUBLICATION_PENDING_ORIGINAL_HANDLE": (_U, None, "contribution result not published; only the original handle can finish it"),
    "PUBLICATION_COMPLETE": (_D, None, "native artifact effects: every file published with its receipt"),
    "CANCELLATION_REQUESTED": (_R, None, "contribution host: cancellation asked, worker still live"),
    "PREPARING_CUSTODY": (_R, None, "contribution host: preparing a custodied launch (live)"),
    "NOT_STARTED": (_N, None, "desktop staffing host / qualification verification not started"),
    "AETHER_DESKTOP_NORMAL_COMPLETED": (_D, None, "desktop host normal completion recorded"),
    # --- Task budgets (runtime/staffing/native_task_budget.py ledger; derived by campaign/records.py) ---
    "TASK_BUDGET_ACTIVATED": (_N, AUTHORIZE, "task budget activated by the operator; launch the run"),
    "TASK_BUDGET_CONSUMED": (_D, AUTHORIZE, "task budget authority in use by its run"),
    # --- Minder hosts (runtime/review_closure/minder_*.py): the plan reviewer's qualification ---
    "PREPARED_ONLY": (_N, PLAN, "minder preparation recorded; nothing dispatched"),
    "HYBRID_REFERENCES_SNAPSHOTTED": (_N, PLAN, "minder hybrid qualification sources snapshotted; nothing dispatched"),
    "AUTHORIZED_ONLY": (_N, PLAN, "minder qualification authorized, not run"),
    "RETAINED_RUN_EVIDENCE": (_N, PLAN, "minder qualification run evidence retained for inspection"),
    "EVIDENCE_HELD_REVIEW_REQUIRED": (_N, PLAN, "minder qualification evidence held for review"),
    "VERIFIED_BOUND_RESPONSES": (_D, None, "minder qualification responses verified (integrity)"),
    "REMEDIATION_REQUIRED": (_X, PLAN, "minder assessment below threshold; remediation required"),
    "INSPECTED_HELD_NO_RELEASE_ADAPTER": (_N, None, "minder reconciliation inspected; held, no release adapter"),
    "HISTORICAL_POINTER_RETIRED_FOR_THIS_DRAFT": (_D, None, "minder source migration decision"),
    "TERMINAL_FAILURE_RECORDED": (_X, None, "minder terminal failure recorded"),
    "TERMINAL_FAILURE_PENDING_CONFIRMATION": (_U, None, "minder terminal failure intent not yet confirmed"),
    "UNRESOLVED_OR_INCOMPLETE": (_U, None, "minder qualification failed mid-run; outcome incomplete"),
    "UNVERIFIED_OR_CORRUPT": (_U, None, "minder qualification evidence cannot be verified"),
    "INVALID_HISTORY_ENTRY": (_U, None, "minder host history entry failed inspection"),
    # --- Interruption obligations (runtime/review_closure/closure_obligation.py; "pending" is mapped above) ---
    "interrupted": (_I, None, "closure obligation outcome: closure interrupted"),
    "outputs_verified": (_D, None, "closure obligation outcome: agent closure outputs verified"),
    # --- Heavyweight sessions and retirement (runtime/sessions/*.py) ---
    "ACCEPTED_HELD": (_D, None, "consent birth accepted and held"),
    "CONSENT_RETIRED_HELD": (_D, None, "consent retired and held"),
    "UNRESOLVED_ACTION": (_U, None, "continuation action unresolved"),
    "ACQUISITION_HELD": (_N, None, "heavyweight acquisition held"),
    "HYDRATION_HELD": (_N, None, "heavyweight hydration held"),
    "AMEND_RETIRED_HELD": (_D, None, "heavyweight amendment retired and held"),
    "WELCOME_RETIRED_HELD": (_D, None, "heavyweight welcome retired and held"),
    # --- Configured missions (runtime/orchestration/configured_*.py), off the supported path but mapped ---
    "CLOSED": (_D, None, "configured closure plan state closed"),
    "configuration_valid": (_D, None, "configured launcher: configuration check passed"),
    "started": (_R, None, "configured launcher: run started"),
    "held_unreconciled": (_U, None, "configured retirement held unreconciled"),
    "not_dispatched": (_I, None, "configured packet cancelled before dispatch; nothing in flight"),
    "transport_returned": (_D, None, "configured request: the transport returned a response"),
    "returned_malformed_tool_arguments": (_X, None, "configured request returned with invalid tool-call arguments; no tool ran (one repair turn)"),
    # --- Charter (runtime/orchestration/charter_*.py phases, verdicts, retro), off the supported path but mapped ---
    "prepared": (_N, None, "charter phase: prepared, awaiting the task"),
    "portability_review": (_N, None, "charter phase: portability review"),
    "roster_needs_adaptation": (_X, None, "charter phase: portability review failed; adapt the roster"),
    "intake_ready": (_N, None, "charter phase: intake ready for the operator"),
    "awaiting_confirmation": (_N, None, "charter phase: staffing awaits operator confirmation"),
    "accepted": (_D, None, "charter phase: deliverable accepted"),
    "return": (_X, None, "charter chunk check verdict: returned for correction"),
    "stopped": (_N, CLOSE, "charter segment stopped; close it as stopped"),
    "unavailable": (_I, None, "charter retro unavailable after a host restart"),
    "protected_escalation": (_N, None, "legacy mission outcome: escalated to the operator"),
}

assert all(v[0] in CONDITIONS and (v[1] is None or v[1] in STAGES) for v in VOCABULARY.values())


def classify(literal) -> Optional[Tuple[str, Optional[str]]]:
    """(condition, stage or None) for a mapped literal; None when it is unmapped (never a guess)."""
    entry = VOCABULARY.get(literal) if isinstance(literal, str) else None
    return None if entry is None else (entry[0], entry[1])
