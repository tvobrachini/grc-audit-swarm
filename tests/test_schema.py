"""
Tests for src/swarm/schema.py — Pydantic schema structural contracts.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from swarm.schema import (
    OSCAL_SAR_ImportAP,
    OSCAL_SAR_Metadata,
    OSCAL_SAR_Result,
    OSCAL_SAR_Schema,
)


def _make_metadata():
    return OSCAL_SAR_Metadata(
        title="Test Report", last_modified="2026-01-01T00:00:00Z", version="1.0"
    )


class TestOscalSarSchema:
    def test_import_ap_is_required(self):
        with pytest.raises(Exception):
            OSCAL_SAR_Schema(metadata=_make_metadata(), results=[])

    def test_import_ap_populates_correctly(self):
        schema = OSCAL_SAR_Schema(
            metadata=_make_metadata(),
            import_ap=OSCAL_SAR_ImportAP(href="urn:audit:fintech-s3-2026"),
            results=[],
        )
        assert schema.import_ap.href == "urn:audit:fintech-s3-2026"
        assert schema.import_ap.remarks is None
