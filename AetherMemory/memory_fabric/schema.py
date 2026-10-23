"""Schema box: what a cartridge contains, and the engine's shared vocabularies.

Inputs:  none (constants).
Outputs: DDL statements, default decay profiles, default per-agent config,
         and the shared vocabularies (categories, strata, relations).

Rule (decision 6): a cartridge holds DATA ONLY: no triggers, no stored views,
no stored logic. Views the engine needs are TEMPORARY and created on open
(see decay.temp_view_sql), so they are never written into the file.
"""
from __future__ import annotations

SCHEMA_VERSION = 4
ENGINE_NAME = "memory_fabric"
ENGINE_REVISION = "memory-fabric-4-foundations"

# Structural role of a note (Gemini draft, axis 1).
CATEGORIES = ("Alert", "Information", "Revelation", "Reflection", "Task", "Thread")

# Epistemic stratum (Gemini draft, axis 2). Optional on a note.
STRATA = ("Experience", "Perception", "Introspection", "Reflection", "Insight", "Abstract")

# Why two notes are linked (restored from the 2025 Librarian design).
RELATIONS = (
    "context",        # belonged to the same situation (e.g. the same project)
    "causal",         # one led to the other
    "temporal",       # happened in sequence
    "conceptual",     # about the same idea
    "behavioral",     # the same habit or pattern of action
    "metacognitive",  # about how the agent thinks
    "thread",         # part of the same line of inquiry
    "derived",        # one was distilled from the other (matters for export)
)

DDL = [
    """CREATE TABLE IF NOT EXISTS meta (
        key   TEXT PRIMARY KEY,
        value TEXT
    )""",
    # Per-agent tunable settings. Every change is logged in config_changes.
    """CREATE TABLE IF NOT EXISTS config (
        key              TEXT PRIMARY KEY,
        value            REAL NOT NULL,
        min_value        REAL NOT NULL,
        max_value        REAL NOT NULL,
        tunable_by_agent INTEGER NOT NULL DEFAULT 1,
        description      TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS config_changes (
        change_id  INTEGER PRIMARY KEY AUTOINCREMENT,
        key        TEXT NOT NULL,
        old_value  REAL,
        new_value  REAL NOT NULL,
        changed_by TEXT NOT NULL,
        reason     TEXT,
        at         TEXT NOT NULL
    )""",
    # The "custom type": named presets so an agent chooses a kind, not six numbers.
    """CREATE TABLE IF NOT EXISTS profiles (
        profile              TEXT PRIMARY KEY,
        importance           REAL NOT NULL,
        slow_rate            REAL NOT NULL,
        fast_rate            REAL NOT NULL,
        horizon_days         REAL NOT NULL,
        refresh_extends_days REAL NOT NULL,
        link_slow_rate       REAL NOT NULL,
        link_fast_rate       REAL NOT NULL,
        description          TEXT NOT NULL,
        source               TEXT NOT NULL DEFAULT 'engine-default'
    )""",
    """CREATE TABLE IF NOT EXISTS notes (
        note_id             TEXT PRIMARY KEY,
        title               TEXT NOT NULL,
        body                TEXT NOT NULL,
        profile             TEXT NOT NULL REFERENCES profiles(profile),
        category            TEXT NOT NULL,
        stratum             TEXT,
        created_at          TEXT NOT NULL,
        refreshed_at        TEXT NOT NULL,
        switch_at           TEXT NOT NULL,
        importance_override REAL,
        slow_rate_override  REAL,
        fast_rate_override  REAL,
        adjust              REAL NOT NULL DEFAULT 0,
        adjust_set_at       TEXT,
        adjust_until        TEXT,
        proprietary         INTEGER NOT NULL DEFAULT 0,
        written_by          TEXT NOT NULL,
        source_ref          TEXT,
        confidence          REAL,
        pull_count          INTEGER NOT NULL DEFAULT 0,
        last_pulled_at      TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS tags (
        note_id TEXT NOT NULL REFERENCES notes(note_id),
        tag     TEXT NOT NULL,
        PRIMARY KEY (note_id, tag)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_tags_tag ON tags(tag)",
    """CREATE TABLE IF NOT EXISTS links (
        link_id         INTEGER PRIMARY KEY AUTOINCREMENT,
        a               TEXT NOT NULL REFERENCES notes(note_id),
        b               TEXT NOT NULL REFERENCES notes(note_id),
        relation        TEXT NOT NULL,
        strength        REAL NOT NULL,
        created_at      TEXT NOT NULL,
        refreshed_at    TEXT NOT NULL,
        switch_at       TEXT NOT NULL,
        slow_rate       REAL NOT NULL,
        fast_rate       REAL NOT NULL,
        reinforce_count INTEGER NOT NULL DEFAULT 0,
        UNIQUE (a, b, relation)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_links_a ON links(a)",
    "CREATE INDEX IF NOT EXISTS idx_links_b ON links(b)",
    # Reviews are written by others. The engine offers no edit or delete for them.
    """CREATE TABLE IF NOT EXISTS reviews (
        review_id     INTEGER PRIMARY KEY AUTOINCREMENT,
        reviewer      TEXT NOT NULL,
        reviewer_role TEXT NOT NULL,
        project       TEXT,
        rating        REAL,
        text          TEXT NOT NULL,
        created_at    TEXT NOT NULL
    )""",
    # Verified outcomes: the only evidence levels are computed from.
    """CREATE TABLE IF NOT EXISTS outcomes (
        outcome_id  INTEGER PRIMARY KEY AUTOINCREMENT,
        specialty   TEXT NOT NULL,
        task_ref    TEXT NOT NULL,
        result      TEXT NOT NULL,
        difficulty  REAL NOT NULL,
        verified_by TEXT NOT NULL,
        at          TEXT NOT NULL
    )""",
    # Highest level ever reached per specialty. Levels never go down (decision 11).
    """CREATE TABLE IF NOT EXISTS levels (
        specialty   TEXT PRIMARY KEY,
        level       INTEGER NOT NULL,
        achieved_at TEXT NOT NULL
    )""",
    # Trading-card identity: name, scan line, the avatar the agent chose.
    """CREATE TABLE IF NOT EXISTS card (
        key   TEXT PRIMARY KEY,
        value TEXT
    )""",
]

# Every table that belongs to the agent's mind, in fingerprint order.
from .history import DDL as HISTORY_DDL
from .sidelog import DDL as SIDELOG_DDL
from .foundations_schema import DDL as FOUNDATIONS_DDL, TABLES as FOUNDATION_TABLES
DDL += HISTORY_DDL
DDL.append(SIDELOG_DDL)
DDL += FOUNDATIONS_DDL
DATA_TABLES = ("config", "config_changes", "profiles", "notes", "tags", "links",
               "reviews", "outcomes", "levels", "card", "meta",
               "history_data", "memory_changes", "operation_receipts", "review_responses",
               "sidelog") + FOUNDATION_TABLES

# A table joins the seal at the schema version that brought it into the engine. An older cartridge's seal was
# written without it (the atelier's side log lived in schema-2 files outside the seal), so the seal of a file is
# always computed over the tables its own version seals.
SEALED_SINCE = {"sidelog": 3, **{name: 4 for name in FOUNDATION_TABLES}}


def sealed_tables(version: int) -> tuple:
    return tuple(t for t in DATA_TABLES if SEALED_SINCE.get(t, 1) <= version)

# (profile, importance, slow/day, fast/day, horizon days, refresh extends days,
#  link slow/day, link fast/day, description). Placeholder calibration: the
#  simulator (scenarios/) is the bench these numbers are tuned against.
DEFAULT_PROFILES = [
    ("project_fact", 0.80, 0.010, 0.400, 7, 3, 0.010, 0.400,
     "Matters during a project; sinks fast after the project ends."),
    # 0.85, deliberately below fog_importance_min (0.90): otherwise every warning
    # would be visible through the fog forever (calibration finding, scenario 08).
    ("safety_warning", 0.85, 0.010, 0.020, 7, 30, 0.010, 0.400,
     "A warning that outlives its situation; its ties to that situation drop fast."),
    ("standing_lesson", 0.70, 0.002, 0.010, 90, 90, 0.005, 0.050,
     "Long-lived know-how; fades slowly unless used."),
    ("core_knowledge", 0.95, 0.0005, 0.002, 365, 365, 0.002, 0.020,
     "Rare, foundational knowledge; visible through the fog."),
    ("task_obligation", 0.85, 0.005, 0.300, 14, 7, 0.010, 0.300,
     "A promise or to-do; sinks fast once its date passes."),
    ("scratch", 0.50, 0.050, 0.600, 1, 1, 0.100, 0.600,
     "In-flight scratch notes and guesses; gone from view within days."),
    ("offloaded_context", 0.60, 0.020, 0.200, 2, 2, 0.020, 0.300,
     "Set down from the working board during context reformation; easy to pull back."),
    # A ruling by the operator (or the lead recording the operator's words). 0.90 = fog_importance_min, so it is
    # visible through the fog; the same slow rate before and after the horizon, so it has no fast phase; a year's
    # horizon. Added 2026-10-19 (decision memory); additive: older cartridges receive it on open.
    ("decision", 0.90, 0.001, 0.001, 365, 365, 0.002, 0.002,
     "A ruling someone with the authority gave, in their words; it binds its project, or every project when standing."),
]

# (key, value, min, max, tunable_by_agent, description)
DEFAULT_CONFIG = [
    ("floor", 0.10, 0.10, 0.10, 0,
     "Nothing ever decays below this (never-delete, decision 13)."),
    ("surface_threshold", 0.30, 0.15, 0.60, 1,
     "Minimum reach for a note to come up in recall."),
    ("fog_importance_min", 0.90, 0.85, 0.98, 1,
     "Notes at least this important can be seen through the fog."),
    ("fog_level", 0.50, 0.35, 0.70, 1,
     "The value a very important note keeps when reached through a thread."),
    ("hop_decay", 0.90, 0.60, 1.00, 1,
     "Reach multiplier for each extra hop along links."),
    ("max_hops", 3, 1, 5, 1,
     "How far along links one recall may travel."),
    ("default_link_strength", 0.80, 0.30, 1.00, 1,
     "Strength of a new link when none is given."),
    ("reinforce_delta", 0.05, 0.00, 0.15, 1,
     "How much a link strengthens when its notes are used together."),
    ("alert_boost", 0.15, 0.00, 0.30, 1,
     "Ranking bonus for Alert notes (alerts first)."),
    ("tag_match_bonus", 0.10, 0.00, 0.25, 1,
     "Ranking bonus per extra matching tag."),
    ("recurring_pulls", 3, 2, 10, 1,
     "After this many pulls an alert is flagged 'recurring: consider a permanent fix'."),
    ("reform_threshold", 0.85, 0.60, 0.95, 1,
     "Fraction of the usable window at which the host asks the agent to reform its board."),
    # Hydration (decision 49): a share of the measured working context, between a floor
    # and a ceiling. The ranges are the harness's hard limits; within them the agent dials.
    ("hydration_share", 0.02, 0.005, 0.10, 1,
     "Share of your working context your memory may fill when a session starts."),
    ("hydration_floor", 600, 400, 20000, 1,
     "Fewest tokens your session-start memory gets, however small the window."),
    ("hydration_ceiling", 40000, 1000, 100000, 1,
     "Most tokens your session-start memory gets, however large the window."),
]

# Settings that must stay ordered (low <= high); a change that would cross them is refused.
ORDERED_SETTINGS = (("hydration_floor", "hydration_ceiling"),)
