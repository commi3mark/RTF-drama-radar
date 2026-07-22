from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    import yt_dlp
except ImportError:
    yt_dlp = None

from adaptive_pacing import clean_attempt, wait_before_video
from candidate_queue import CHANNEL_CACHE, config, load_json


def save(value: dict) -> None:
    CHANNEL_CACHE.parent.mkdir(parents=True, exist_ok=True)
    temporary = CHANNEL_CACHE.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, CHANNEL_CACHE)


def parsed(value):
    try:
        result = datetime.fromisoformat(str(value))
        return result if result.tzinfo else result.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def channel_entries(channel_url: str, label: str) -> list[dict]:
    if yt_dlp is None:
        raise RuntimeError("yt-dlp is required to inventory YouTube channels.")
    output, seen = [], set()
    for tab in ("streams", "videos"):
        wait_before_video(f"refreshing {label} {tab}")
        with yt_dlp.YoutubeDL({
            "quiet": True,
            "no_warnings": True,
            "extract_flat": True,
            "playlistend": 10000,
            "socket_timeout": 30,
        }) as ydl:
            info = ydl.extract_info(channel_url.rstrip("/") + "/" + tab, download=False)
        clean_attempt()
        for entry in (info or {}).get("entries") or []:
            video = str(entry.get("id") or "")
            if len(video) != 11 or video in seen:
                continue
            seen.add(video)
            timestamp = entry.get("timestamp") or entry.get("release_timestamp")
            published = None
            try:
                if timestamp:
                    published = datetime.fromtimestamp(float(timestamp), tz=timezone.utc).isoformat()
            except Exception:
                pass
            output.append({
                "video_id": video,
                "url": entry.get("url") if str(entry.get("url") or "").startswith("http") else f"https://www.youtube.com/watch?v={video}",
                "title": entry.get("title") or f"{label} [{video}]",
                "source": label,
                "published": published,
            })
    output.sort(key=lambda item: item.get("published") or "", reverse=True)
    return output


def refresh(force: bool = False) -> None:
    cfg = config()
    cache = load_json(CHANNEL_CACHE, {})
    hours = int(cfg.get("channel_refresh_hours", 24))
    definitions = (
        ("piper", cfg.get("piper_channel"), "Adventures of Piper"),
        ("commi3_mark", cfg.get("commi3_mark_channel"), "Commi3 Mark"),
    )
    changed = False
    for key, url, label in definitions:
        if not url:
            continue
        checked = parsed((cache.get(key) or {}).get("checked_at"))
        if not force and checked and datetime.now(timezone.utc) - checked < timedelta(hours=hours):
            continue
        print(f"Refreshing {label} channel inventory...")
        try:
            items = channel_entries(str(url), label)
            cache[key] = {"checked_at": datetime.now(timezone.utc).isoformat(), "url": url, "items": items}
            changed = True
            print(f"  Found {len(items)} distinct livestreams/videos.")
        except Exception as exc:
            print(f"  Inventory refresh deferred: {type(exc).__name__}: {exc}")
    if changed:
        save(cache)
