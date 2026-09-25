import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.auth import require_api_auth
from api.executor import init_executor, shutdown_executor
from api.job_store import set_main_loop
from api.routers import config, evidence, exports, phases, sessions
from swarm.demo import demo_mode_enabled

logger = logging.getLogger(__name__)


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
