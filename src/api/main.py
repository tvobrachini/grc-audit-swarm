from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.auth import require_api_auth
import asyncio
from api.executor import init_executor, shutdown_executor
from api.job_store import set_main_loop
from api.routers import evidence, phases, sessions


@asynccontextmanager
async def lifespan(app: FastAPI):
    set_main_loop(asyncio.get_running_loop())
    init_executor()
    yield
    shutdown_executor()


app = FastAPI(title="GRC Audit Swarm API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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
    phases.router, prefix="/api", tags=["phases"], dependencies=_api_auth
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
