import asyncio
import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from api.job_store import get_flow, get_job, get_queue, peek_queue
from swarm.session_manager import get_session

router = APIRouter()


@router.get("/jobs/{job_id}/status")
def job_status(job_id: str) -> dict:
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.get("/stream/{session_id}")
async def stream_events(session_id: str) -> StreamingResponse:
    # Only stream sessions that exist (or already have queued events). Creating
    # a queue for any id a caller names would let one authenticated client grow
    # the queue table without bound.
    q = peek_queue(session_id)
    if q is None:
        if get_flow(session_id) is None and get_session(session_id) is None:
            raise HTTPException(status_code=404, detail="Session not found")
        q = get_queue(session_id)

    async def generator():
        while True:
            try:
                event = await asyncio.wait_for(q.get(), timeout=1.0)
                yield f"data: {json.dumps(event)}\n\n"
            except asyncio.TimeoutError:
                yield ": heartbeat\n\n"

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
