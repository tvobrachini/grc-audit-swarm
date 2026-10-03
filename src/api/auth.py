import logging
import os
import secrets
from dataclasses import dataclass
from typing import Optional

from fastapi import Header, HTTPException, Request, status

from api import reviewer_tokens
from swarm.review_policy import same_person

logger = logging.getLogger(__name__)


def _configured_token() -> str:
    return os.environ.get("API_AUTH_TOKEN", "")


def require_api_auth(
    authorization: str | None = Header(default=None),
    x_api_token: str | None = Header(default=None),
) -> None:
    """Require a shared API token for non-health API routes."""
    expected = _configured_token()
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="API_AUTH_TOKEN is not configured",
        )

    provided = x_api_token or ""
    if authorization:
        scheme, _, value = authorization.partition(" ")
        if scheme.lower() == "bearer":
            provided = value

    # Compare as bytes: secrets.compare_digest raises TypeError on str inputs
    # that aren't ASCII-only, which would otherwise turn a bad/garbled token
    # into an unhandled 500 instead of a 401.
    if not provided or not secrets.compare_digest(provided.encode(), expected.encode()):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API token",
            headers={"WWW-Authenticate": "Bearer"},
        )


# ── Per-reviewer tokens (opt-in; DECISIONS.md, ADR-012) ─────────────────────

# ``code`` values of a reviewer-token refusal (the JSON body carries both
# ``detail`` and ``code``).
REVIEWER_TOKEN_MISSING = "reviewer_token_missing"  # 401
REVIEWER_TOKEN_INVALID = "reviewer_token_invalid"  # 401
REVIEWER_NAME_MISMATCH = "reviewer_name_mismatch"  # 403
REVIEWER_TOKEN_RATE_LIMITED = "reviewer_token_rate_limited"  # 429
REVIEWER_TOKENS_UNAVAILABLE = "reviewer_tokens_unavailable"  # 503


class ReviewerAuthError(Exception):
    """A reviewer-token refusal; rendered by the handler in ``api.main``."""

    def __init__(
        self,
        status_code: int,
        code: str,
        detail: str,
        headers: Optional[dict[str, str]] = None,
    ) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.code = code
        self.detail = detail
        self.headers = headers or {}


@dataclass(frozen=True)
class ReviewerIdentity:
    """Who is acting, as far as the API can tell.

    ``name`` is the authenticated reviewer name when reviewer tokens are
    configured, else None (the caller's typed name is used, as declared).
    """

    name: Optional[str] = None

    @property
    def authenticated(self) -> bool:
        return self.name is not None

    @property
    def source(self) -> str:
        return "authenticated" if self.authenticated else "declared"

    def resolve(self, typed: Optional[str], field: str) -> tuple[str, str]:
        """(identity to record, identity_source) for a typed name field.

        Declared mode returns the typed name unchanged (the flow rejects a
        blank one with 422, as before). Authenticated mode returns the
        token's name; a typed name that names someone else is refused (403)
        rather than silently replaced, so a mistaken or impersonating client
        finds out. A typed name that matches (ignoring case and spacing) is
        accepted and the token's spelling is recorded.
        """
        if self.name is None:
            return typed or "", "declared"
        if typed and typed.strip() and not same_person(typed, self.name):
            raise ReviewerAuthError(
                status.HTTP_403_FORBIDDEN,
                REVIEWER_NAME_MISMATCH,
                f"{field} does not match the reviewer the X-Reviewer-Token "
                "belongs to. Leave it empty or send that reviewer's name.",
            )
        return self.name, "authenticated"


def _client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def reviewer_identity(
    request: Request,
    x_reviewer_token: Optional[str] = Header(default=None),
) -> ReviewerIdentity:
    """Authenticate the ``X-Reviewer-Token`` header when tokens are configured.

    Not configured (``REVIEWER_TOKENS_FILE`` unset): the header is ignored
    and identities stay declared. Configured: a valid token is required
    (401 missing / invalid, 429 after repeated invalid tokens from one
    address, 503 if the file cannot be used — never a silent fallback to
    declared identities). The token itself is never logged or echoed.
    """
    path = reviewer_tokens.configured_path()
    if path is None:
        if reviewer_tokens.reviewer_tokens_mandatory():
            raise ReviewerAuthError(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                REVIEWER_TOKENS_UNAVAILABLE,
                "Reviewer tokens are mandatory in this deployment, but no "
                "tokens file is configured; see the server log.",
            )
        return ReviewerIdentity()
    try:
        registry = reviewer_tokens.load_registry(path)
    except reviewer_tokens.ReviewerTokensError as exc:
        logger.error("Reviewer tokens are configured but unusable: %s", exc)
        raise ReviewerAuthError(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            REVIEWER_TOKENS_UNAVAILABLE,
            "Reviewer tokens are configured but the tokens file cannot be "
            "used; see the server log.",
        ) from None
    if not x_reviewer_token:
        raise ReviewerAuthError(
            status.HTTP_401_UNAUTHORIZED,
            REVIEWER_TOKEN_MISSING,
            "This action needs your personal reviewer token in the "
            "X-Reviewer-Token header.",
        )
    key = _client_key(request)
    wait = reviewer_tokens.limiter.retry_after(key)
    if wait is not None:
        raise ReviewerAuthError(
            status.HTTP_429_TOO_MANY_REQUESTS,
            REVIEWER_TOKEN_RATE_LIMITED,
            "Too many invalid reviewer tokens; try again later.",
            headers={"Retry-After": str(wait)},
        )
    name = registry.authenticate(x_reviewer_token)
    if name is None:
        reviewer_tokens.limiter.record_failure(key)
        raise ReviewerAuthError(
            status.HTTP_401_UNAUTHORIZED,
            REVIEWER_TOKEN_INVALID,
            "The X-Reviewer-Token is not valid.",
        )
    return ReviewerIdentity(name=name)
