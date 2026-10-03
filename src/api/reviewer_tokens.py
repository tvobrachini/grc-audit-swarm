"""Per-reviewer tokens (opt-in; see DECISIONS.md, ADR-012).

When ``REVIEWER_TOKENS_FILE`` is set, every reviewer action (gate approval,
return for rework, retry, QA override, reviewer decision) and the creation of
an audit need a personal token in the ``X-Reviewer-Token`` header, on top of
the shared API token. The name the token belongs to becomes the identity
recorded in the approval trail and on decisions, with
``identity_source: "authenticated"``.

The file maps reviewer names to a SHA-256 digest of their token; it never
holds a token. Tokens are 256-bit random values made by the CLI below, so a
plain digest is enough to keep a leaked file from revealing them (ADR-012
explains why there is no salt or server key). The CLI prints a new token
once and stores only its digest::

    PYTHONPATH=src uv run python -m api.reviewer_tokens add "Alice Example"
    PYTHONPATH=src uv run python -m api.reviewer_tokens add --replace "Alice Example"
    PYTHONPATH=src uv run python -m api.reviewer_tokens remove "Alice Example"
    PYTHONPATH=src uv run python -m api.reviewer_tokens list

This authenticates a reviewer to this application only. It is not single
sign-on, there is no MFA, and anyone who can write the tokens file (or run
the CLI on the server) can issue a token under any name.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import secrets
import sys
import tempfile
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Optional, Sequence

from swarm.review_policy import normalise_identity

ENV_VAR = "REVIEWER_TOKENS_FILE"
HEADER = "X-Reviewer-Token"
FILE_VERSION = 1
HASH_SCHEME = "sha256"
TOKEN_PREFIX = "grcrt_"
# Longest header value that is even looked up (a CLI token is 49 chars).
MAX_TOKEN_LENGTH = 256
MAX_NAME_LENGTH = 200

_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class ReviewerTokensError(Exception):
    """The reviewer tokens file is missing, unreadable or malformed."""


def configured_path() -> Optional[str]:
    """Path from ``REVIEWER_TOKENS_FILE``, or None when the feature is off."""
    value = os.environ.get(ENV_VAR, "").strip()
    return value or None


_TRUTHY = frozenset({"1", "true", "yes", "on"})
_BLOCKED_ENVIRONMENTS = frozenset({"production", "prod", "staging", "stage"})


def reviewer_tokens_mandatory() -> bool:
    """True when reviewer tokens are mandatory for reviewer actions.

    Enabled when REVIEWER_TOKENS_MANDATORY is set, or when ENVIRONMENT is
    production or staging. In DEMO_MODE, returns False so demo walkthroughs
    run out of the box without keys.
    """
    from swarm.demo import demo_mode_enabled

    try:
        if demo_mode_enabled():
            return False
    except Exception:
        # If demo_mode_enabled() raises because of production, tokens remain mandatory.
        pass

    raw = os.environ.get("REVIEWER_TOKENS_MANDATORY", "").strip().lower()
    if raw in _TRUTHY:
        return True
    env = os.environ.get("ENVIRONMENT", "local").strip().lower()
    return env in _BLOCKED_ENVIRONMENTS


def generate_token() -> str:
    """A new random token (256 bits from ``secrets``)."""
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """The stored form of a token: ``sha256:<hex digest>``."""
    return f"{HASH_SCHEME}:" + hashlib.sha256(token.encode("utf-8")).hexdigest()


def _clean_name(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())


@dataclass(frozen=True)
class ReviewerEntry:
    name: str
    token_hash: str
    created_at: str = ""


@dataclass(frozen=True)
class ReviewerRegistry:
    """The parsed tokens file."""

    entries: tuple[ReviewerEntry, ...] = ()

    def authenticate(self, token: Optional[str]) -> Optional[str]:
        """Name whose token this is, or None.

        The digest of ``token`` is compared with every stored digest in
        constant time, without stopping at a match, so the time taken does
        not depend on which (or whether any) entry matched. Only the token is
        looked up, never a name, so a failed attempt says nothing about which
        names exist.
        """
        if not token or len(token) > MAX_TOKEN_LENGTH:
            return None
        provided = hash_token(token).encode("ascii")
        match: Optional[str] = None
        for entry in self.entries:
            if hmac.compare_digest(provided, entry.token_hash.encode("ascii")):
                match = entry.name
        return match

    def find(self, name: str) -> Optional[ReviewerEntry]:
        key = normalise_identity(name)
        return next(
            (e for e in self.entries if normalise_identity(e.name) == key), None
        )


def parse_registry(data: Any) -> ReviewerRegistry:
    """Validate the JSON content of a tokens file.

    Raises ReviewerTokensError for anything unexpected: a wrong version, a
    blank or over-long name, two names that differ only in case or spacing
    (the segregation-of-duties checks could not tell them apart), a digest
    in the wrong form, or one digest under two names.
    """
    if not isinstance(data, dict):
        raise ReviewerTokensError("the tokens file must hold a JSON object")
    if data.get("version") != FILE_VERSION:
        raise ReviewerTokensError(
            f"unsupported tokens file version {data.get('version')!r} "
            f"(expected {FILE_VERSION})"
        )
    raw = data.get("reviewers")
    if not isinstance(raw, list):
        raise ReviewerTokensError("'reviewers' must be a list")
    entries: list[ReviewerEntry] = []
    names: set[str] = set()
    hashes: set[str] = set()
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ReviewerTokensError(f"reviewers[{i}] must be an object")
        name = _clean_name(item.get("name"))
        if not name:
            raise ReviewerTokensError(f"reviewers[{i}] has no name")
        if len(name) > MAX_NAME_LENGTH:
            raise ReviewerTokensError(f"reviewers[{i}] name is too long")
        token_hash = item.get("token_hash")
        if not isinstance(token_hash, str) or not _HASH_RE.match(token_hash):
            raise ReviewerTokensError(
                f"reviewers[{i}] token_hash must be 'sha256:' and 64 hex digits"
            )
        key = normalise_identity(name)
        if key in names:
            raise ReviewerTokensError(f"reviewers[{i}]: duplicate name")
        if token_hash in hashes:
            raise ReviewerTokensError(f"reviewers[{i}]: duplicate token_hash")
        names.add(key)
        hashes.add(token_hash)
        created = item.get("created_at")
        entries.append(
            ReviewerEntry(
                name=name,
                token_hash=token_hash,
                created_at=created if isinstance(created, str) else "",
            )
        )
    return ReviewerRegistry(tuple(entries))


def read_registry(path: str | os.PathLike[str]) -> ReviewerRegistry:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise ReviewerTokensError(
            f"cannot read the reviewer tokens file: {exc.strerror or exc}"
        ) from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ReviewerTokensError(
            f"the reviewer tokens file is not valid JSON (line {exc.lineno})"
        ) from exc
    return parse_registry(data)


_cache_lock = threading.Lock()
_cache: dict[str, tuple[tuple[int, int], ReviewerRegistry]] = {}


def load_registry(path: str) -> ReviewerRegistry:
    """Parsed tokens file, re-read whenever its mtime or size changes.

    A token added or removed with the CLI takes effect on the next request,
    with no restart.
    """
    try:
        st = os.stat(path)
    except OSError as exc:
        raise ReviewerTokensError(
            f"cannot read the reviewer tokens file: {exc.strerror or exc}"
        ) from exc
    stamp = (st.st_mtime_ns, st.st_size)
    with _cache_lock:
        cached = _cache.get(path)
        if cached is not None and cached[0] == stamp:
            return cached[1]
    registry = read_registry(path)
    with _cache_lock:
        _cache[path] = (stamp, registry)
    return registry


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


# ── Failed-attempt limiter ──────────────────────────────────────────────────


class FailureLimiter:
    """Refuse further reviewer-token attempts from a client address after
    ``max_failures`` invalid tokens within ``window`` seconds.

    In-memory and per process. Behind the Compose nginx proxy every browser
    shares the proxy's address, so one client sending bad tokens can make
    everyone wait out the window (ADR-012).
    """

    def __init__(self, max_failures: int = 10, window: float = 60.0) -> None:
        self.max_failures = max_failures
        self.window = window
        self._lock = threading.Lock()
        self._failures: dict[str, deque[float]] = {}

    def _prune(self, key: str, now: float) -> deque[float]:
        q = self._failures.setdefault(key, deque())
        while q and now - q[0] >= self.window:
            q.popleft()
        if not q:
            self._failures.pop(key, None)
        return q

    def retry_after(self, key: str, now: Optional[float] = None) -> Optional[int]:
        """Seconds to wait if ``key`` is blocked, else None."""
        now = time.monotonic() if now is None else now
        with self._lock:
            q = self._prune(key, now)
            if len(q) < self.max_failures:
                return None
            return max(1, int(self.window - (now - q[0])) + 1)

    def record_failure(self, key: str, now: Optional[float] = None) -> None:
        now = time.monotonic() if now is None else now
        with self._lock:
            q = self._prune(key, now)
            q.append(now)
            self._failures[key] = q

    def reset(self) -> None:
        with self._lock:
            self._failures.clear()


limiter = FailureLimiter()


# ── CLI ─────────────────────────────────────────────────────────────────────


def _load_for_edit(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    registry = read_registry(path)
    return [
        {"name": e.name, "token_hash": e.token_hash, "created_at": e.created_at}
        for e in registry.entries
    ]


def _write_atomic(path: Path, reviewers: list[dict[str, str]]) -> None:
    payload = json.dumps(
        {"version": FILE_VERSION, "reviewers": reviewers}, indent=2, sort_keys=True
    )
    parse_registry(json.loads(payload))  # never write a file the API would refuse
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".reviewer-tokens-")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload + "\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def add_reviewer(path: Path, name: str, *, replace: bool = False) -> str:
    """Issue a token for ``name``; store its digest; return the token."""
    name = _clean_name(name)
    if not name:
        raise ReviewerTokensError("the reviewer name must not be blank")
    if len(name) > MAX_NAME_LENGTH:
        raise ReviewerTokensError("the reviewer name is too long")
    reviewers = _load_for_edit(path)
    key = normalise_identity(name)
    existing = [r for r in reviewers if normalise_identity(r["name"]) == key]
    if existing and not replace:
        raise ReviewerTokensError(
            f"'{existing[0]['name']}' already has a token; use --replace to "
            "issue a new one (the old token stops working)"
        )
    reviewers = [r for r in reviewers if normalise_identity(r["name"]) != key]
    token = generate_token()
    reviewers.append(
        {
            "name": name,
            "token_hash": hash_token(token),
            "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
    )
    _write_atomic(path, reviewers)
    return token


def remove_reviewer(path: Path, name: str) -> bool:
    reviewers = _load_for_edit(path)
    key = normalise_identity(name)
    kept = [r for r in reviewers if normalise_identity(r["name"]) != key]
    if len(kept) == len(reviewers):
        return False
    _write_atomic(path, kept)
    return True


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m api.reviewer_tokens",
        description=(
            "Manage per-reviewer tokens. The file stores only a SHA-256 digest "
            "of each token; a new token is printed once and cannot be shown "
            "again."
        ),
    )
    parser.add_argument(
        "--file",
        help=f"tokens file (default: ${ENV_VAR})",
        default=None,
    )
    sub = parser.add_subparsers(dest="command", required=True)
    add = sub.add_parser("add", help="issue a token for a reviewer")
    add.add_argument("name")
    add.add_argument(
        "--replace",
        action="store_true",
        help="replace the reviewer's existing token (the old one stops working)",
    )
    rm = sub.add_parser("remove", help="revoke a reviewer's token")
    rm.add_argument("name")
    sub.add_parser("list", help="list reviewer names (never tokens)")
    args = parser.parse_args(argv)

    file = args.file or configured_path()
    if not file:
        parser.error(f"pass --file or set {ENV_VAR}")
    path = Path(file)
    try:
        if args.command == "add":
            token = add_reviewer(path, args.name, replace=args.replace)
            entry = read_registry(path).find(args.name)
            print(f"Token for {entry.name if entry else args.name}:")
            print(token)
            print(
                "Shown once and not stored: give it to the reviewer over a "
                "private channel. It is sent in the X-Reviewer-Token header.",
                file=sys.stderr,
            )
        elif args.command == "remove":
            if not remove_reviewer(path, args.name):
                print(f"No token for '{args.name}'.", file=sys.stderr)
                return 1
            print(f"Removed the token for '{args.name}'.")
        else:
            registry = read_registry(path) if path.exists() else ReviewerRegistry()
            for e in registry.entries:
                print(f"{e.name}\t{e.created_at}")
    except ReviewerTokensError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
