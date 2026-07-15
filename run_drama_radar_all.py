from __future__ import annotations

import sys

from radar_common import ROOT
from radar_pipeline import run_pipeline


def main() -> int:
    stages = [
        ("Source collection", [sys.executable, str(ROOT / "collect_sources.py")]),
        ("Transcript download", [sys.executable, str(ROOT / "download_transcripts.py")]),
        ("Transcript index", [sys.executable, str(ROOT / "build_transcript_index.py")]),
        ("OCTOPUSS evidence packets", [sys.executable, str(ROOT / "build_octopuss_packets.py")]),
        ("OCTOPUSS packet validation", [sys.executable, str(ROOT / "validate_octopuss_packets.py")]),
        ("OCTOPUSS compact pre-analysis", [sys.executable, str(ROOT / "octopuss_preanalyse.py")]),
        ("OCTOPUSS candidate filing", [sys.executable, str(ROOT / "octopuss_file_candidates.py")]),
        ("OCTOPUSS reviewed intelligence filing", [sys.executable, str(ROOT / "apply_octopuss_analysis.py")]),
        ("Feed linking", [sys.executable, str(ROOT / "link_transcripts.py")]),
        ("Monthly archive", [sys.executable, str(ROOT / "archive_months.py")]),
        ("Validation", [sys.executable, str(ROOT / "validate_outputs.py")]),
        ("GitHub publishing", [sys.executable, str(ROOT / "git_publish.py")]),
    ]

    return run_pipeline("DRAMA RADAR — COMPLETE RUN", stages)


if __name__ == "__main__":
    raise SystemExit(main())
