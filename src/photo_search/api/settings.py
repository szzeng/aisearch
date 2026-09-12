"""Local-only API settings: bind policy, allowed hosts and the one-time session token.

Architecture §10.3 fixes the access boundary this module encodes: the application listens
on the loopback interface by default, a non-loopback bind needs an explicit opt-in, and a
random per-process credential is the only way a local process can obtain a session.

Nothing here performs I/O: ``create_app`` must stay free of side effects so the
higher-schema-version preflight can be verified without creating anything on disk.
"""

from __future__ import annotations

import ipaddress
import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

from photo_search.config import resolve_data_root

#: Diagnostic/test override for the bootstrap credential (never used by a normal run).
SESSION_TOKEN_ENV = "PHOTO_SEARCH_SESSION_TOKEN"
#: Overrides the bind host without touching the CLI (diagnostics only).
BIND_HOST_ENV = "PHOTO_SEARCH_BIND_HOST"
BIND_PORT_ENV = "PHOTO_SEARCH_BIND_PORT"

DEFAULT_BIND_HOST = "127.0.0.1"
DEFAULT_BIND_PORT = 8000
#: Host spellings a browser may legitimately use for this machine's loopback interface.
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1", "[::1]")
#: Bind addresses that mean "every interface"; they are never a browser ``Host`` value.
WILDCARD_BIND_HOSTS = ("0.0.0.0", "::", "[::]")


class BindNotAllowed(ValueError):
    """The configured bind address would expose the API beyond this machine."""


def is_loopback_host(host: str) -> bool:
    """True when ``host`` names the local machine's loopback interface."""
    text = str(host).strip().lower()
    if not text:
        return False
    if text in ("localhost",):
        return True
    try:
        return ipaddress.ip_address(text.strip("[]")).is_loopback
    except ValueError:
        return False


def generate_session_token() -> str:
    """A fresh, high-entropy bootstrap credential for one application process."""
    return secrets.token_urlsafe(32)


@dataclass(frozen=True)
class ApiSettings:
    """Everything the HTTP layer needs to know about this local installation."""

    data_root: Path
    bind_host: str = DEFAULT_BIND_HOST
    bind_port: int = DEFAULT_BIND_PORT
    allow_non_loopback: bool = False
    session_token: str | None = None
    #: Explicit override (tests); ``None`` derives the set from the bind address.
    allowed_hosts: frozenset[str] | None = field(default=None)

    @classmethod
    def create(
        cls,
        *,
        data_root: str | Path | None = None,
        bind_host: str | None = None,
        bind_port: int | None = None,
        allow_non_loopback: bool = False,
        session_token: str | None = None,
        allowed_hosts: frozenset[str] | None = None,
    ) -> ApiSettings:
        """Build settings, letting explicit values win over the diagnostic overrides."""
        host = bind_host if bind_host is not None else os.environ.get(BIND_HOST_ENV, DEFAULT_BIND_HOST)
        port = bind_port
        if port is None:
            raw_port = os.environ.get(BIND_PORT_ENV)
            port = int(raw_port) if raw_port else DEFAULT_BIND_PORT
        token = session_token if session_token is not None else os.environ.get(SESSION_TOKEN_ENV)
        return cls(
            data_root=Path(resolve_data_root(data_root)),
            bind_host=str(host),
            bind_port=int(port),
            allow_non_loopback=bool(allow_non_loopback),
            session_token=token or None,
            allowed_hosts=allowed_hosts,
        )

    # ------------------------------------------------------------------- policy
    @property
    def loopback_only(self) -> bool:
        return is_loopback_host(self.bind_host)

    def effective_allowed_hosts(self) -> frozenset[str]:
        """Host spellings accepted in the ``Host`` header.

        The set is deliberately small: DNS rebinding is defeated by rejecting any ``Host``
        that is not a loopback spelling (or, for an explicitly allowed non-loopback bind,
        that exact address).
        """
        if self.allowed_hosts is not None:
            return frozenset(str(value).lower() for value in self.allowed_hosts)
        hosts = {value.lower() for value in LOOPBACK_HOSTS}
        candidate = str(self.bind_host).strip().strip("[]").lower()
        if candidate and candidate not in WILDCARD_BIND_HOSTS and not candidate.startswith("*"):
            hosts.add(candidate)
        return frozenset(hosts)

    def validate_bind(self) -> None:
        """Refuse a non-loopback bind unless that exposure was requested explicitly."""
        if self.loopback_only:
            return
        if self.allow_non_loopback:
            return
        raise BindNotAllowed(
            f"refusing to listen on non-loopback address {self.bind_host!r}; "
            "pass the explicit non-loopback switch to accept the exposure "
            "(no LAN authentication is implemented)"
        )

    def browser_host(self) -> str:
        """The host a browser on this machine should use for the session exchange."""
        if self.loopback_only or str(self.bind_host).strip() in WILDCARD_BIND_HOSTS:
            return DEFAULT_BIND_HOST
        return str(self.bind_host)
