import asyncio
import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from api.job_store import get_job, get_queue

router = APIRouter()


@router.get("/jobs/{job_id}/status")
def job_status(job_id: str) -> dict:
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.get("/stream/{session_id}")
async def stream_events(session_id: str) -> StreamingResponse:
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
