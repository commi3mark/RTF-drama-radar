from __future__ import annotations

import argparse
import sys
from datetime import datetime

from radar_common import ROOT, settings
from radar_pipeline import run_pipeline


def inside_scheduled_window() -> bool:
    cfg = settings()
    allowed_hours = [
        int(hour)
        for hour in cfg.get(
            "scheduled_transcript_hours_local",
            [2, 8, 14, 20],
        )
    ]
    tolerance = int(
        cfg.get("scheduled_transcript_tolerance_minutes", 45)
    )

    now = datetime.now()

    for hour in allowed_hours:
        scheduled_minutes = hour * 60
        current_minutes = now.hour * 60 + now.minute

        if abs(current_minutes - scheduled_minutes) <= tolerance:
            return True

    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--scheduled",
        action="store_true",
        help="Only run inside configured local transcript windows.",
    )
    parser.add_argument(
        "--ignore-cooldown",
        action="store_true",
        help="Force one transcript attempt despite a global cooldown.",
    )
    args = parser.parse_args()

    if args.scheduled and not inside_scheduled_window():
        cfg = settings()
        hours = cfg.get(
            "scheduled_transcript_hours_local",
            [2, 8, 14, 20],
        )
        print(
            "Transcript run skipped: outside configured schedule. "
            f"Configured local hours: {hours}"
        )
        return 0

    transcript_command = [
        sys.executable,
        str(ROOT / "download_transcripts.py"),
    ]

    if args.ignore_cooldown:
        transcript_command.append("--ignore-cooldown")

    stages = [
        ("Transcript download", transcript_command),
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

    return run_pipeline(
        "DRAMA RADAR — TRANSCRIPT RUN",
        stages,
    )


if __name__ == "__main__":
    raise SystemExit(main())
