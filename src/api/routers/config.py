from fastapi import APIRouter

from swarm.demo import demo_mode_enabled

router = APIRouter()


@router.get("/config")
def get_config() -> dict:
    """UI-relevant runtime flags (no secrets)."""
    return {"demo_mode": demo_mode_enabled()}
