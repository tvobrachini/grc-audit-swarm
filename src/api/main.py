import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api import reviewer_tokens
from api.auth import ReviewerAuthError, require_api_auth
from api.executor import init_executor, shutdown_executor
from api.job_store import set_main_loop
from api.origin_check import (
    ORIGIN_NOT_ALLOWED,
    allowed_origins_from_env,
    is_request_allowed,
)
from api.routers import config, evidence, exports, imports, phases, sessions
from api.scope_document import MAX_UPLOAD_BYTES
from swarm.demo import demo_mode_enabled
from swarm.tools.findings_checks import MAX_PROWLER_FILE_BYTES

logger = logging.getLogger(__name__)

# Document limit plus room for the multipart envelope and form fields.
_MAX_UPLOAD_REQUEST_BYTES = MAX_UPLOAD_BYTES + 256 * 1024
_MAX_PROWLER_UPLOAD_REQUEST_BYTES = MAX_PROWLER_FILE_BYTES + 256 * 1024


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Refuse to start with DEMO_MODE in production/staging: demo_mode_enabled()
    # raises DemoModeNotAllowedError there, which aborts startup.
    if demo_mode_enabled():
        logger.warning(
            "DEMO_MODE is on: phase crews are replaced with fixed demo artifacts."
        )
    if reviewer_tokens.reviewer_tokens_mandatory():
        path = reviewer_tokens.configured_path()
        if path is None:
            raise reviewer_tokens.ReviewerTokensError(
                "REVIEWER_TOKENS_FILE must be configured when reviewer tokens are "
                "mandatory (ENVIRONMENT is production/staging or "
                "REVIEWER_TOKENS_MANDATORY is set). Reviewer identity cannot be "
                "unauthenticated in this environment."
            )
    set_main_loop(asyncio.get_running_loop())
    init_executor()
    yield
    shutdown_executor()


app = FastAPI(title="GRC Audit Swarm API", version="0.1.0", lifespan=lifespan)

# The API is credentialed (bearer token), so a wildcard origin is invalid per
# the CORS spec when allow_credentials=True — browsers reject that combination.
# CORS_ALLOWED_ORIGINS is a comma-separated allow-list; the default covers the
# Vite dev server (the compose frontend is same-origin via its nginx proxy).
# The same allow-list gates state-changing requests by Origin (see
# api.origin_check and the middleware below).
_allowed_origins = allowed_origins_from_env()

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def limit_upload_size(request: Request, call_next):
    """Reject oversized scope-document uploads before the body is parsed.

    The endpoint re-checks the size it actually reads; this only stops a
    declared-oversized body from being spooled at all (the nginx proxy also
    caps request bodies in the compose deployment).
    """
    if request.method == "POST" and request.url.path.endswith("/with-document"):
        declared = request.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > _MAX_UPLOAD_REQUEST_BYTES:
            return JSONResponse(
                status_code=413, content={"detail": "Upload is too large."}
            )
    if request.method == "POST" and request.url.path.endswith("/imports/prowler"):
        declared = request.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > _MAX_PROWLER_UPLOAD_REQUEST_BYTES:
            return JSONResponse(
                status_code=413, content={"detail": "Upload is too large."}
            )
    return await call_next(request)


@app.middleware("http")
async def refuse_cross_site_writes(request: Request, call_next):
    """403 for a state-changing request from a browser page on another origin.

    Registered after the other middleware so it runs first. The compose
    nginx proxy adds the API token to every request it forwards, so without
    this a page on any site could make a visitor's browser post uploads or
    deletes through it (see api.origin_check).
    """
    if not is_request_allowed(request.method, request.headers, _allowed_origins):
        logger.warning(
            "Refused cross-site %s %s (Origin=%r, Sec-Fetch-Site=%r)",
            request.method,
            request.url.path,
            (request.headers.get("origin") or "")[:200],
            (request.headers.get("sec-fetch-site") or "")[:40],
        )
        return JSONResponse(
            status_code=403,
            content={
                "detail": (
                    "Cross-site request refused: this origin is not allowed "
                    "to change data (see CORS_ALLOWED_ORIGINS)."
                ),
                "code": ORIGIN_NOT_ALLOWED,
            },
        )
    return await call_next(request)


@app.exception_handler(ReviewerAuthError)
async def reviewer_auth_error(request: Request, exc: ReviewerAuthError):
    """Reviewer-token refusals: ``detail`` (a string, like every other error)
    plus a machine-readable ``code`` (see ``api.auth``)."""
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail, "code": exc.code},
        headers=exc.headers,
    )


_api_auth = [Depends(require_api_auth)]

app.include_router(
    sessions.router,
    prefix="/api/sessions",
    tags=["sessions"],
    dependencies=_api_auth,
)
app.include_router(
    exports.router,
    prefix="/api/sessions",
    tags=["exports"],
    dependencies=_api_auth,
)
app.include_router(
    phases.router, prefix="/api", tags=["phases"], dependencies=_api_auth
)
app.include_router(
    config.router, prefix="/api", tags=["config"], dependencies=_api_auth
)
app.include_router(
    evidence.router,
    prefix="/api/evidence",
    tags=["evidence"],
    dependencies=_api_auth,
)
app.include_router(
    imports.router,
    prefix="/api/sessions",
    tags=["imports"],
    dependencies=_api_auth,
)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
