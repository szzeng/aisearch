"""One-time local session bootstrap and in-memory session state (architecture §10.3).

The launcher writes a random credential under ``<data_root>/runtime/``; the only way to
obtain a session is a single successful exchange of that credential. The credential is
burned on first use, a new process always generates a new one, and comparison is
constant-time so a local process cannot probe it by timing or by reading error detail.

Nothing in this module logs the credential, and the session store is memory-only: a
restarted process invalidates every session it handed out.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from photo_search.api.errors import VALIDATION_ERROR, ApiError
from photo_search.api.settings import ApiSettings, generate_session_token
from photo_search.config import session_credential_path

SESSION_COOKIE = "ps_session"
CSRF_COOKIE = "ps_csrf"
CSRF_HEADER = "x-csrf-token"
COOKIE_PATH = "/"
COOKIE_SAMESITE = "strict"

CREDENTIAL_SCHEMA = 1

#: Characters that must never appear in a same-origin redirect target.
_FORBIDDEN_REDIRECT_CHARS = ("\r", "\n", "\\")


def safe_redirect_target(value: str | None) -> str | None:
    """Return the local absolute path a successful exchange may redirect to.

    ``next`` is only ever honoured when it is a plain same-origin path: it must start with
    exactly one ``/``, must not be scheme-relative (``//host``), must not carry a scheme or
    a backslash spelling that a browser could read as a network path, and must not contain
    a control character. Anything else is refused with a structured ``422`` *before* the
    one-time credential is burned, so a mistyped link never costs the user their session.
    """
    if value is None:
        return None
    text = str(value)
    if not text:
        raise ApiError(
            VALIDATION_ERROR,
            "the next parameter must not be empty",
            status=422,
            details={"param": "next"},
        )
    problem: str | None = None
    if not text.startswith("/"):
        problem = "the next parameter must be an absolute path on this server"
    elif text.startswith("//"):
        problem = "the next parameter must not be scheme-relative"
    elif "://" in text:
        problem = "the next parameter must not carry a scheme"
    elif any(char in text for char in _FORBIDDEN_REDIRECT_CHARS):
        problem = "the next parameter must not contain a control character or a backslash"
    elif any(ord(char) < 0x20 for char in text):
        problem = "the next parameter must not contain a control character"
    if problem is not None:
        raise ApiError(VALIDATION_ERROR, problem, status=422, details={"param": "next"})
    return text


def _system_now_ms() -> int:
    return time.time_ns() // 1_000_000


@dataclass(frozen=True)
class Session:
    """One live local session: an opaque id plus its double-submit CSRF value."""

    session_id: str
    csrf_token: str
    created_at_ms: int


class SessionManager:
    """Owns the one-time credential and every session it has issued."""

    def __init__(
        self,
        token: str,
        *,
        now_ms: Callable[[], int] | None = None,
        token_source: str = "generated",
    ) -> None:
        if not str(token):
            raise ValueError("the bootstrap session token must not be empty")
        self._token = str(token)
        self._token_digest = hashlib.sha256(self._token.encode("utf-8")).digest()
        self._token_used = False
        self._sessions: dict[str, Session] = {}
        self._now_ms = now_ms or _system_now_ms
        self.token_source = str(token_source)

    @property
    def bootstrap_used(self) -> bool:
        return self._token_used

    @property
    def active_session_count(self) -> int:
        return len(self._sessions)

    def matches_bootstrap_token(self, supplied: str | None) -> bool:
        """Constant-time credential comparison over fixed-length digests."""
        if supplied is None:
            return False
        candidate = hashlib.sha256(str(supplied).encode("utf-8")).digest()
        return hmac.compare_digest(candidate, self._token_digest)

    def exchange(self, supplied: str | None) -> Session | None:
        """Burn the credential and open one session, or return ``None``.

        A used credential, a wrong credential and a missing credential are deliberately
        indistinguishable to the caller: all three answer ``SESSION_INVALID``.
        """
        if self._token_used:
            return None
        if not self.matches_bootstrap_token(supplied):
            return None
        self._token_used = True
        session = Session(
            session_id=secrets.token_urlsafe(32),
            csrf_token=secrets.token_urlsafe(32),
            created_at_ms=int(self._now_ms()),
        )
        self._sessions[session.session_id] = session
        return session

    def lookup(self, session_id: str | None) -> Session | None:
        if not session_id:
            return None
        return self._sessions.get(str(session_id))

    def revoke(self, session_id: str | None) -> bool:
        if not session_id:
            return False
        return self._sessions.pop(str(session_id), None) is not None

    def clear(self) -> None:
        self._sessions.clear()


@dataclass(frozen=True)
class CredentialBootstrap:
    """Where the bootstrap credential came from and how it was protected on disk."""

    token: str
    source: str
    path: Path | None
    permission: str

    def as_dict(self) -> dict[str, object]:
        return {
            "source": self.source,
            "path": None if self.path is None else str(self.path),
            "permission": self.permission,
        }


def bootstrap_session_credential(settings: ApiSettings) -> CredentialBootstrap:
    """Resolve the credential: an explicit override wins, otherwise write a fresh one."""
    if settings.session_token:
        return CredentialBootstrap(
            token=settings.session_token, source="override", path=None, permission="not-written"
        )
    token = generate_session_token()
    path = session_credential_path(settings.data_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        {
            "schema": CREDENTIAL_SCHEMA,
            "token": token,
            "pid": os.getpid(),
            "created_at": int(_system_now_ms()),
        },
        ensure_ascii=True,
        sort_keys=True,
    )
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    permission = restrict_credential_file(path)
    return CredentialBootstrap(token=token, source="file", path=path, permission=permission)


def restrict_credential_file(path: Path) -> str:
    """Best-effort narrowing of the credential file's permissions.

    The file lives inside the user's own data root, so the baseline already excludes other
    accounts. A POSIX mode of ``0600`` and a Windows DACL that drops inheritance are both
    attempted. The Windows entry retains ``DELETE`` for the owning user: replacing a prior
    one-time credential uses ``os.replace``, which must be able to delete its old target.
    A failure (a locked-down account, a sandbox, a missing binary) is reported instead of
    being silently claimed as success.
    """
    if os.name != "nt":
        try:
            os.chmod(path, 0o600)
        except OSError as error:
            return f"chmod-failed:{type(error).__name__}"
        return "posix-0600"
    user = os.environ.get("USERNAME") or ""
    if not user:
        return "inherited:no-username"
    try:
        completed = subprocess.run(
            ["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:(R,W,D)"],
            capture_output=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        return f"inherited:{type(error).__name__}"
    if completed.returncode == 0:
        return "windows-restricted-acl"
    return f"inherited:icacls-exit-{completed.returncode}"
