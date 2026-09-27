"""Map working-paper findings to answer-key control areas.

RACM control IDs are written by the model, so they cannot be keyed in
advance. Each finding is mapped to an area deterministically:

1. **Text.** Its RACM control's description and test-of-design steps are
   matched against every area's keyword patterns (case-insensitive); each
   pattern that matches adds its weight once. If the control ID is not in
   the RACM, the finding's own test_conclusion is used instead.
2. **Evidence.** If the finding cites a vault record whose source is one of
   an area's evidence sources, that area gets ``EVIDENCE_BONUS``.
3. The highest-scoring area wins if its score reaches ``MIN_SCORE``. A tie
   for the top score is resolved by the catalogue order and flagged
   ``ambiguous``. Below the threshold the finding is *unmatched*.
4. A matched area that the scenario's key does not list, and that has no
   default expectation, is reported as unmatched (``area_not_in_key``).

Unmatched findings are reported in the results, never silently dropped. The
full mapping record (text used, per-area score and hits) is saved with every
run so each mapping can be audited and challenged.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from evals.answer_key import AnswerKey, Scenario

EVIDENCE_BONUS = 3.0
MIN_SCORE = 2.0


def racm_controls(racm: Any) -> dict[str, dict[str, Any]]:
    """control_id -> {description, tod, risk_id} from a RACM (model or dict)."""
    out: dict[str, dict[str, Any]] = {}
    if racm is None:
        return out
    data = racm if isinstance(racm, dict) else racm.model_dump(mode="json")
    for risk in data.get("risks") or []:
        for c in risk.get("controls") or []:
            tod = " ".join(
                f"{s.get('step_description', '')} {s.get('expected_result', '')}"
                for s in (c.get("testing_procedures") or {}).get("test_of_design") or []
            )
            out[str(c.get("control_id"))] = {
                "description": c.get("description", ""),
                "tod": tod,
                "risk_id": risk.get("risk_id"),
            }
    return out


def map_finding(
    finding: dict[str, Any],
    controls: dict[str, dict[str, Any]],
    vault: dict[str, dict[str, Any]],
    key: AnswerKey,
    scenario: Scenario,
) -> dict[str, Any]:
    control_id = str(finding.get("control_id", ""))
    control = controls.get(control_id)
    if control is not None:
        text = f"{control['description']} {control['tod']}"
        text_source = "racm_control"
    else:
        text = str(finding.get("test_conclusion", ""))
        text_source = "finding_test_conclusion (control not in RACM)"
    lowered = text.lower()

    cited_source: Optional[str] = None
    vault_id = str(finding.get("vault_id_reference") or "")
    if vault_id and vault_id in vault:
        cited_source = vault[vault_id].get("source")

    scores: dict[str, dict[str, Any]] = {}
    for name, area in key.areas.items():
        hits = [k.pattern for k in area.keywords if re.search(k.pattern, lowered)]
        score = sum(k.weight for k in area.keywords if k.pattern in hits)
        evidence = bool(cited_source and cited_source in area.evidence_sources)
        if evidence:
            score += EVIDENCE_BONUS
        if score > 0:
            scores[name] = {"score": score, "hits": hits, "evidence_match": evidence}

    record: dict[str, Any] = {
        "control_id": control_id,
        "text_source": text_source,
        "text": text[:500],
        "cited_evidence_source": cited_source,
        "scores": scores,
        "area": None,
        "ambiguous": False,
        "unmatched_reason": None,
    }
    if not scores:
        record["unmatched_reason"] = "no_keyword_or_evidence_match"
        return record
    top = max(v["score"] for v in scores.values())
    if top < MIN_SCORE:
        record["unmatched_reason"] = f"best score {top} below {MIN_SCORE}"
        return record
    best = [name for name in key.areas if scores.get(name, {}).get("score") == top]
    area = best[0]
    record["ambiguous"] = len(best) > 1
    if len(best) > 1:
        record["tied_areas"] = best
    if key.expectation(scenario, area) is None:
        record["unmatched_reason"] = f"area_not_in_key ({area})"
        record["candidate_area"] = area
        return record
    record["area"] = area
    return record
