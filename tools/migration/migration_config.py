"""Explicit destination configuration for administrative migration scripts."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from config import DATA_ROOT, EVIDENCE_ROOT

PILOT = DATA_ROOT
HISTORICAL_ROOT = DATA_ROOT / "historical_samples" / "2026-09-27"
REPORT_ROOT = Path(os.environ.get("ACQ_REPORT_ROOT") or EVIDENCE_ROOT.parent).expanduser().resolve()
PROJECT = os.environ.get("SUPABASE_PROJECT_REF", "")
if PROJECT and (len(PROJECT) != 20 or not PROJECT.isalnum()):
    raise ValueError("Invalid destination SUPABASE_PROJECT_REF")


def require_project():
    if not PROJECT:
        raise ValueError("Set SUPABASE_PROJECT_REF to the explicitly approved destination")
    return PROJECT
