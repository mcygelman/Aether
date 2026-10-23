"""Atelier (MOD-18).

A workshop of minds (Michel, 2026-10-18). A campaign is a commission; the operator opens it with a commission letter;
the master (a heavyweight) runs it by writing notes to assistants and the critic; the daybook is the workshop's
chronological record; the atelier itself delivers notes, keeps the daybook, meters the budget, and intervenes only
for money, consent, secrets or harm. Design: AetherPlanAndAnalysis/Specifications/MVP/FABLE_ATELIER_DESIGN_20261018.md
under the charter FABLE_TEAM_CHARTER_AND_DEMOLITION_20261018.md.

Contract: Daybook(root, commission_id): append-only notes with a hash chain, files stored by content hash,
read(since_id, limit), verify() that reports and never blocks. rebuild(daybook) -> facts (pure, rebuildable);
questions(facts) -> what the atelier still needs and whom to ask. Atelier(root, commission_id, budget, secrets):
open(operator, letter) is the authorization; post(sender, text, files, cost, to) records, derives, asks and meters;
wake_context(participant) is what a mind sees when it wakes (facts, unread notes, its own prior closures, the budget
left, the questions for it); closure(participant, text) and filed(participant, where); numbers() -> thinking minutes
per harness minute, refusals per hundred calls, approvals per sprint. AtelierStop(reason) for money, consent,
secrets, harm only. Postmaster(atelier, mailboxes_root) delivers daybook notes to mailboxes and collects outbox notes
through post(). No preview, no confirm, no hold, no re-welcome, no template refusal: an atelier that needs one is
growing fences.
Depends on: roadmaps (digest, checked_path), integrity (through roadmaps), mailbox. Never imports Mission Control.
Owner: Fable (claude-fable-5-1).

participants.py and turn.py (the minds and their turns) are the turn-runner lane; atelier_http.py and the page are
the page lane.
"""
from .daybook import Daybook, DaybookError
from .facts import rebuild, questions
from .atelier import Atelier, AtelierStop, Budget
from .postmaster import Postmaster
from .participants import Participant
from .turn import run_turn, Deployment
from .place import Place, PlaceClaimed

__all__ = [
    'Daybook', 'DaybookError', 'rebuild', 'questions', 'Atelier', 'AtelierStop', 'Budget',
    'Postmaster', 'Participant', 'run_turn', 'Deployment', 'Place', 'PlaceClaimed',
]


def __dir__():
    return __all__          # the interface is the contract; submodules are reached by their own imports
