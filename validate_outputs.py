from __future__ import annotations
from radar_common import load_json, path_for

def main() -> int:
    radar = load_json(path_for("radar"), None)
    index = load_json(path_for("transcript_index"), None)
    if not isinstance(radar, list):
        raise SystemExit("drama-radar.json is not a list")
    if not isinstance(index, dict) or "transcripts" not in index:
        raise SystemExit("transcript-index.json is invalid")
    ids = [x.get("id") for x in radar if isinstance(x, dict)]
    if len(ids) != len(set(ids)):
        raise SystemExit("Duplicate Radar item IDs detected")
    print(f"Validation passed: {len(radar)} Radar items, {index.get('count',0)} transcripts.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
