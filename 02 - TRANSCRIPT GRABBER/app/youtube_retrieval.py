from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

from youtube_transcript_api import YouTubeTranscriptApi

try:
    import yt_dlp
except ImportError:
    yt_dlp = None

GRABBER_ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = GRABBER_ROOT / "state"
TRANSCRIPTS_DIR = GRABBER_ROOT / "transcripts"
COOLDOWN = STATE_DIR / "transcript-cooldown.json"
RETRIES = STATE_DIR / "transcript-retries.json"
YOUTUBE_METADATA = STATE_DIR / "youtube-metadata.json"

PERMANENT = {"TranscriptsDisabled", "AgeRestricted", "VideoUnavailable"}
VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


class DualIpBlocked(RuntimeError):
    """Both transcript retrieval routes were rejected at the IP level."""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def dt(value):
    try:
        parsed = datetime.fromisoformat(str(value))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def safe_name(value: str) -> str:
    bad = '<>:"/\\|?*'
    cleaned = "".join("_" if ch in bad else ch for ch in value).strip().rstrip(".")
    return cleaned[:150] or "untitled"


def transcript_text_path(json_path: Path) -> Path:
    """Return the collision-safe, human-readable companion path."""
    return json_path.with_name(json_path.stem + ".transcript.txt")


def render_transcript_text(payload: dict) -> str:
    lines = [
        str(payload.get("title") or "YouTube transcript"),
        "=" * 72,
        f"Video ID: {payload.get('video_id') or payload.get('youtube_id') or ''}",
        f"Source: {payload.get('source') or ''}",
        f"Published: {payload.get('published') or ''}",
        f"URL: {payload.get('url') or ''}",
        "",
    ]
    for segment in payload.get("segments") or []:
        seconds = max(0, int(float(segment.get("start") or 0)))
        hours, remainder = divmod(seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        stamp = (
            f"{hours:02d}:{minutes:02d}:{seconds:02d}"
            if hours
            else f"{minutes:02d}:{seconds:02d}"
        )
        text = " ".join(str(segment.get("text") or "").split())
        if text:
            lines.append(f"[{stamp}] {text}")
    return "\n".join(lines).rstrip() + "\n"


def save_transcript_text(json_path: Path, payload: dict) -> Path:
    """Atomically save a readable transcript without overwriting reports."""
    path = transcript_text_path(json_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(render_transcript_text(payload), encoding="utf-8")
    os.replace(temporary, path)
    return path


def youtube_id(value: str) -> str | None:
    value = value.strip()
    if VIDEO_ID_RE.fullmatch(value):
        return value
    try:
        parsed = urlparse(value)
        host = parsed.netloc.lower().removeprefix("www.")
        if host == "youtu.be":
            candidate = parsed.path.strip("/").split("/")[0]
        elif host in {"youtube.com", "m.youtube.com", "music.youtube.com"}:
            if parsed.path == "/watch":
                candidate = parse_qs(parsed.query).get("v", [""])[0]
            else:
                parts = [part for part in parsed.path.split("/") if part]
                candidate = (
                    parts[1]
                    if len(parts) >= 2 and parts[0] in {"shorts", "live", "embed"}
                    else ""
                )
        else:
            candidate = ""
        return candidate if VIDEO_ID_RE.fullmatch(candidate) else None
    except Exception:
        return None


def is_ip_block(exc: Exception) -> bool:
    name = type(exc).__name__
    text = str(exc).casefold()
    return name in {"IpBlocked", "RequestBlocked"} or any(
        token in text
        for token in (
            "ip has been blocked",
            "request blocked",
            "too many requests",
            "http error 429",
            "status code 429",
            "sign in to confirm you're not a bot",
        )
    )


def api_transcript(video_id: str) -> tuple[list[dict], dict]:
    fetched = YouTubeTranscriptApi().fetch(video_id)
    segments = [
        {
            "text": segment.text,
            "start": round(float(segment.start), 3),
            "duration": round(float(segment.duration), 3),
        }
        for segment in fetched
    ]
    return segments, {
        "retrieval_method": "youtube_transcript_api",
        "caption_type": "published_or_automatic",
    }


def _timestamp_seconds(value: str) -> float:
    bits = value.replace(",", ".").split(":")
    try:
        if len(bits) == 3:
            return int(bits[0]) * 3600 + int(bits[1]) * 60 + float(bits[2])
        return int(bits[0]) * 60 + float(bits[1])
    except Exception:
        return 0.0


def _parse_json3(raw: str) -> list[dict]:
    data = json.loads(raw)
    segments = []
    for event in data.get("events", []):
        text = "".join(
            str(part.get("utf8") or "") for part in (event.get("segs") or [])
        ).replace("\n", " ").strip()
        if text:
            segments.append(
                {
                    "text": text,
                    "start": round(float(event.get("tStartMs", 0)) / 1000.0, 3),
                    "duration": round(
                        float(event.get("dDurationMs", 0)) / 1000.0, 3
                    ),
                }
            )
    return segments


def _parse_vtt(raw: str) -> list[dict]:
    lines = raw.replace("\r", "").split("\n")
    segments, index = [], 0
    while index < len(lines):
        line = lines[index].strip()
        if "-->" not in line:
            index += 1
            continue
        start_s, end_s = [
            part.strip().split(" ")[0] for part in line.split("-->", 1)
        ]
        index += 1
        text_lines = []
        while index < len(lines) and lines[index].strip():
            cleaned = re.sub(r"<[^>]+>", "", lines[index]).strip()
            if cleaned and cleaned not in text_lines:
                text_lines.append(cleaned)
            index += 1
        text = " ".join(text_lines).strip()
        if text:
            start = _timestamp_seconds(start_s)
            end = _timestamp_seconds(end_s)
            segments.append(
                {
                    "text": text,
                    "start": round(start, 3),
                    "duration": round(max(0, end - start), 3),
                }
            )
        index += 1
    return segments


def subtitle_transcript(video_id: str) -> tuple[list[dict], dict]:
    if yt_dlp is None:
        raise RuntimeError("Subtitle retrieval is unavailable because yt-dlp is not installed.")
    with yt_dlp.YoutubeDL(
        {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "extract_flat": False,
            "socket_timeout": 30,
        }
    ) as ydl:
        info = ydl.extract_info(
            f"https://www.youtube.com/watch?v={video_id}", download=False
        )
    selected = None
    for caption_type, pool in (
        ("published", info.get("subtitles") or {}),
        ("automatic", info.get("automatic_captions") or {}),
    ):
        for language in ("en", "en-US", "en-GB"):
            tracks = pool.get(language) or []
            if tracks:
                selected = (caption_type, language, tracks)
                break
        if selected:
            break
        for language, tracks in pool.items():
            if str(language).startswith("en") and tracks:
                selected = (caption_type, language, tracks)
                break
        if selected:
            break
    if not selected:
        raise RuntimeError("No English subtitles or automatic captions are available.")
    caption_type, language, tracks = selected
    track = (
        next((item for item in tracks if item.get("ext") == "json3"), None)
        or next((item for item in tracks if item.get("ext") == "vtt"), None)
        or tracks[0]
    )
    url = track.get("url")
    if not url:
        raise RuntimeError("YouTube supplied no downloadable caption file.")
    with urlopen(
        Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=30
    ) as response:
        raw = response.read().decode("utf-8", errors="replace")
    extension = str(track.get("ext") or "").lower()
    segments = _parse_json3(raw) if extension == "json3" else _parse_vtt(raw)
    if not segments:
        raise RuntimeError("The caption file contained no readable captions.")
    return segments, {
        "retrieval_method": "yt_dlp_subtitles",
        "caption_type": caption_type,
        "language": language,
        "caption_format": extension or "unknown",
    }


def recover_transcript(
    video_id: str,
    before_fallback: Callable[[], None] | None = None,
) -> tuple[list[dict], dict]:
    api_error = None
    try:
        print("  Trying the normal YouTube transcript service...", flush=True)
        return api_transcript(video_id)
    except Exception as exc:
        api_error = exc
        print("  Trying YouTube subtitles...", flush=True)
    if before_fallback:
        before_fallback()
    try:
        return subtitle_transcript(video_id)
    except Exception as subtitle_error:
        if is_ip_block(api_error) and is_ip_block(subtitle_error):
            raise DualIpBlocked(
                "Both transcript routes were rejected by YouTube at the IP level."
            ) from subtitle_error
        raise subtitle_error from api_error


def youtube_metadata(video_id: str, cache: dict) -> dict:
    cached = cache.get(video_id)
    checked = dt(cached.get("checked_at")) if isinstance(cached, dict) else None
    if checked and (datetime.now(timezone.utc) - checked).total_seconds() < 21600:
        return cached
    if yt_dlp is None:
        return {}
    try:
        with yt_dlp.YoutubeDL(
            {
                "quiet": True,
                "no_warnings": True,
                "skip_download": True,
                "extract_flat": False,
                "socket_timeout": 20,
            }
        ) as ydl:
            info = ydl.extract_info(
                f"https://www.youtube.com/watch?v={video_id}", download=False
            )
        result = {
            "checked_at": now_iso(),
            "live_status": info.get("live_status"),
            "was_live": bool(info.get("was_live")),
            "is_live": bool(info.get("is_live")),
            "release_timestamp": info.get("release_timestamp"),
            "timestamp": info.get("timestamp"),
            "duration": info.get("duration"),
            "title": info.get("title"),
            "channel": info.get("channel") or info.get("uploader"),
            "webpage_url": info.get("webpage_url"),
        }
        cache[video_id] = result
        return result
    except Exception as exc:
        cache[video_id] = {
            "checked_at": now_iso(),
            "metadata_error": f"{type(exc).__name__}: {exc}",
        }
        return cache[video_id]


def manual_item(video_id: str, raw_url: str, cache: dict, source_label: str) -> dict:
    metadata = youtube_metadata(video_id, cache)
    timestamp = metadata.get("release_timestamp") or metadata.get("timestamp")
    published = None
    try:
        if timestamp:
            published = datetime.fromtimestamp(
                float(timestamp), tz=timezone.utc
            ).isoformat()
    except Exception:
        pass
    return {
        "youtube_id": video_id,
        "source": metadata.get("channel") or source_label,
        "platform": "YouTube",
        "type": "video",
        "title": metadata.get("title") or f"Selected transcript [{video_id}]",
        "url": metadata.get("webpage_url")
        or raw_url
        or f"https://www.youtube.com/watch?v={video_id}",
        "published": published,
        "transcript_status": "pending",
    }


def livestream_ready(
    item: dict, published: datetime | None, now: datetime, cache: dict
) -> tuple[bool, str | None]:
    if not published or (now - published).total_seconds() >= 21600:
        return True, None
    video_id = str(item.get("youtube_id") or "")
    metadata = youtube_metadata(video_id, cache)
    live_status = str(metadata.get("live_status") or "").lower()
    if metadata.get("is_live") or live_status in {"is_live", "is_upcoming"}:
        return False, "livestream is live or upcoming"
    was_live = bool(metadata.get("was_live")) or live_status in {
        "post_live",
        "was_live",
    }
    if not was_live:
        return True, None
    start = metadata.get("release_timestamp") or metadata.get("timestamp")
    duration = metadata.get("duration")
    end_time = None
    try:
        if start:
            end_time = datetime.fromtimestamp(float(start), tz=timezone.utc)
            if duration:
                end_time += timedelta(seconds=float(duration))
    except Exception:
        pass
    eligible_at = (end_time or published) + timedelta(hours=6)
    return (
        (True, None)
        if now >= eligible_at
        else (False, f"livestream delay until {eligible_at.isoformat()}")
    )
