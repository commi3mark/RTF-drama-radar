from __future__ import annotations
from radar_common import load_json, save_json, path_for, now_iso

def main() -> int:
    rows = []
    transcript_root = path_for("transcripts")
    for path in sorted(transcript_root.rglob("*.json")):
        try:
            data = load_json(path, {})
            rows.append({
                "video_id": data.get("video_id") or data.get("youtube_id"),
                "title": data.get("title"),
                "source": data.get("source"),
                "published": data.get("published"),
                "downloaded_at": data.get("downloaded_at"),
                "segment_count": data.get("segment_count") or len(data.get("segments", [])),
                "path": str(path.relative_to(transcript_root.parent)).replace("\\", "/")
            })
        except Exception as exc:
            print(f"INDEX WARNING: {path}: {exc}")
    rows.sort(key=lambda x: x.get("published") or "", reverse=True)
    save_json(path_for("transcript_index"), {
        "generated_at": now_iso(),
        "count": len(rows),
        "transcripts": rows
    })
    print(f"Transcript index contains {len(rows)} files.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
