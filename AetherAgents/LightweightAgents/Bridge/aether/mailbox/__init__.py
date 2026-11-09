"""Aether Mailbox (MOD-19).

A participant is a mailbox and a mind that reads it. The mailbox is a directory the mind can reach: on this machine,
on a share, or (later) behind an HTTP inbox; the mind does not know or care where the others run. This is the
"network" underneath the atelier: correspondence, like email, with no control of the mind, only
the protocol of transmission. The mailbox knows nothing about commissions, daybooks or budgets; it stores and forwards.

Contract: Mailbox(root, participant) owns <root>/mailboxes/<participant id>/ with inbox/, outbox/, sent/, bounced/
and delivered.json. inbox(unread_only) -> notes delivered here in order; mark_read(note_id); send(text, files=, cost=,
to=, in_reply_to=) -> the outbox file written (files travel base64 inside the note); pending() -> outbox notes in
the order written, each with its path; deliver(note) writes a daybook note into the inbox once (idempotent by note
number); delivered_through() / mark_delivered(n) keep the last daybook number delivered; sent(path) and
bounce(path, reason, detail) move an outbox note on; bounces() lists them. Nothing here refuses a note for its shape.
Depends on: nothing in Aether (standard library only).
Owner: Mailbox maintainers.
"""
from .mailbox import Mailbox, MailboxError, BROADCAST

__all__ = ['Mailbox', 'MailboxError']     # BROADCAST (None, a note to everyone) is importable but not an interface value


def __dir__():
    return __all__          # the interface is the contract; submodules are reached by their own imports
