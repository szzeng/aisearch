"""Person knowledge package with a lazy facade import to avoid API-layer cycles."""

from __future__ import annotations

from typing import Any

__all__ = ["PersonService"]


def __getattr__(name: str) -> Any:
    """Load the public service facade only when callers request it.

    API read models are imported while the application module initializes. Eagerly importing
    ``PersonService`` here pulled ``persons.albums`` back into ``api.queries`` during that
    initialization, so normal CLI startup failed with a partially initialized module.
    """
    if name == "PersonService":
        from photo_search.persons.service import PersonService

        return PersonService
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
