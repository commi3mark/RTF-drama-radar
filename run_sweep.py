from __future__ import annotations

import sys

from radar_pipeline import run_pipeline
from radar_common import ROOT


STAGES = [
    (
        "Source collection",
        [sys.executable, str(ROOT / "collect_sources.py")],
    ),
    (
        "Transcript index",
        [sys.executable, str(ROOT / "build_transcript_index.py")],
    ),
    (
        "Feed linking",
        [sys.executable, str(ROOT / "link_transcripts.py")],
    ),
    (
        "Monthly archive",
        [sys.executable, str(ROOT / "archive_months.py")],
    ),
    (
        "Validation",
        [sys.executable, str(ROOT / "validate_outputs.py")],
    ),
]


if __name__ == "__main__":
    raise SystemExit(
        run_pipeline(
            "DRAMA RADAR — SOURCE SWEEP",
            STAGES,
        )
    )
