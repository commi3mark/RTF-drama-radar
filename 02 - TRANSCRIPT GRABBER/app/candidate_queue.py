from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from youtube_retrieval import youtube_id

ROOT = Path(__file__).resolve().parents[1]
SYSTEM_ROOT = ROOT.parent
CONFIG_FILE = ROOT / "config" / "transcript-priorities.json"
STATE_DIR = ROOT / "state"
CHANNEL_CACHE = STATE_DIR / "channel-inventory.json"


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def config() -> dict:
    return load_json(CONFIG_FILE, {})


def radar_path() -> Path | None:
    choices = (
        SYSTEM_ROOT / "01 - DRAMA RADAR" / "drama-radar.json",
        SYSTEM_ROOT / "01 - DRAMA RADAR" / "output" / "drama-radar.json",
        SYSTEM_ROOT / "drama-radar.json",
        ROOT / "drama-radar.json",
    )
    return next((path for path in choices if path.exists()), None)


def flatten_records(value):
    if isinstance(value, list):
        for item in value:
            yield from flatten_records(item)
    elif isinstance(value, dict):
        if any(key in value for key in ("url", "link", "video_url", "youtube_url", "youtube_id")):
            yield value
        for child in value.values():
            if isinstance(child, (dict, list)):
                yield from flatten_records(child)


def first(record: dict, *keys):
    for key in keys:
        value = record.get(key)
        if value not in (None, ""):
            return value
    return None


def text_blob(item: dict) -> str:
    return " ".join(str(item.get(key) or "") for key in (
        "title", "description", "summary", "source", "story", "notes", "recommendation_reason"
    )).casefold()


def normalized_radar_items() -> list[dict]:
    path = radar_path()
    if not path:
        return []
    raw = load_json(path, [])
    output, seen = [], set()
    for record in flatten_records(raw):
        raw_url = str(first(record, "url", "video_url", "youtube_url", "link") or "")
        video = str(first(record, "youtube_id", "video_id") or youtube_id(raw_url) or "")
        if not video or video in seen:
            continue
        if urlparse(raw_url).netloc.casefold().endswith("rumble.com"):
            continue
        seen.add(video)
        output.append({
            "video_id": video,
            "url": raw_url or f"https://www.youtube.com/watch?v={video}",
            "title": str(first(record, "title", "name") or f"Radar video [{video}]"),
            "description": str(first(record, "description", "summary", "text") or ""),
            "source": str(first(record, "source", "channel", "author") or "Unknown Radar channel"),
            "published": first(record, "published", "published_at", "date", "timestamp"),
            "unique_sources": int(first(record, "unique_sources", "source_count") or 1),
            "selection_source": "drama_radar",
        })
    # Also recover YouTube links buried in recommendation text, receipts,
    # descriptions or other fields whose schema may change over time.
    youtube_url = re.compile(
        r"https?://(?:www\.|m\.)?(?:youtube\.com/(?:watch\?[^\s\"']*v=|live/|shorts/)|youtu\.be/)[A-Za-z0-9_?&=./%-]+",
        re.IGNORECASE,
    )
    serialized = json.dumps(raw, ensure_ascii=False)
    for match in youtube_url.findall(serialized):
        clean = match.rstrip(".,);]}")
        video = youtube_id(clean)
        if not video or video in seen:
            continue
        seen.add(video)
        output.append({
            "video_id": video,
            "url": clean,
            "title": f"Radar-recommended YouTube video [{video}]",
            "description": "YouTube link extracted from Drama Radar recommendation data",
            "source": "Unknown Radar-linked channel",
            "published": None,
            "unique_sources": 1,
            "selection_source": "drama_radar",
        })
    return output


def known_source_names() -> set[str]:
    names = set()
    roots = (SYSTEM_ROOT / "01 - DRAMA RADAR" / "config", ROOT / "config")
    for base in roots:
        if not base.exists():
            continue
        for path in base.glob("source*"):
            try:
                content = path.read_text(encoding="utf-8-sig")
            except Exception:
                continue
            if path.suffix.casefold() == ".json":
                value = load_json(path, {})
                records = value.values() if isinstance(value, dict) else value if isinstance(value, list) else []
                for record in records:
                    if isinstance(record, dict):
                        names.add(str(first(record, "name", "source", "channel") or "").casefold())
            else:
                names.update(line.strip().casefold() for line in content.splitlines() if line.strip() and not line.startswith("#"))
    return {name for name in names if name}


def parse_date(value):
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def score_radar(item: dict, known_sources: set[str], cfg: dict) -> tuple[int, int, str]:
    blob = text_blob(item)
    aliases = [str(x).casefold() for x in cfg.get("mark_aliases", [])]
    response_words = [str(x).casefold() for x in cfg.get("response_words", [])]
    direct_mark = any(alias in blob for alias in aliases)
    outsider = str(item.get("source") or "").casefold() not in known_sources
    score = 0
    score += 40 if direct_mark else 0
    score += 25  # It was linked or emitted by Radar.
    score += 20 if int(item.get("unique_sources", 1)) >= 2 else 0
    score += 20 if any(word in blob for word in response_words) else 0
    score += 15 if outsider else 0
    published = parse_date(item.get("published"))
    if published and (datetime.now(timezone.utc) - published).total_seconds() <= 48 * 3600:
        score += 10
    if direct_mark:
        return 1, -score, "direct_commi3_mark"
    if outsider:
        return 3, -score, "sleeper_outsider"
    return 4, -score, "radar_story"


def cached_channel_items(kind: str) -> list[dict]:
    cache = load_json(CHANNEL_CACHE, {})
    value = cache.get(kind, {}) if isinstance(cache, dict) else {}
    return value.get("items", []) if isinstance(value, dict) else []


def assemble(manual: list[str]) -> list[dict]:
    cfg, known = config(), known_source_names()
    ranked: dict[str, tuple[tuple, dict]] = {}

    def add(item: dict, key: tuple):
        video = str(item.get("video_id") or youtube_id(str(item.get("url") or "")) or "")
        if not video:
            return
        item["video_id"] = video
        item.setdefault("url", f"https://www.youtube.com/watch?v={video}")
        old = ranked.get(video)
        if old is None or key < old[0]:
            ranked[video] = (key, item)

    for position, value in enumerate(manual):
        video = youtube_id(value)
        if video:
            add({"video_id": video, "url": value, "selection_source": "manual_priority"}, (0, position))

    for item in normalized_radar_items():
        tier, negative_score, label = score_radar(item, known, cfg)
        item["selection_source"] = label
        published = parse_date(item.get("published"))
        recent = -(published.timestamp() if published else 0)
        add(item, (tier, negative_score, recent))

    for position, item in enumerate(cached_channel_items("piper")):
        item = dict(item)
        item["selection_source"] = "piper_priority"
        add(item, (2, position))

    for position, item in enumerate(cached_channel_items("commi3_mark")):
        item = dict(item)
        item["selection_source"] = "commi3_back_catalogue"
        add(item, (6, position))

    return [value[1] for value in sorted(ranked.values(), key=lambda pair: pair[0])]
