from __future__ import annotations

from common import (
    RADAR_PATH,
    ARCHIVE_DIR,
    SYSTEM_ROOT,
    TRANSCRIPT_INDEX_PATH,
    load_json,
)


def validate(radar: list[dict] | None = None) -> list[str]:
    """Validate live Radar output and linked archive/transcript files.

    ``radar`` may be supplied by the current run so validation checks the exact
    in-memory feed that was just written. When omitted, the live feed is loaded
    from ``drama-radar.json``. Supporting both forms keeps manual validation and
    the main sweep runner compatible.
    """
    errors: list[str] = []

    if radar is None:
        radar = load_json(RADAR_PATH, None)

    if not isinstance(radar, list):
        return ["drama-radar.json is not a JSON list"]

    ids = [item.get("id") for item in radar if isinstance(item, dict)]
    populated_ids = [item_id for item_id in ids if item_id]
    if len(populated_ids) != len(set(populated_ids)):
        errors.append("Duplicate detection IDs exist in drama-radar.json")

    archive_index = load_json(ARCHIVE_DIR / "archive-index.json", {})
    if not isinstance(archive_index, dict):
        errors.append("archive-index.json is not a JSON object")
    else:
        for row in archive_index.get("months", []):
            if not isinstance(row, dict):
                errors.append("archive-index.json contains an invalid month entry")
                continue
            path = row.get("path")
            if path and not (ARCHIVE_DIR / str(path)).exists():
                errors.append(f"Archive file missing: {path}")

    index = load_json(TRANSCRIPT_INDEX_PATH, {"transcripts": []})
    if not isinstance(index, dict):
        errors.append("transcript-index.json is not a JSON object")
    else:
        for row in index.get("transcripts", []):
            if not isinstance(row, dict):
                errors.append("transcript-index.json contains an invalid entry")
                continue
            path = row.get("path")
            # Transcript index paths are stored relative to the shared system
            # root, e.g. "02 - TRANSCRIPT GRABBER/transcripts/...".  Resolving
            # them from RADAR_PATH.parent incorrectly inserts
            # "01 - DRAMA RADAR/output" and makes every valid transcript look
            # missing.
            if path and not (SYSTEM_ROOT / str(path)).exists():
                errors.append(f"Transcript index path missing: {path}")

    return errors
