from __future__ import annotations
from collections import defaultdict
from datetime import datetime
from radar_common import load_json, save_json, path_for

def main() -> int:
    radar = load_json(path_for("radar"), [])
    grouped = defaultdict(list)

    for item in radar:
        published = item.get("published")
        if not published:
            continue
        try:
            dt = datetime.fromisoformat(published)
        except Exception:
            continue
        grouped[f"{dt.year:04d}-{dt.month:02d}"].append(item)

    archive_root = path_for("archive")
    total = 0
    for month, items in sorted(grouped.items()):
        path = archive_root / f"{month}.json"
        existing = load_json(path, [])
        by_id = {x["id"]: x for x in existing if isinstance(x, dict) and x.get("id")}
        for item in items:
            by_id[item["id"]] = item
        merged = list(by_id.values())
        merged.sort(key=lambda x: x.get("published") or "", reverse=True)
        save_json(path, merged)
        print(f"ARCHIVE: {month}: {len(merged)} items.")
        total += len(items)

    print(f"Monthly archive update complete. Processed {total} live items.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
