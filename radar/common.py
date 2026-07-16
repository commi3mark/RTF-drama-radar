from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
RADAR_PATH = ROOT / "drama-radar.json"
STATS_PATH = ROOT / "radar-stats.json"
SOURCES_PATH = ROOT / "radar" / "sources.json"
ARCHIVE_DIR = ROOT / "archive"
BRAIN_DIR = ROOT / "radar" / "brain"
TRANSCRIPTS_DIR = ROOT / "transcripts" / "archive"
TRANSCRIPT_INDEX_PATH = ROOT / "transcripts" / "transcript-index.json"
TRANSCRIPT_MANIFEST_PATH = ROOT / "transcripts" / "transcript-manifest.json"
RECEIPTS_DIR = ROOT / "radar" / "receipts"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path, default: Any):
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    temp.replace(path)


def stable_id(*parts: str) -> str:
    raw = "|".join(str(part or "").strip() for part in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
