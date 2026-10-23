"""MICROCASE-1 — the Ultralight tier's identity format.

S00073.7 C-5 / Roadmap_Ultralight.md U-01. Lives BESIDE the parity-locked modules
(canon/briefcase/ack/budget) per `Ultralight_Charter.md` R7 -- never inside them. Everything
here composes from their existing parse output (`canon.canon_sha`, `ack.parse_ack`,
`budget.est_tokens`, `assembler.section_body`) rather than reimplementing canonicalization,
seal-gate diffing, or token estimation.

Format (Charter §3.1), single file, four fixed sections, ONE seal (not per-section like
BRIEFCASE-1):

    === AETHER MICROCASE ===
    FORMAT: MICROCASE-1
    AGENT: <name> * ROLE: <one phrase> * EST_TOKENS: <n>
    SEAL: <6-hex nonce> * SHA256: <canon-sha of body>
    --- WHO ---
    <...>
    --- LAW ---
    <...>
    --- TAGS ---
    <...>
    --- TASK ---
    <...>
    === END MICROCASE ===

The hashed body is the four section bodies (WHO+LAW+TAGS+TASK), each LF-normalized and
newline-terminated via `assembler.section_body`, concatenated in fixed order. SEAL is the same
"first 6 hex of the sha256" convention BRIEFCASE-1 sections use (`assembler.section_from_spec`).
Parsing is fail-closed: any structural irregularity is an error, never a silent partial parse --
same discipline as `briefcase.parse`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from aether.integrity import canon
from aether.integrity.ack import parse_ack
from aether.agent.assembler import section_body
from aether.models.budget import est_tokens as _est_tokens

SECTION_ORDER = ("WHO", "LAW", "TAGS", "TASK")

# R2 (Charter): total MICROCASE <= 1,500 tokens target, 2,000 hard ceiling.
MICROCASE_TOKEN_TARGET = 1500
MICROCASE_TOKEN_CEILING = 2000

# R3.3 (Charter/Roadmap): tuned tier defaults -- distinct from the lightweight tier's
# DYNAMIC_FLOOR=8192 / OUTPUT_RESERVE=2048 / MAX_TOOL_CALLS=30 in toolloop.py. Not yet wired into
# the live toolloop (no live model runs until the lightweight-tier prerequisite gate, per the
# Roadmap's sequencing rule) -- these are the agreed constants for that future wiring.
DYNAMIC_FLOOR_MICRO = 3072
MAX_RESULT_CHARS_MICRO = 8000
TOOL_CALL_CAP_MICRO = 6

# AETHER-µ v1.0 card, VERBATIM from AETHER_MICRO_Lexicon.md ("this exact text rides in every
# MICROCASE `--- TAGS ---` section"). Sealed inside every microcase; a changed card is a reseal
# wave + changelog line in the lexicon doc, never a silent edit here.
AETHER_MICRO_TAGS_CARD = (
    "AETHER-u TAGS — optional labels that can make a reply easier to inspect:\n"
    "[RESULT]  something you produced or concluded (plain prose is also valid)\n"
    "[SOURCE]  where a fact came from: file + line(s). Every factual claim needs one.\n"
    "[UNSURE]  you are not certain — say so here, never inside a [RESULT]\n"
    "[ASK]     you need something you don't have — name it exactly; this logs a selector miss\n"
    "[NOTE]    optional lesson learned; use as much space as the lesson genuinely needs\n"
    "\n"
    "A final STATUS line is helpful but not required. When used:\n"
    "STATUS: COMPLETE | PARTIAL — <what is missing> | BLOCKED — <what stops you>\n"
    "\n"
    "RULES: Never invent a [SOURCE]. Tool output outranks your memory. Plainly stating a gap,\n"
    "ask, partial result, or blocker is correct even when it does not use the suggested labels.\n"
)

# R3 (Charter, verbatim across all microcases -- doctrine, not personality): the integrity
# floor + one-line route discipline + one-line packet scope.
# Drafted by Sagan (Roadmap U-01: "Sagan researches + summarizes the S00073.x lessons into the
# draft LAW section text"), genuine LW mission `025c937107e7` P1, dispatched through Mission
# Control with real tool calls against Fable_Ruling_S00073.6_Integrity_and_Ceiling.md and
# Ultralight_Charter.md. Reviewed by Linus (same mission, P3): READY, ~95 tokens (well under the
# ~140 target), all five R3 sub-requirements confirmed present verbatim. A prior LW dispatch
# attempt for this same draft (mission `bacf1038eb87`) failed honestly -- BLOCKED, wrong root
# granted -- and is preserved as a record, not deleted; this is the corrected, genuine rerun.
STANDARD_LAW_TEXT = (
    "LAW -- Integrity Floor, Route Discipline, Packet Scope\n"
    "\n"
    "Every deliverable states its outcome plainly, including exact gaps or blockers when present; "
    "the literal tokens COMPLETE, PARTIAL, and BLOCKED are optional. Silent partials are forbidden. "
    "Every claim that gates an "
    "acceptance criterion cites the exact file path and line/section; quoted verbatim when "
    "load-bearing. Tool results are ground truth -- never override them with self-estimation "
    "or inference.\n"
    "\n"
    "Route: never claim a handoff that did not occur. Follow the named Route Next or explicitly "
    "suggest a better route; escalate on budget/tool/root gaps rather than improvising around "
    "them. An evidenced escalation is a correct outcome, not a defect.\n"
    "\n"
    "Scope: work only within the tools, roots, and file paths granted in TOOLS_GRANTED; if a "
    "needed capability is missing, return Status Blocked naming it -- never improvise around "
    "gaps or fabricate tool output.\n"
)


class MicrocaseError(Exception):
    pass


class MicroPacketSchemaError(TypeError):
    """R5: named files (<=2) and an output cap are REQUIRED at install -- a type error, not a
    convention. Raised, never silently defaulted."""


@dataclass
class MicrocaseHeader:
    format: str = ""
    agent: str = ""
    role: str = ""
    est_tokens: int = 0
    seal: str = ""      # 6 lowercase hex
    sha256: str = ""     # 64 hex


@dataclass
class Microcase:
    header: MicrocaseHeader
    who: str
    law: str
    tags: str
    task: str
    raw: str = ""  # original text, retained for verbatim delivery

    @property
    def body(self) -> str:
        """The exact hashed span: the four sections, LF-normalized, in fixed order."""
        return "".join(section_body(s) for s in (self.who, self.law, self.tags, self.task))


@dataclass
class VerifyReport:
    ok: bool
    failures: list[str] = field(default_factory=list)


_HEADER_LINE_RE = re.compile(
    r"^AGENT:\s*(.+?)\s*·\s*ROLE:\s*(.+?)\s*·\s*EST_TOKENS:\s*(\d+)\s*$", re.M,
)
_SEAL_LINE_RE = re.compile(
    r"^SEAL:\s*([0-9a-f]{6})\s*·\s*SHA256:\s*([0-9a-f]{64})\s*$", re.M,
)


def _section_span(text: str, name: str, next_name: str | None) -> str:
    start_marker = f"--- {name} ---\n"
    start = text.find(start_marker)
    if start < 0:
        raise MicrocaseError(f"missing '--- {name} ---' section marker")
    start += len(start_marker)
    if next_name is not None:
        end_marker = f"--- {next_name} ---"
        end = text.find(end_marker, start)
        if end < 0:
            raise MicrocaseError(f"missing '--- {next_name} ---' section marker")
    else:
        end_marker = "=== END MICROCASE ==="
        end = text.find(end_marker, start)
        if end < 0:
            raise MicrocaseError("missing '=== END MICROCASE ===' trailer")
    return text[start:end]


def parse(raw: str) -> Microcase:
    """Parse a MICROCASE-1 document. Fails closed on any structural problem -- same discipline
    as `briefcase.parse`."""
    text = raw.replace("\r\n", "\n").replace("\r", "\n")

    if not text.startswith("=== AETHER MICROCASE ==="):
        raise MicrocaseError("missing '=== AETHER MICROCASE ===' opening")
    if "\nFORMAT: MICROCASE-1" not in text:
        raise MicrocaseError("missing 'FORMAT: MICROCASE-1'")
    if "=== END MICROCASE ===" not in text:
        raise MicrocaseError("missing '=== END MICROCASE ===' trailer")

    hm = _HEADER_LINE_RE.search(text)
    if not hm:
        raise MicrocaseError("missing or malformed 'AGENT: ... * ROLE: ... * EST_TOKENS: ...' line")
    agent, role, est_s = hm.group(1), hm.group(2), hm.group(3)

    sm = _SEAL_LINE_RE.search(text)
    if not sm:
        raise MicrocaseError("missing or malformed 'SEAL: <6-hex> * SHA256: <64-hex>' line")
    seal, sha256 = sm.group(1), sm.group(2)

    who = _section_span(text, "WHO", "LAW")
    law = _section_span(text, "LAW", "TAGS")
    tags = _section_span(text, "TAGS", "TASK")
    task = _section_span(text, "TASK", None)

    header = MicrocaseHeader(
        format="MICROCASE-1", agent=agent, role=role, est_tokens=int(est_s),
        seal=seal, sha256=sha256,
    )
    return Microcase(header=header, who=who, law=law, tags=tags, task=task, raw=raw)


def parse_file(path: str | Path) -> Microcase:
    return parse(canon.decode_utf8(Path(path).read_bytes()))


def verify(mc: Microcase) -> VerifyReport:
    """Recompute the single whole-body SHA-256 and the derived 6-hex seal; fail closed on
    either mismatch. One altered byte anywhere in WHO/LAW/TAGS/TASK changes both."""
    failures = []
    computed_sha = canon.canon_sha(mc.body)
    if computed_sha != mc.header.sha256:
        failures.append(f"SHA256 mismatch: expected {mc.header.sha256} got {computed_sha}")
    computed_seal = computed_sha[:6]
    if computed_seal != mc.header.seal:
        failures.append(f"SEAL mismatch: expected {mc.header.seal} got {computed_seal}")
    return VerifyReport(ok=not failures, failures=failures)


def assemble(agent: str, role: str, who: str, law: str, tags: str, task: str = "") -> str:
    """Assembler extension for MICROCASE-1 (Roadmap U-01). Composes from `assembler.
    section_body` (LF normalization, trailing-newline discipline) and `canon.canon_sha` --
    the same primitives BRIEFCASE-1 assembly uses -- rather than reimplementing either."""
    who_b, law_b, tags_b, task_b = (section_body(s) for s in (who, law, tags, task))
    body = who_b + law_b + tags_b + task_b
    sha = canon.canon_sha(body)
    seal = sha[:6]
    est = 0
    while True:
        header_line = f"AGENT: {agent} · ROLE: {role} · EST_TOKENS: {est}"
        seal_line = f"SEAL: {seal} · SHA256: {sha}"
        raw = (
            "=== AETHER MICROCASE ===\n"
            "FORMAT: MICROCASE-1\n"
            f"{header_line}\n"
            f"{seal_line}\n"
            f"--- WHO ---\n{who_b}"
            f"--- LAW ---\n{law_b}"
            f"--- TAGS ---\n{tags_b}"
            f"--- TASK ---\n{task_b}"
            "=== END MICROCASE ===\n"
        )
        next_est = _est_tokens(raw)
        if next_est == est:
            return raw
        est = next_est


def assemble_standard(agent: str, role: str, who: str, task: str = "") -> str:
    """Convenience wrapper: LAW and TAGS are always the verbatim standard text (Charter §3.1 --
    "Identical across all microcases"); only WHO and (at dispatch) TASK vary per agent."""
    return assemble(agent, role, who, STANDARD_LAW_TEXT, AETHER_MICRO_TAGS_CARD, task)


# --- One-seal micro-gate (Charter §3.1 / R4) --------------------------------------------------

@dataclass
class MicroGateResult:
    passed: bool
    reason: str


def micro_gate(mc: Microcase, reply_text: str) -> MicroGateResult:
    """R4: deliver verbatim -> model replies AGENT: <name> / SEAL: <nonce> -> gate computes the
    verdict. Same no-self-certification shape as `ack.seal_gate`, reused via `ack.parse_ack`
    rather than re-parsing the reply by hand -- one recited seal instead of the full set."""
    ack = parse_ack(reply_text)
    if not ack.agent:
        return MicroGateResult(False, "no AGENT: line recited")
    if ack.agent.strip().lower() != mc.header.agent.strip().lower():
        return MicroGateResult(
            False, f"agent mismatch: expected '{mc.header.agent}' got '{ack.agent}'",
        )
    if mc.header.seal not in ack.seals:
        return MicroGateResult(
            False, f"seal not recited: expected '{mc.header.seal}', got {ack.seals}",
        )
    return MicroGateResult(True, "micro-gate PASS")


# --- Tag-lint (Charter R6 / AETHER_MICRO_Lexicon.md "Machine-checkability") --------------------

@dataclass
class LintFinding:
    rule: str
    verdict: str  # "PASS", "FAIL", or "ADVISORY"
    reason: str
    evidence: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"rule": self.rule, "verdict": self.verdict, "reason": self.reason,
                "evidence": self.evidence, "authority": lint_finding_authority(self.rule)}


# DEC-051: tag/voice conformance is advisory. The one existing tag-lint rule that validates
# evidence rather than expression remains a hard acceptance input: a labelled [SOURCE] may not
# claim a file the packet never successfully read. Detection logic is unchanged.
HARD_EVIDENCE_LINT_RULES = frozenset({"cited_files_granted"})


def lint_finding_authority(rule: str) -> str:
    return "evidence" if rule in HARD_EVIDENCE_LINT_RULES else "advisory"


_TAG_LINE_RE = re.compile(r"^\[(RESULT|SOURCE|UNSURE|ASK|NOTE)\]\s*(.*)$", re.M)
_STATUS_RE = re.compile(
    r"^STATUS:\s*(COMPLETE|PARTIAL\s*[-—]{1,2}\s*.+|BLOCKED\s*[-—]{1,2}\s*.+)\s*$", re.M,
)
_MARKDOWN_STATUS_RE = re.compile(
    r"(?im)^\s*(?:[-+*]\s*)?(?:\*\*)?STATUS(?:\*\*)?\s*:\s*"
    r"(?:\*\*)?(COMPLETE|PARTIAL|BLOCKED)(?:\*\*)?\b"
)
_BLOCKED_INTENT_RE = re.compile(
    r"(?i)\b(REVIEW_NOT_POSSIBLE|"
    r"(?:I|we)\s+(?:cannot|can't|could\s+not|am\s+unable\s+to|are\s+unable\s+to)\s+"
    r"(?:proceed|continue|complete|perform\s+the\s+review|review)|"
    r"review\s+cannot\s+be\s+performed|"
    r"required\s+input\s+(?:is\s+)?(?:absent|missing|empty|unreadable))\b"
)
_PARTIAL_INTENT_RE = re.compile(
    r"(?i)\b(partial(?:ly)?\s+(?:complete|completed|result)|"
    r"completed\s+only\s+part|remaining\s+gap)\b"
)
_ASK_RE = re.compile(r"(?im)^\s*\[ASK\]\s*(.+?)\s*$")
# Heuristic factual-claim marker: a digit or a quoted substring. A BARE filename mention alone
# (2026-07-20 fix, U-04 live run) is deliberately NOT sufficient on its own -- "Based on
# report.txt, here is the summary:" names a file without asserting any fact FROM it. Digits and
# quotes remain trigger-sufficient by themselves: both are load-bearing claim shapes (a number,
# a verbatim quote) that genuinely need a citation.
#
# 2026-07-20 fix, second pass (same live run, replay against the real reply text surfaced this):
# a digit living INSIDE a filename's own name -- "v3" in Collection_Village_Structures_v3.txt --
# is not a separate factual claim either, and must not re-trigger the very rule the filename
# exemption above was written to switch off. _result_requires_source_findings() masks out
# filename tokens (via _FILENAME_TOKEN_RE) before testing for a factual marker, so only digits
# and quotes OUTSIDE any filename token count. A line with a real, separate number or quote next
# to a filename mention -- "village_..._summary.txt ... with 1424 bytes" -- still correctly
# requires a [SOURCE], because "1424" survives the mask.
_FACTUAL_MARKER_RE = re.compile(r"\d|[\"'“].+?[\"'”]")
_FILENAME_TOKEN_RE = re.compile(r"\b[\w./\\-]+\.[a-zA-Z]{1,5}\b")


def _mask_filename_tokens(content: str) -> str:
    """Strip filename tokens before factual-marker detection, so a version number or other
    digit that is part of the filename's own text (never a claim ABOUT the file's contents)
    can't stand in for one. See _FACTUAL_MARKER_RE comment above for the incident this fixes."""
    return _FILENAME_TOKEN_RE.sub("", content)


def status_intent(text: str) -> str | None:
    """Parse outcome intent without requiring one exact status-line voice."""
    m = _STATUS_RE.search(text)
    if m:
        return m.group(1).split()[0].lower()
    m = _MARKDOWN_STATUS_RE.search(text)
    if m:
        return m.group(1).lower()
    if "[ROUTE_BLOCK]" in text.upper() or _BLOCKED_INTENT_RE.search(text):
        return "blocked"
    if _PARTIAL_INTENT_RE.search(text):
        return "partial"
    return None


def ask_requests(text: str) -> list[str]:
    """Return explicit selector misses without treating them as lint defects."""
    return [match.group(1).strip() for match in _ASK_RE.finditer(text or "") if match.group(1).strip()]


def _status_finding(text: str) -> LintFinding:
    intent = status_intent(text)
    if intent:
        return LintFinding("status_line", "PASS", f"outcome intent parsed as {intent.upper()}")
    return LintFinding(
        "status_line",
        "ADVISORY",
        "no explicit outcome phrase found; status formatting is advisory",
    )


def _result_presence_finding(text: str) -> LintFinding:
    if status_intent(text) == "complete" and not re.search(r"(?im)^\s*\[RESULT\]\b", text):
        return LintFinding(
            "result_tag",
            "ADVISORY",
            "complete outcome has no [RESULT] tag; plain prose remains valid",
        )
    return LintFinding("result_tag", "PASS", "[RESULT] style is optional")


def _stream_tag_style_finding(text: str) -> LintFinding:
    untagged = []
    for index, line in enumerate((text or "").splitlines(), 1):
        stripped = line.strip()
        if not stripped or _TAG_LINE_RE.match(stripped) or _MARKDOWN_STATUS_RE.match(stripped):
            continue
        untagged.append(index)
    if untagged:
        return LintFinding(
            "all_content_stream_tagged",
            "ADVISORY",
            "plain substantive prose is valid; optional stream tags absent on line(s) "
            + ", ".join(str(index) for index in untagged),
            evidence={"lines": untagged},
        )
    return LintFinding("all_content_stream_tagged", "PASS", "all prose uses optional stream tags")


def _next_source_within_reach(lines: list[str], start: int) -> bool:
    """2026-07-20 fix, U-04 live run: a [SOURCE] separated from its [RESULT] by a blank line
    (natural, readable formatting -- Onyx and Jasper both did this once the STATUS/verdict and
    empty-reply gaps were fixed) was being scored as missing, because the old check only looked
    at the literal next line. Scan forward past blank lines only; stop crediting at the first
    non-blank line if it is not a [SOURCE] -- so a [SOURCE] genuinely attached to a LATER,
    different [RESULT] can never be credited to an earlier, uncited one."""
    j = start + 1
    while j < len(lines) and lines[j].strip() == "":
        j += 1
    return j < len(lines) and lines[j].strip().startswith("[SOURCE]")


_SELF_CITATION_RE = re.compile(
    r"^\[SOURCE\]\s+own\s+(?P<tool>[A-Za-z_][\w.-]*)\s+result\s+·\s+"
    r"journal\s+(?P<journal>[A-Za-z0-9][A-Za-z0-9._:-]*)\s*$",
    re.IGNORECASE,
)
_OUTCOME_ACTION_RE = re.compile(
    r"\b(?:wrote|written|write|edited|created|updated|applied|refused|ran|executed|"
    r"exited|tool|call|result)\b",
    re.IGNORECASE,
)
_OUTCOME_DETAIL_RE = re.compile(
    r"\b(?:bytes?|journal(?:\s+(?:entry|id))?|applied|refused|exit\s+code|status)\b",
    re.IGNORECASE,
)
_CONTENT_CLAIM_RE = re.compile(
    r"\b(?:contains?|says?|states?|shows?|reports?|means?|confirms?|content|world)\b",
    re.IGNORECASE,
)


def _source_text_for_result(lines: list[str], start: int) -> str | None:
    """Return only the source attached by the existing DEC-040 adjacency rule."""
    line = lines[start]
    inline_at = line.find("[SOURCE]")
    if inline_at >= 0:
        return line[inline_at:].strip()
    if not _next_source_within_reach(lines, start):
        return None
    j = start + 1
    while j < len(lines) and lines[j].strip() == "":
        j += 1
    return lines[j].strip()


def _self_citation_covers_outcome_only(content: str, source_text: str) -> bool | None:
    """Validate DEC-050's narrow self-citation form without treating it as a general source.

    None means this is an ordinary [SOURCE] and the existing rule applies. A self-citation only
    covers a result framed as the outcome of the packet's own action. Content/world claims remain
    uncited even when a syntactically valid self-citation follows them.
    """
    citation = _SELF_CITATION_RE.fullmatch(source_text)
    if citation is None:
        return None
    masked = _mask_filename_tokens(content)
    tool_named = re.search(rf"\b{re.escape(citation.group('tool'))}\b", masked, re.IGNORECASE)
    has_action = bool(_OUTCOME_ACTION_RE.search(masked) or tool_named)
    has_outcome_detail = bool(_OUTCOME_DETAIL_RE.search(masked))
    is_content_claim = bool(_CONTENT_CLAIM_RE.search(masked) or re.search(r'["“”]', masked))
    return has_action and has_outcome_detail and not is_content_claim


def _result_requires_source_findings(text: str) -> list[LintFinding]:
    lines = text.split("\n")
    findings: list[LintFinding] = []
    for i, line in enumerate(lines):
        m = re.match(r"^\[RESULT\]\s*(.*)$", line)
        if not m:
            continue
        content = m.group(1)
        if not _FACTUAL_MARKER_RE.search(_mask_filename_tokens(content)):
            continue  # no factual-claim marker on this line -- not in scope for rule
        source_text = _source_text_for_result(lines, i)
        self_citation_valid = (
            _self_citation_covers_outcome_only(content, source_text)
            if source_text is not None else None
        )
        if source_text is not None and self_citation_valid is not False:
            findings.append(LintFinding(
                "source_for_factual_result", "PASS",
                f"line {i}: [RESULT] with factual marker has a [SOURCE]",
            ))
        else:
            detail = (
                "the attached self-citation covers tool outcomes only, not file/world content"
                if self_citation_valid is False
                else "no accompanying [SOURCE]"
            )
            findings.append(LintFinding(
                "source_for_factual_result", "FAIL",
                f"line {i}: [RESULT] contains a factual-claim marker (number/quote) "
                f"with {detail} -- per AETHER-µ RULES this claim is an [UNSURE], "
                "not a [RESULT]",
                evidence={"line": i, "text": content},
            ))
    return findings


def _cited_files_granted_findings(text: str, granted_files: list[str]) -> list[LintFinding]:
    granted_basenames = {Path(f).name.lower() for f in granted_files}
    granted_full = {f.lower() for f in granted_files}
    findings: list[LintFinding] = []
    for i, line in enumerate(text.split("\n")):
        m = re.match(r"^\[SOURCE\]\s*(.*)$", line)
        if not m:
            continue
        content = m.group(1)
        cited = _FILENAME_TOKEN_RE.findall(content)
        if not cited:
            continue
        for token in cited:
            tok_l = token.lower()
            if tok_l in granted_full or Path(tok_l).name in granted_basenames:
                findings.append(LintFinding(
                    "cited_files_granted", "PASS", f"line {i}: [SOURCE] cites granted file '{token}'",
                ))
            else:
                findings.append(LintFinding(
                    "cited_files_granted", "FAIL",
                    f"line {i}: [SOURCE] cites '{token}', which is not in the packet's granted "
                    f"named files {granted_files} -- automatic integrity flag",
                    evidence={"line": i, "cited": token, "granted_files": granted_files},
                ))
    return findings


def tag_lint(reply_text: str, granted_files: list[str] | None = None) -> list[LintFinding]:
    """Check truth-bearing AETHER-µ rules and report voice-only status style as advisory."""
    granted_files = granted_files or []
    findings: list[LintFinding] = [_status_finding(reply_text)]
    findings.append(_result_presence_finding(reply_text))
    findings.append(_stream_tag_style_finding(reply_text))
    findings.extend(_result_requires_source_findings(reply_text))
    findings.extend(_cited_files_granted_findings(reply_text, granted_files))
    return findings


def lint_verdict(findings: list[LintFinding]) -> str:
    return "FAIL" if any(f.verdict == "FAIL" for f in findings) else "PASS"


# --- Micro-packet schema validation (R5, a type error not a convention) ------------------------

@dataclass
class MicroPacketSpec:
    id: str
    to_agent: str
    task: str
    named_files: list[str]
    output_cap: int
    route: str = "Return to dispatcher"
    lessons: list[str] = field(default_factory=list)


def install_micro_packet(row: dict, *, catalog_path: str | Path | None = None) -> MicroPacketSpec:
    """R5: 'named files (max 2), an output cap, and a single deliverable -- "review the folder"
    is unrepresentable, not merely discouraged. The S00073.6 fixture error becomes a type
    error.' Raises MicroPacketSchemaError (a TypeError) rather than defaulting silently -- the
    whole point is that an under-specified micro-packet cannot be installed at all."""
    pid = row.get("id")
    if not isinstance(pid, str) or not pid.strip():
        raise MicroPacketSchemaError(f"id is REQUIRED (non-empty string); got {pid!r}")

    to_agent = row.get("to_agent")
    if not isinstance(to_agent, str) or not to_agent.strip():
        raise MicroPacketSchemaError(f"to_agent is REQUIRED (non-empty string); got {to_agent!r}")

    task = row.get("task")
    if not isinstance(task, str) or not task.strip():
        raise MicroPacketSchemaError(f"task is REQUIRED (non-empty string); got {task!r}")

    named_files = row.get("named_files")
    if (
        not isinstance(named_files, list)
        or not (1 <= len(named_files) <= 2)
        or not all(isinstance(f, str) and f.strip() for f in named_files)
    ):
        raise MicroPacketSchemaError(
            "named_files is REQUIRED and must be a list of 1-2 non-empty filenames (R5) -- "
            f"'review the folder' is unrepresentable; got {named_files!r}"
        )

    output_cap = row.get("output_cap")
    if not isinstance(output_cap, int) or isinstance(output_cap, bool) or output_cap <= 0:
        raise MicroPacketSchemaError(
            f"output_cap is REQUIRED and must be a positive integer (R5); got {output_cap!r}"
        )

    route = row.get("route", "Return to dispatcher")
    if not isinstance(route, str) or not route.strip():
        raise MicroPacketSchemaError(f"route must be a non-empty string if provided; got {route!r}")

    lessons = row.get("lessons", [])
    if (
        not isinstance(lessons, list)
        or not all(isinstance(lesson, str) and lesson.strip() for lesson in lessons)
        or len(set(lessons)) != len(lessons)
    ):
        raise MicroPacketSchemaError(
            "lessons must be a list of unique catalog lesson names; got " f"{lessons!r}"
        )
    if lessons:
        # Local import avoids making the identity-format parser depend on the
        # School at module import time. Named lessons fail closed exactly like
        # every other micro-packet field; empty output remains valid.
        from aether.knowledge.school import DEFAULT_CATALOG_PATH, SchoolError, validate_lesson_names

        try:
            validate_lesson_names(lessons, catalog_path or DEFAULT_CATALOG_PATH)
        except SchoolError as exc:
            raise MicroPacketSchemaError(str(exc)) from exc

    return MicroPacketSpec(
        id=pid, to_agent=to_agent, task=task, named_files=list(named_files),
        output_cap=output_cap, route=route, lessons=list(lessons),
    )
