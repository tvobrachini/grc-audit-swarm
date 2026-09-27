"""Cross-site request refusal for state-changing API calls.

In the compose deployment the frontend's nginx injects the API bearer token
into every ``/api/`` request it proxies, so the token alone does not show
that a request came from the app: any web page the user visits could make
their browser POST to the proxy. Multipart form POSTs (the Prowler import
and the scope-document upload) are "simple" requests that need no CORS
preflight, so CORS alone does not stop them from being *sent*.

This check closes that gap. For ``POST`` / ``PUT`` / ``PATCH`` / ``DELETE``:

* an ``Origin`` header, when present, must be in the configured allow-list
  (``CORS_ALLOWED_ORIGINS``, the same list CORS uses), or name the same
  host the request was sent to, or come with ``Sec-Fetch-Site:
  same-origin``; ``Origin: null`` (sandboxed frames, local files) is refused;
* with no ``Origin``, a ``Sec-Fetch-Site`` of ``cross-site`` or
  ``same-site`` is refused (``same-origin`` and ``none`` are allowed);
* a request with neither header (curl, scripts, server-to-server clients,
  the test client) is allowed: browsers always send at least one of them on
  these methods, so their absence means the caller is not a browser page.

A refusal is ``403`` with ``{"detail": ..., "code": "origin_not_allowed"}``.
``GET`` / ``HEAD`` / ``OPTIONS`` are not checked (they change nothing, and
CORS still governs whether another origin may read a response).
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from urllib.parse import urlsplit

ORIGIN_NOT_ALLOWED = "origin_not_allowed"

STATE_CHANGING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

_DEFAULT_ALLOWED_ORIGINS = "http://localhost:5173"


def allowed_origins_from_env() -> list[str]:
    """``CORS_ALLOWED_ORIGINS`` as a list (``*`` is ignored: the API is
    credentialed, and a wildcard would defeat this check)."""
    raw = os.environ.get("CORS_ALLOWED_ORIGINS", _DEFAULT_ALLOWED_ORIGINS)
    return [o.strip() for o in raw.split(",") if o.strip() and o.strip() != "*"]


def _normalise_origin(origin: str) -> str:
    return origin.strip().rstrip("/").lower()


def is_request_allowed(
    method: str, headers: Mapping[str, str], allowed_origins: Iterable[str]
) -> bool:
    """Whether a request may proceed (see the module docstring)."""
    if method.upper() not in STATE_CHANGING_METHODS:
        return True
    fetch_site = (headers.get("sec-fetch-site") or "").strip().lower()
    origin = headers.get("origin")
    if origin is not None:
        normalised = _normalise_origin(origin)
        if normalised in {_normalise_origin(o) for o in allowed_origins}:
            return True
        if normalised == "null" or not normalised:
            return False
        if fetch_site == "same-origin":
            return True
        host = (headers.get("host") or "").strip().lower()
        try:
            origin_host = urlsplit(normalised).netloc
        except ValueError:
            return False
        return bool(host) and origin_host == host
    return fetch_site in ("", "same-origin", "none")
