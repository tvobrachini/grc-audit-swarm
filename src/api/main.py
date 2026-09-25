import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api.auth import require_api_auth
from api.executor import init_executor, shutdown_executor
from api.job_store import set_main_loop
from api.routers import config, evidence, exports, phases, sessions
from api.scope_document import MAX_UPLOAD_BYTES
from swarm.demo import demo_mode_enabled

logger = logging.getLogger(__name__)

# Document limit plus room for the multipart envelope and form fields.
_MAX_UPLOAD_REQUEST_BYTES = MAX_UPLOAD_BYTES + 256 * 1024


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Refuse to start with DEMO_MODE in production/staging: demo_mode_enabled()
    # raises DemoModeNotAllowedError there, which aborts startup.
    if demo_mode_enabled():
        logger.warning(
            "DEMO_MODE is on: phase crews are replaced with fixed demo artifacts."
        )
    set_main_loop(asyncio.get_running_loop())
    init_executor()
    yield
    shutdown_executor()


app = FastAPI(title="GRC Audit Swarm API", version="0.1.0", lifespan=lifespan)

# The API is credentialed (bearer token), so a wildcard origin is invalid per
# the CORS spec when allow_credentials=True — browsers reject that combination.
# CORS_ALLOWED_ORIGINS is a comma-separated allow-list; defaults cover local dev.
_allowed_origins = [
    origin.strip()
    for origin in os.environ.get(
        "CORS_ALLOWED_ORIGINS", "http://localhost:5173,http://localhost:8502"
    ).split(",")
    if origin.strip() and origin.strip() != "*"
]

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
    return await call_next(request)


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


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
