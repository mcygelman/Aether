"""MOD-20 Aether Library: a collection with a catalog, a librarian's shelf.

Contract (what `__all__` promises):
  Library(root, collection, title=) owns <root>/libraries/<collection>/ with catalog.json, items/, deposits/,
  declined/ and a hash-chained ledger.jsonl.
  catalog(), entries(), entry(id), search(query, tags=, kind=, limit=) -> ranked entries (never items);
  add(id, title=, kind=, summary=, files=, tags=, source=, related=, by=), edit(id, **changes), read(id, name=),
  lend(id, to, into) copies an item into a commission's library folder and counts it;
  deposit(sender, files, note, commission=), deposits(), file(deposit_id, ...), decline(deposit_id, why);
  ledger(limit=), verify() reports the chain and the files; Library.all_under(root).
  LibraryError for a wrong id or kind. Nothing here chooses for a mind; the librarian does.
"""
from .library import Library, LibraryError, KINDS, LEDGER_KINDS, SCHEMA

__all__ = ['Library', 'LibraryError', 'KINDS', 'LEDGER_KINDS', 'SCHEMA']


def __dir__():
    return __all__          # the interface is the contract; submodules are reached by their own imports
