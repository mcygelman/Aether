"""The postmaster: the atelier's side of the mailboxes.

It delivers every new daybook note to the mailboxes it addresses (a note with no recipient is to the whole
commission; a host question goes only to whom it asks) and collects every outbox note into the daybook through the
same `Atelier.post`, so record, facts, questions, meter and the four stops are identical for a mind across the
network and for one in this process. A note that carries a credential is bounced back to its author with the reason;
a money, consent or harm stop records the note as sent and re-raises, so the atelier waits for the operator.
Nothing is refused for shape.
"""
from __future__ import annotations

import re
import time
from aether.config.model_settings_store import writer_lock, StaleSettings

from pathlib import Path

from aether.mailbox import Mailbox, MailboxError, BROADCAST

from .atelier import Atelier, AtelierStop, is_capacity_failure
from .daybook import DaybookError
from . import faults


class Postmaster:
    def __init__(self, atelier: Atelier, mailboxes_root: Path):
        self.atelier = atelier
        self.root = Path(mailboxes_root)
        (self.root / 'mailboxes').mkdir(parents=True, exist_ok=True)

    def participants(self) -> list:
        return Mailbox.all_under(self.root)

    def _addressed(self, note: dict, participant_id: str, kind: str | None, name: str | None = None) -> bool:
        if is_capacity_failure(note):
            return False                     # includes retained, formerly addressed capacity failures
        if note['sender']['id'] == participant_id:
            return False
        to = note.get('to')
        if to is BROADCAST:
            return note['kind'] != 'host'      # the atelier's own unaddressed notes (filed, budget) are record, not mail
        # A note is addressed by id or by name, as a person would write it; case does not matter.
        mine = {str(x).lower() for x in (participant_id, name) if x}
        target = {str(x).lower() for x in (to.get('id'), to.get('name')) if x}
        if mine & target:
            return True
        words = {w for x in mine for w in re.split(r'[\s\-_]+', x) if len(w) >= 3}
        if any(t in words for t in target if len(t) >= 3):      # "Guide" reaches Harbor Guide
            return True
        return to.get('id') == kind or (to.get('kind') == kind and not to.get('id'))

    def deliver(self, kinds: dict | None = None, names: dict | None = None) -> dict:
        """Copy every daybook note not yet delivered into each addressed mailbox. Idempotent across restarts.
        `kinds` and `names` map a participant id to its kind and its name (a note may be addressed by either)."""
        kinds = kinds or {}
        names = names or {}
        rows = self.atelier.daybook.read()
        campaign = self.atelier.facts()['campaign']
        library_request = campaign.get('request_kind') == 'library'
        chat_with = campaign.get('chat_with') if campaign.get('request_kind') == 'chat' else None
        librarians = {r['sender']['id'] for r in rows if r['sender']['kind'] == 'librarian'}
        delivered = {}
        for pid in self.participants():
            if chat_with and pid != chat_with:
                continue  # One-to-one chat replies never wake other mailboxes on the network.
            if library_request and kinds.get(pid) != 'librarian' and pid not in librarians:
                continue  # The operator explicitly chose a library conversation, not a team commission.
            box = Mailbox(self.root, dict(id=pid, kind=kinds.get(pid) or 'member'))
            last = box.delivered_through(self.atelier.daybook.campaign_id)
            count = 0
            for note in rows:
                if note['n'] <= last:
                    continue
                if self._addressed(note, pid, kinds.get(pid), names.get(pid)) and box.deliver(note):
                    count += 1
                last = note['n']
            box.mark_delivered(last, self.atelier.daybook.campaign_id)
            delivered[pid] = count
        return delivered

    def collect(self, only=None) -> dict:
        # Serialize the check/post/move transaction across manual wakes and network collectors.
        # Retrying lock contention is bookkeeping, not a model retry or an operator gate.
        for attempt in range(101):
            try:
                with writer_lock(self.root / 'mailboxes' / '.collect.lock'):
                    return self._collect(only)
            except StaleSettings:
                if attempt == 100:
                    raise
                time.sleep(0.05)

    def _collect(self, only=None) -> dict:
        """Hand every outbox note to the atelier, in the order written. `only` limits it to the minds a place hosts,
        so two places never take the same note."""
        posted, bounced = [], []
        for pid in (list(only) if only is not None else self.participants()):
            box = Mailbox(self.root, dict(id=pid, kind='member'))
            for path, item in box.pending():
                rows = self.atelier.daybook.read()
                reply = item.get('in_reply_to')
                if reply and not any(r['id'] == reply for r in rows):
                    continue  # Another conversation owns this reply; keep it for its collector.
                already = next((r for r in rows if (r.get('facts') or {}).get('mailbox_item') == item['id']), None)
                if already is not None:                  # posted before a crash could move the outbox copy to sent: once, not twice
                    box.sent(path)
                    posted.append(dict(participant=pid, id=item['id'], note_id=already['id'], recovered=True))
                    continue
                try:
                    refs = [self.atelier.daybook.store_file(name, data, item['sender']) for name, data in item['files'].items()]
                    facts = dict(mailbox_item=item['id'], **(dict(memory=item['memory']) if item.get('memory') else {}))
                    note = self.atelier.post(item['sender'], item['text'], files=refs, cost=item.get('cost') or None, to=item.get('to'),
                                             place=item.get('place'), facts=facts)
                except AtelierStop as stop:
                    if stop.reason != 'secrets':
                        # The note was recorded, then the atelier stopped (money, consent, harm): it is sent, and the
                        # atelier waits for the operator. Nothing is bounced and nothing is lost.
                        box.sent(path)
                        posted.append(dict(participant=pid, id=item['id'], note_id=None, stopped=stop.reason))
                        raise
                    box.bounce(path, 'secrets', stop.detail)
                    bounced.append(dict(participant=pid, id=item['id'], reason='secrets'))
                    continue
                except (DaybookError, MailboxError) as exc:
                    box.bounce(path, 'shape', str(exc))
                    bounced.append(dict(participant=pid, id=item['id'], reason='shape'))
                    continue
                faults.point('after_collect_post')
                box.sent(path)
                posted.append(dict(participant=pid, id=item['id'], note_id=note['id']))
        return dict(posted=posted, bounced=bounced)

    def exchange(self, kinds: dict | None = None, names: dict | None = None, *, only=None, deliver: bool = True) -> dict:
        """One round: collect what minds wrote, then deliver what is new. The loop a transport daemon would run."""
        collected = self.collect(only)
        return dict(collected, delivered=self.deliver(kinds, names) if deliver else {})
