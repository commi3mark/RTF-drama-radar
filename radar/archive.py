from __future__ import annotations
from collections import defaultdict
from datetime import datetime
from common import ARCHIVE_DIR, load_json, save_json, now_iso


def update_archive(items: list[dict]) -> dict:
    grouped = defaultdict(list)
    for item in items:
        try:
            dt = datetime.fromisoformat(str(item.get("published")))
        except Exception:
            continue
        grouped[f"{dt.year:04d}-{dt.month:02d}"].append(item)

    months = []
    for month, current in sorted(grouped.items()):
        path = ARCHIVE_DIR / f"{month}.json"
        existing = load_json(path, [])
        by_id = {x.get("id"): x for x in existing if isinstance(x, dict) and x.get("id")}
        for item in current:
            by_id[item["id"]] = item
        merged = sorted(by_id.values(), key=lambda x: x.get("published") or x.get("discovered_at") or "", reverse=True)
        save_json(path, merged)
        months.append({"month": month, "items": len(merged), "path": path.name})
        print(f"ARCHIVE: {month}: {len(merged)} items")

    # Include historical month files even if no longer represented in live feed.
    known = {m["month"] for m in months}
    for path in sorted(ARCHIVE_DIR.glob("????-??.json")):
        if path.stem not in known:
            months.append({"month": path.stem, "items": len(load_json(path, [])), "path": path.name})
    months.sort(key=lambda x: x["month"])
    index = {
        "updated_at": now_iso(),
        "months": months,
        "month_count": len(months),
        "total_archived_detections": sum(m["items"] for m in months),
    }
    save_json(ARCHIVE_DIR / "archive-index.json", index)
    return index
