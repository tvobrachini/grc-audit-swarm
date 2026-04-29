from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routers import evidence, phases, sessions

app = FastAPI(title="GRC Audit Swarm API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(sessions.router, prefix="/api/sessions", tags=["sessions"])
app.include_router(phases.router, prefix="/api", tags=["phases"])
app.include_router(evidence.router, prefix="/api/evidence", tags=["evidence"])


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
