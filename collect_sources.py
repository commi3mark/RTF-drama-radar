from __future__ import annotations
import calendar
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse
import feedparser

from radar_common import ROOT, load_json, save_json, settings, path_for, stable_id, now_iso

def struct_time_to_iso(value) -> str | None:
    if not value:
        return None
    try:
        return datetime.fromtimestamp(calendar.timegm(value), tz=timezone.utc).isoformat()
    except Exception:
        return None

def youtube_id(entry: dict) -> str | None:
    if entry.get("yt_videoid"):
        return str(entry["yt_videoid"])
    query = parse_qs(urlparse(str(entry.get("link", ""))).query)
    return query.get("v", [None])[0]

def normalise(source: dict, entry: dict) -> dict:
    published = struct_time_to_iso(entry.get("published_parsed") or entry.get("updated_parsed"))
    link = str(entry.get("link", "")).strip()
    vid = youtube_id(entry) if source.get("platform") == "YouTube" else None
    unique = vid or entry.get("id") or link
    return {
        "id": stable_id(source.get("name",""), source.get("platform",""), str(unique)),
        "source": source.get("name"),
        "platform": source.get("platform"),
        "type": "video" if source.get("platform") == "YouTube" else "article",
        "title": str(entry.get("title", "")).strip(),
        "url": link,
        "published": published,
        "description": str(entry.get("summary", "") or entry.get("description", "")).strip(),
        "youtube_id": vid,
        "transcript_status": "pending" if vid else "not_applicable",
        "transcript_path": None,
        "transcript_url": None,
        "discovered_at": now_iso()
    }

def main() -> int:
    cfg = settings()
    sources = load_json(ROOT / "config" / "sources.json", [])
    old = load_json(path_for("radar"), [])
    by_id = {item["id"]: item for item in old if isinstance(item, dict) and item.get("id")}
    cutoff = datetime.now(timezone.utc) - timedelta(days=int(cfg.get("rolling_days", 30)))
    success = failed = read = added = 0

    print(f"Loaded {len(sources)} configured sources.")
    for source in sources:
        if source.get("disabled"):
            print(f"SOURCE DISABLED: {source.get('name')} — {source.get('note','')}")
            continue
        name = source.get("name", "Unnamed")
        try:
            parsed = feedparser.parse(source["feed"])
            if getattr(parsed, "bozo", False) and not parsed.entries:
                raise RuntimeError(str(getattr(parsed, "bozo_exception", "Feed parse error")))
            retained = 0
            for entry in parsed.entries:
                read += 1
                item = normalise(source, entry)
                if item["published"]:
                    try:
                        if datetime.fromisoformat(item["published"]) < cutoff:
                            continue
                    except ValueError:
                        pass
                retained += 1
                if item["id"] not in by_id:
                    by_id[item["id"]] = item
                    added += 1
                else:
                    old_item = by_id[item["id"]]
                    preserved = {
                        "discovered_at": old_item.get("discovered_at"),
                        "transcript_status": old_item.get("transcript_status"),
                        "transcript_path": old_item.get("transcript_path"),
                        "transcript_url": old_item.get("transcript_url"),
                    }
                    old_item.update(item)
                    old_item.update({k:v for k,v in preserved.items() if v is not None})
            success += 1
            print(f"SOURCE OK: {name}: {len(parsed.entries)} items ({retained} retained).")
        except Exception as exc:
            failed += 1
            print(f"SOURCE FAILED: {name}: {type(exc).__name__}: {exc}")

    items = list(by_id.values())
    items.sort(key=lambda x: x.get("published") or x.get("discovered_at") or "", reverse=True)
    save_json(path_for("radar"), items)
    save_json(path_for("item_index"), {
        i["id"]: {
            "source": i.get("source"),
            "platform": i.get("platform"),
            "type": i.get("type"),
            "title": i.get("title"),
            "url": i.get("url"),
            "published": i.get("published"),
            "youtube_id": i.get("youtube_id"),
            "transcript_status": i.get("transcript_status"),
            "transcript_path": i.get("transcript_path"),
            "transcript_url": i.get("transcript_url")
        } for i in items
    })

    print()
    print("Source collection complete.")
    print(f"Sources successful: {success}")
    print(f"Sources failed:     {failed}")
    print(f"Feed items read:    {read}")
    print(f"New radar items:    {added}")
    print(f"Radar items saved:  {len(items)}")
    return 0 if failed == 0 else 2

if __name__ == "__main__":
    raise SystemExit(main())
