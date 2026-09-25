import os
import secrets

from fastapi import Header, HTTPException, status


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
