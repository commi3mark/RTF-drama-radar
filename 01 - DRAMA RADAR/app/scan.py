from __future__ import annotations

import calendar
import time
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse
import feedparser

from common import SOURCES_PATH, load_json, now_iso, stable_id
from console import progress


def _iso(value):
    if not value:
        return None
    try:
        return datetime.fromtimestamp(calendar.timegm(value), tz=timezone.utc).isoformat()
    except Exception:
        return None


def _youtube_id(entry: dict) -> str | None:
    if entry.get("yt_videoid"):
        return str(entry["yt_videoid"])
    query = parse_qs(urlparse(str(entry.get("link", ""))).query)
    return query.get("v", [None])[0]


def _normalise(source: dict, entry: dict) -> dict:
    platform = str(source.get("platform", "Unknown"))
    link = str(entry.get("link", "")).strip()
    video_id = _youtube_id(entry) if platform == "YouTube" else None
    unique = video_id or entry.get("id") or link
    return {
        "id": stable_id(source.get("name", ""), platform, str(unique)),
        "source": source.get("name"),
        "platform": platform,
        "type": "video" if video_id else "article",
        "title": str(entry.get("title", "")).strip(),
        "url": link,
        "published": _iso(entry.get("published_parsed") or entry.get("updated_parsed")),
        "description": str(entry.get("summary", "") or entry.get("description", "")).strip(),
        "youtube_id": video_id,
        "transcript_status": "pending" if video_id else "not_applicable",
        "transcript_path": None,
        "transcript_url": None,
        "discovered_at": now_iso(),
    }


def scan_sources() -> tuple[list[dict], dict]:
    sources = load_json(SOURCES_PATH, [])
    enabled_sources = [s for s in sources if not s.get("disabled")]
    detections: list[dict] = []
    results = {
        "configured": len(sources),
        "enabled": len(enabled_sources),
        "successful": 0,
        "failed": 0,
        "items_read": 0,
        "failures": [],
    }
    started = time.time()
    print("\nCURRENT SWEEP")

    for number, source in enumerate(enabled_sources, 1):
        name = str(source.get("name", "Unnamed"))
        platform = str(source.get("platform", "Unknown"))
        progress("Checking source", number - 1, len(enabled_sources), f"{platform} • {name}", started)
        try:
            parsed = feedparser.parse(source["feed"])
            if getattr(parsed, "bozo", False) and not parsed.entries:
                raise RuntimeError(str(getattr(parsed, "bozo_exception", "Feed parse error")))
            for entry in parsed.entries:
                results["items_read"] += 1
                detections.append(_normalise(source, entry))
            results["successful"] += 1
            progress("Source complete", number, len(enabled_sources), f"{name} • {len(parsed.entries)} returns", started)
        except Exception as exc:
            results["failed"] += 1
            message = f"{type(exc).__name__}: {exc}"
            results["failures"].append({"source": name, "error": message})
            progress("Source failed", number, len(enabled_sources), f"{name} • {message}", started)

    return detections, results
