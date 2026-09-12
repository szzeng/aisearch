"""Local HTTP business API: read models, controlled file access and session protection.

P1-04 exposes the already accepted catalog and derived-media capabilities over
``/api/v1`` on the loopback interface only. Search, persons, models and index generations
belong to later phases and are deliberately absent.
"""

from photo_search.api.errors import ApiError
from photo_search.api.session import Session, SessionManager
from photo_search.api.settings import ApiSettings, BindNotAllowed

__all__ = ["ApiError", "ApiSettings", "BindNotAllowed", "Session", "SessionManager"]
