"""P3-03 person knowledge: naming, same-name/explicit merging, redirects and undo.

The package is deliberately small and layered so the frozen rules stay auditable:

* :mod:`photo_search.persons.contracts` - the ``nfc1:`` name key, the deterministic operation
  id, the payload hash and the error codes of ADR-0006 §9;
* :mod:`photo_search.persons.identity` - the one ``merged_into`` resolver, including the
  bounded cycle refusal;
* :mod:`photo_search.persons.bundle` - the bounded before/after snapshot an undo compares;
* :mod:`photo_search.persons.operations` - the single merge use case plus rename and
  compensation, and the authoritative→derived commit order;
* :mod:`photo_search.persons.service` - the thin facade the API calls.

Nothing here reads or writes a path, a face box, a landmark, a vector or a similarity.
"""

from photo_search.persons.service import PersonService

__all__ = ["PersonService"]
