import logging
from fastapi import APIRouter, HTTPException
from api.models import VerifyEvidenceRequest

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/verify")
def verify_evidence(req: VerifyEvidenceRequest) -> dict:
    try:
        from swarm.evidence import EvidenceAssuranceProtocol

        result = EvidenceAssuranceProtocol.verify_exact_quote(
            req.vault_id, req.exact_quote
        )
        return {"vault_id": req.vault_id, "verified": result}
    except Exception:
        logger.exception("Evidence verification failed")
        raise HTTPException(
            status_code=500, detail="Internal server error during evidence verification"
        )
