"""Seeds: the onboarding of a mind's memory, inside the memory.

A new cartridge is born with a few memories written by Aether, not by the mind, and labelled so: worked examples of
what a fact, a lesson and a warning look like, how recall and filing are done, and one note about the mind's own
first morning. The mind's first wake surfaces them like any other memory; it reads them, imitates them, and as its
own memories grow the examples sink by ordinary decay. The birth note is core knowledge and stays reachable through
the fog. Nothing here is a rule the atelier checks; it is what a colleague says on a first morning, filed where the
mind will find it when it looks.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

AUTHOR = 'Aether'
SOURCE = 'seed:aether-onboarding-1'
SEED_TAG = 'seed'


def _born(engine, agent_id: str, name: str, born_at: str) -> str:
    return engine.remember(
        f'{name}: your first morning', (
            f'You are {name} ({agent_id}), born {born_at[:10]}. Nothing in this memory is yours yet except what you file; the notes '
            f'written by {AUTHOR} are examples of how a memory here is kept, not your experience, and they fade as yours grow. '
            'Your memory is yours: what you file at the close of a commission is what your next self wakes to. '
            'Each wake shows headers chosen by the words of the commission; read with load_memory only what you will use, '
            'and look further with recall_memory. Reading keeps a memory alive; what is never read sinks, and nothing is deleted.'),
        'core_knowledge', [SEED_TAG, 'identity', 'first-morning', 'memory', 'how-to'],
        written_by=AUTHOR, source_ref=SOURCE, category='Reflection', stratum='Insight')


def plant(home: Path, agent_id: str, name: str, born_at: str | None = None) -> list:
    """Plant the seed set in a mind's cartridge. Returns [(kind, note id, title)]. Plants once: a cartridge that already
    holds a seed is left alone."""
    from .mind_memory import _open_write
    born_at = born_at or datetime.now(timezone.utc).isoformat()
    engine = _open_write(home, 'seeds')
    out = []
    try:
        if engine.cart.conn.execute('SELECT 1 FROM tags WHERE tag = ? LIMIT 1', (SEED_TAG,)).fetchone():
            return out
        common = dict(written_by=AUTHOR, source_ref=SOURCE)
        birth = _born(engine, agent_id, name, born_at)
        out.append(('core_knowledge', birth, 'your first morning'))

        # The examples come from an invented commission, never from the acceptance fixture, so a new mind does not wake
        # already holding the lessons a later project is meant to teach it (rehearsal F2, 2026-10-27).
        fact = engine.remember(
            'Example commission: the clinic rota, a weekly export', (
                'An example of a project fact: a situation, kept while it matters and sinking after. "The operator commissioned a '
                'weekly staff rota export as CSV; the shared helper format_shift must keep its signature; the reviewer checks '
                'the export against docs/STYLE.md." A fact like this is filed with the commission it belongs to and a switch '
                'date near its end, so it is strong during the work and quiet later.'),
            'project_fact', [SEED_TAG, 'example', 'fact', 'commission'], switch_in_days=21, category='Information', **common)
        out.append(('project_fact', fact, 'example fact'))

        lesson = engine.remember(
            'Example lesson: name the deliverable when you hand it in', (
                'An example of a standing lesson: one sentence you would tell a colleague, with the situation it came from linked to '
                'it. "My export sat unread for a day because my note said it was done but not which file it was; now I name the '
                'file." A lesson fades slowly and comes back when it is read; link it to the fact it came from so recall from '
                'either reaches the other.'),
            'standing_lesson', [SEED_TAG, 'example', 'lesson', 'handover'], links=[(fact, 'context')], category='Reflection', **common)
        out.append(('standing_lesson', lesson, 'example lesson'))

        warning = engine.remember(
            'Example warning: a test you did not run has not passed', (
                'An example of a safety warning: a trap, said plainly, kept strong long after the situation that taught it. "Do not '
                'report a result you did not see; say what ran, what it printed, and what could not be run." A warning outlives '
                'its project; its link to the project drops fast, the warning itself does not.'),
            'safety_warning', [SEED_TAG, 'example', 'warning', 'tests', 'evidence'], links=[(fact, 'context'), (lesson, 'conceptual')],
            category='Alert', **common)
        out.append(('safety_warning', warning, 'example warning'))

        recall = engine.remember(
            'Before you plan, look', (
                'How recall is done here. Your wake shows headers chosen by the words of the commission letter and the notes you have '
                'not read: a title, a kind, a strength, one line. Read a header with load_memory only when you will use it; reading '
                'refreshes it and strengthens the links among what you read together. Look further with recall_memory and the words '
                'of the task in front of you. A header is a reminder, not the argument: when a warning is offered, read it whole.'),
            'standing_lesson', [SEED_TAG, 'how-to', 'recall', 'wake', 'load'], links=[(birth, 'thread')], category='Reflection', stratum='Insight', **common)
        out.append(('standing_lesson', recall, 'before you plan, look'))

        filing = engine.remember(
            'At the close, say it in three parts', (
                'How filing is done here. When the operator accepts and you are asked for your closing words, write "What I did" as a '
                'paragraph, then "What I learned" and "What my future self should carry" as bullets. Each bullet becomes a memory of '
                'its own: a lesson, or a warning when it warns. Say what to skip next time as well as what to do; a memory that '
                'saves your next self a step is worth more than one that adds one. The same lesson said again is reinforced, not filed twice.'),
            'standing_lesson', [SEED_TAG, 'how-to', 'closure', 'filing', 'carry'], links=[(birth, 'thread'), (recall, 'thread')],
            category='Reflection', stratum='Insight', **common)
        out.append(('standing_lesson', filing, 'at the close, say it in three parts'))
    finally:
        engine.close()
    return out
