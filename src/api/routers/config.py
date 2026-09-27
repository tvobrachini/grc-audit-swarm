from typing import Optional

from fastapi import APIRouter, Depends

from api import reviewer_tokens
from api.auth import ReviewerIdentity, reviewer_identity
from swarm.demo import demo_mode_enabled

router = APIRouter()


@router.get("/config")
def get_config() -> dict:
    """UI-relevant runtime flags (no secrets).

    ``reviewer_tokens``: whether reviewer actions need an ``X-Reviewer-Token``
    (``REVIEWER_TOKENS_FILE`` is set; ADR-012).
    """
    return {
        "demo_mode": demo_mode_enabled(),
        "reviewer_tokens": reviewer_tokens.configured_path() is not None,
    }


@router.get("/reviewer")
def get_reviewer(
    who: ReviewerIdentity = Depends(reviewer_identity),
) -> dict[str, Optional[str]]:
    """Who the ``X-Reviewer-Token`` belongs to, so a client can check a token
    before using it.

    Without reviewer tokens: ``{"name": null, "identity_source": "declared"}``.
    With them: the token's name and ``"authenticated"``, or the same 401 / 429
    / 503 refusals as any reviewer action.
    """
    return {"name": who.name, "identity_source": who.source}
