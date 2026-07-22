from __future__ import annotations
import json
import os
from datetime import datetime, timezone
from pathlib import Path
GRABBER_ROOT = Path(__file__).resolve().parents[1]
SYSTEM_ROOT = GRABBER_ROOT.parent
ROOT = SYSTEM_ROOT
TRANSCRIPTS_DIR = GRABBER_ROOT / "transcripts"
TRANSCRIPT_INDEX_PATH = TRANSCRIPTS_DIR / "transcript-index.json"
TRANSCRIPT_MANIFEST_PATH = TRANSCRIPTS_DIR / "transcript-manifest.json"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def save_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def build() -> dict:
    rows = []
    for path in TRANSCRIPTS_DIR.rglob("*.json"):
        if path.name.endswith(".intelligence.json") or path.name in {"transcript-index.json", "transcript-manifest.json"}:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        vid = data.get("video_id") or data.get("youtube_id")
        if not vid:
            continue
        rel = path.relative_to(ROOT).as_posix()
        intel_json = path.with_name(path.stem + ".intelligence.json")
        intel_txt = path.with_name(path.stem + ".intelligence.txt")
        transcript_txt = path.with_name(path.stem + ".transcript.txt")
        rows.append({
            "video_id": str(vid), "title": data.get("title"), "source": data.get("source"),
            "published": data.get("published"), "downloaded_at": data.get("downloaded_at"),
            "segment_count": data.get("segment_count") or len(data.get("segments", [])), "path": rel,
            "intelligence_json": intel_json.relative_to(ROOT).as_posix() if intel_json.exists() else None,
            "intelligence_txt": intel_txt.relative_to(ROOT).as_posix() if intel_txt.exists() else None,
            "transcript_txt": transcript_txt.relative_to(ROOT).as_posix() if transcript_txt.exists() else None,
        })
    rows.sort(key=lambda x: x.get("downloaded_at") or x.get("published") or "", reverse=True)
    index = {"updated_at": now_iso(), "count": len(rows), "transcripts": rows}
    save_json(TRANSCRIPT_INDEX_PATH, index)
    save_json(TRANSCRIPT_MANIFEST_PATH, index)
    return index

if __name__ == "__main__":
    result = build(); print(f"Transcript index contains {result['count']} files.")
