from __future__ import annotations

import argparse
import json
import random
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

from youtube_transcript_api import YouTubeTranscriptApi

try:
    import yt_dlp
except ImportError:
    yt_dlp = None

GRABBER_ROOT = Path(__file__).resolve().parents[1]
SYSTEM_ROOT = GRABBER_ROOT.parent
RADAR_ROOT = SYSTEM_ROOT / "01 - DRAMA RADAR"
ROOT = SYSTEM_ROOT
sys.path.insert(0, str(RADAR_ROOT / "app"))
sys.path.insert(0, str(GRABBER_ROOT / "app"))

from common import (
    BRAIN_DIR,
    RADAR_PATH,
    SOURCES_PATH,
    TRANSCRIPTS_DIR,
    load_json,
    now_iso,
    save_json,
)
from build_index import build
from link import link
from stats import print_summary, update_stats
from console import progress

STATE_DIR = GRABBER_ROOT / "state"
COOLDOWN = STATE_DIR / "transcript-cooldown.json"
RETRIES = STATE_DIR / "transcript-retries.json"
YOUTUBE_METADATA = STATE_DIR / "youtube-metadata.json"

PRIORITY_FILE = RADAR_ROOT / "config" / "priority.txt"
WATCHLIST_FILE = RADAR_ROOT / "config" / "watchlist.txt"
IGNORE_FILE = RADAR_ROOT / "config" / "ignore.txt"

EXTERNAL_TRANSCRIPT_CUTOFF = datetime(2026, 7, 15, tzinfo=timezone.utc)
OWN_SOURCE = "Commi3 Mark"

SOURCE_PRIORITIES = {
    "Commi3 Mark": 100,
    "KatyDid": 99,
    "Ethan Van Sciver": 95,
    "Frog Tony": 95,
    "Liam Gray": 85,
    "Shane Davis": 85,
    "Eric July": 80,
    "Elissa Clips": 60,
}

PERMANENT = {"TranscriptsDisabled", "AgeRestricted", "VideoUnavailable"}
BLOCKED = {"IpBlocked", "RequestBlocked", "DualIpBlocked"}
VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


class DualIpBlocked(RuntimeError):
    """Both transcript retrieval routes were rejected at the IP level."""


def is_ip_block(exc: Exception) -> bool:
    name = type(exc).__name__
    text = str(exc).casefold()
    return name in {"IpBlocked", "RequestBlocked"} or any(
        token in text for token in (
            "ip has been blocked", "request blocked", "too many requests",
            "http error 429", "status code 429", "sign in to confirm you’re not a bot",
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
    return segments, {"retrieval_method": "youtube_transcript_api", "caption_type": "published_or_automatic"}


def _parse_json3(raw: str) -> list[dict]:
    data = json.loads(raw)
    segments = []
    for event in data.get("events", []):
        parts = event.get("segs") or []
        text = "".join(str(part.get("utf8") or "") for part in parts).replace("\n", " ").strip()
        if not text:
            continue
        start = float(event.get("tStartMs", 0)) / 1000.0
        duration = float(event.get("dDurationMs", 0)) / 1000.0
        segments.append({"text": text, "start": round(start, 3), "duration": round(duration, 3)})
    return segments


def _timestamp_seconds(value: str) -> float:
    bits = value.replace(",", ".").split(":")
    try:
        if len(bits) == 3:
            return int(bits[0]) * 3600 + int(bits[1]) * 60 + float(bits[2])
        return int(bits[0]) * 60 + float(bits[1])
    except Exception:
        return 0.0


def _parse_vtt(raw: str) -> list[dict]:
    lines = raw.replace("\r", "").split("\n")
    segments, i = [], 0
    while i < len(lines):
        line = lines[i].strip()
        if "-->" not in line:
            i += 1
            continue
        start_s, end_s = [part.strip().split(" ")[0] for part in line.split("-->", 1)]
        i += 1
        text_lines = []
        while i < len(lines) and lines[i].strip():
            cleaned = re.sub(r"<[^>]+>", "", lines[i]).strip()
            if cleaned and cleaned not in text_lines:
                text_lines.append(cleaned)
            i += 1
        text = " ".join(text_lines).strip()
        if text:
            start = _timestamp_seconds(start_s)
            end = _timestamp_seconds(end_s)
            segments.append({"text": text, "start": round(start, 3), "duration": round(max(0, end-start), 3)})
        i += 1
    return segments


def subtitle_transcript(video_id: str) -> tuple[list[dict], dict]:
    if yt_dlp is None:
        raise RuntimeError("Subtitle retrieval is unavailable because yt-dlp is not installed.")
    options = {
        "quiet": True, "no_warnings": True, "skip_download": True,
        "extract_flat": False, "socket_timeout": 30,
    }
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
    pools = [("published", info.get("subtitles") or {}), ("automatic", info.get("automatic_captions") or {})]
    selected = None
    for caption_type, pool in pools:
        for lang in ("en", "en-US", "en-GB"):
            tracks = pool.get(lang) or []
            if tracks:
                selected = (caption_type, lang, tracks)
                break
        if selected:
            break
        for lang, tracks in pool.items():
            if str(lang).startswith("en") and tracks:
                selected = (caption_type, lang, tracks)
                break
        if selected:
            break
    if not selected:
        raise RuntimeError("No English subtitles or automatic captions are available.")
    caption_type, language, tracks = selected
    track = next((x for x in tracks if x.get("ext") == "json3"), None) or next((x for x in tracks if x.get("ext") == "vtt"), None) or tracks[0]
    url = track.get("url")
    if not url:
        raise RuntimeError("YouTube supplied a caption listing without a downloadable caption file.")
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(req, timeout=30) as response:
        raw = response.read().decode("utf-8", errors="replace")
    ext = str(track.get("ext") or "").lower()
    segments = _parse_json3(raw) if ext == "json3" else _parse_vtt(raw)
    if not segments:
        raise RuntimeError("The subtitle file was downloaded but contained no readable captions.")
    return segments, {
        "retrieval_method": "yt_dlp_subtitles",
        "caption_type": caption_type,
        "language": language,
        "caption_format": ext or "unknown",
    }


def recover_transcript(video_id: str) -> tuple[list[dict], dict]:
    api_error = None
    try:
        print("  Trying the normal YouTube transcript service...", flush=True)
        return api_transcript(video_id)
    except Exception as exc:
        api_error = exc
        if is_ip_block(exc):
            print("  The normal transcript service is blocked. Trying YouTube subtitles...", flush=True)
        else:
            print("  No usable transcript from the normal service. Trying YouTube subtitles...", flush=True)
    try:
        return subtitle_transcript(video_id)
    except Exception as subtitle_error:
        if is_ip_block(api_error) and is_ip_block(subtitle_error):
            raise DualIpBlocked("Both transcript routes were rejected by YouTube at the IP level.") from subtitle_error
        # A single blocked route is not enough to declare a global block.
        if is_ip_block(subtitle_error):
            raise RuntimeError("The subtitle route is temporarily blocked, but the normal transcript route was not IP-blocked.") from subtitle_error
        raise subtitle_error from api_error


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


def is_git_repo() -> bool:
    return (ROOT / ".git").exists()


def git(args: list[str]) -> bool:
    if not is_git_repo():
        return False
    try:
        return subprocess.run(["git", *args], cwd=ROOT, check=True).returncode == 0
    except Exception as exc:
        print(f"GIT WARNING: {exc}")
        return False


def read_control_file(path: Path) -> list[str]:
    if not path.exists():
        return []
    values = []
    seen = set()
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        value = raw.strip()
        if not value or value.startswith("#"):
            continue
        key = value.casefold()
        if key not in seen:
            seen.add(key)
            values.append(value)
    return values


def write_control_file(path: Path, values: list[str], header: str) -> None:
    body = header.rstrip() + "\n\n"
    if values:
        body += "\n".join(values) + "\n"
    path.write_text(body, encoding="utf-8")


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
                candidate = parts[1] if len(parts) >= 2 and parts[0] in {"shorts", "live", "embed"} else ""
        else:
            candidate = ""
        return candidate if VIDEO_ID_RE.fullmatch(candidate) else None
    except Exception:
        return None


def normalized_tokens(values: list[str]) -> set[str]:
    return {value.strip().casefold() for value in values if value.strip()}


def item_matches_tokens(item: dict, tokens: set[str]) -> bool:
    video_id = str(item.get("youtube_id") or "").casefold()
    if video_id and video_id in tokens:
        return True
    haystack = "\n".join(
        str(item.get(field) or "")
        for field in ("source", "url", "title", "description")
    ).casefold()
    return any(token in haystack for token in tokens if not youtube_id(token))


def youtube_metadata(video_id: str, cache: dict) -> dict:
    cached = cache.get(video_id)
    cached_at = dt(cached.get("checked_at")) if isinstance(cached, dict) else None
    if cached_at and (datetime.now(timezone.utc) - cached_at).total_seconds() < 6 * 3600:
        return cached
    if yt_dlp is None:
        return {}
    options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": False,
        "socket_timeout": 20,
    }
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
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
        cache[video_id] = {"checked_at": now_iso(), "metadata_error": f"{type(exc).__name__}: {exc}"}
        return cache[video_id]


def manual_item(video_id: str, raw_url: str, cache: dict, source_label: str) -> dict:
    metadata = youtube_metadata(video_id, cache)
    timestamp = metadata.get("release_timestamp") or metadata.get("timestamp")
    published = None
    try:
        if timestamp:
            published = datetime.fromtimestamp(float(timestamp), tz=timezone.utc).isoformat()
    except Exception:
        published = None
    return {
        "youtube_id": video_id,
        "source": metadata.get("channel") or source_label,
        "platform": "YouTube",
        "type": "video",
        "title": metadata.get("title") or f"Manual transcript request [{video_id}]",
        "url": metadata.get("webpage_url") or raw_url or f"https://www.youtube.com/watch?v={video_id}",
        "published": published,
        "transcript_status": "pending",
        "_manual": source_label,
    }


def livestream_ready(item: dict, published: datetime | None, now: datetime, cache: dict) -> tuple[bool, str | None]:
    """Ordinary uploads pass; livestreams wait until six hours after ending."""
    if not published or (now - published).total_seconds() >= 6 * 3600:
        return True, None
    video_id = str(item.get("youtube_id") or "")
    metadata = youtube_metadata(video_id, cache)
    live_status = str(metadata.get("live_status") or "").lower()
    if metadata.get("is_live") or live_status in {"is_live", "is_upcoming"}:
        return False, "livestream is live or upcoming"
    was_live = bool(metadata.get("was_live")) or live_status in {"post_live", "was_live"}
    if not was_live:
        return True, None
    start_ts = metadata.get("release_timestamp") or metadata.get("timestamp")
    duration = metadata.get("duration")
    end_time = None
    try:
        if start_ts:
            end_time = datetime.fromtimestamp(float(start_ts), tz=timezone.utc)
            if duration:
                end_time += timedelta(seconds=float(duration))
    except Exception:
        end_time = None
    eligible_at = (end_time or published) + timedelta(hours=6)
    if now < eligible_at:
        remaining = eligible_at - now
        hours = int(remaining.total_seconds() // 3600)
        minutes = int((remaining.total_seconds() % 3600) // 60)
        return False, f"livestream delay ({hours}h {minutes}m remaining)"
    return True, None


def item_receipt(item: dict, reason: str | None = None) -> dict:
    result = {
        "video_id": str(item.get("youtube_id") or ""),
        "source": item.get("source"),
        "title": item.get("title"),
    }
    if reason:
        result["reason"] = reason
    return result


def source_priority(item: dict, config: dict) -> int:
    return SOURCE_PRIORITIES.get(str(item.get("source") or ""), int(config.get("priority", 50)))


def main() -> int:
    started = time.time()
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--ignore-cooldown", action="store_true")
    args = parser.parse_args()

    publish_enabled = args.publish and is_git_repo()
    if args.publish and not publish_enabled:
        print("GIT: This folder is not yet connected to a repository; running locally only.")
    if publish_enabled:
        git(["pull", "--rebase"])

    cooldown = load_json(COOLDOWN, {})
    until = dt(cooldown.get("until"))
    if (
        not args.ignore_cooldown
        and cooldown.get("active")
        and until
        and datetime.now(timezone.utc) < until
    ):
        print(f"Transcript cooldown active until {until.isoformat()}")
        return 2
    if cooldown.get("active"):
        save_json(COOLDOWN, {**cooldown, "active": False, "cleared_at": now_iso()})

    radar = load_json(RADAR_PATH, [])
    sources = {
        (entry.get("name"), entry.get("platform")): entry
        for entry in load_json(SOURCES_PATH, [])
    }
    retries = load_json(RETRIES, {})
    metadata_cache = load_json(YOUTUBE_METADATA, {})
    existing = {str(item.get("video_id")) for item in build().get("transcripts", [])}
    now = datetime.now(timezone.utc)

    priority_values = read_control_file(PRIORITY_FILE)
    watchlist_values = read_control_file(WATCHLIST_FILE)
    ignore_values = read_control_file(IGNORE_FILE)

    priority_by_id = {video_id: value for value in priority_values if (video_id := youtube_id(value))}
    watchlist_by_id = {video_id: value for value in watchlist_values if (video_id := youtube_id(value))}
    ignore_ids = {video_id for value in ignore_values if (video_id := youtube_id(value))}
    watch_tokens = normalized_tokens([value for value in watchlist_values if not youtube_id(value)])
    ignore_tokens = normalized_tokens([value for value in ignore_values if not youtube_id(value)])

    completed_priority_ids = set(priority_by_id) & existing
    priority_values = [
        value for value in priority_values
        if youtube_id(value) not in completed_priority_ids
    ]
    if completed_priority_ids:
        write_control_file(
            PRIORITY_FILE,
            priority_values,
            "# HIGHEST PRIORITY TRANSCRIPTS\n"
            "# Paste one YouTube video or livestream URL per line.\n"
            "# Successful or already-downloaded links remove themselves automatically.",
        )

    radar_by_id = {
        str(item.get("youtube_id")): item
        for item in radar
        if item.get("youtube_id")
    }

    # Queue tuple:
    # (tier, failed_before, retry_attempts, negative_priority, negative_timestamp, item)
    # Lower tier wins. Newer timestamps win because they are negated.
    candidates: dict[str, tuple[int, int, int, int, float, dict]] = {}

    def add_candidate(item: dict, tier: int, bypass_cutoff: bool = False, bypass_retry_delay: bool = False) -> None:
        video_id = str(item.get("youtube_id") or "")
        if not video_id or video_id in existing or video_id in ignore_ids:
            return
        if item_matches_tokens(item, ignore_tokens):
            return
        if item.get("transcript_status") in {"available", "unavailable", "permanent_failure"}:
            return

        retry = retries.get(video_id, {})
        next_retry = dt(retry.get("next_retry"))
        if not bypass_retry_delay and next_retry and now < next_retry:
            return

        published = dt(item.get("published"))
        source = str(item.get("source") or "")
        watched = video_id in watchlist_by_id or item_matches_tokens(item, watch_tokens)
        if not bypass_cutoff and not watched and source != OWN_SOURCE:
            if published is None or published < EXTERNAL_TRANSCRIPT_CUTOFF:
                return

        config = sources.get((item.get("source"), item.get("platform")), {})
        if tier >= 2:
            delay = float(config.get("transcript_delay_hours", 6))
            if published and (now - published).total_seconds() < delay * 3600:
                return

        ready, _ = livestream_ready(item, published, now, metadata_cache)
        if not ready:
            return

        failed_before = 1 if retry else 0
        retry_attempts = int(retry.get("attempts", 0) or 0)
        priority = source_priority(item, config)
        timestamp = published.timestamp() if published else now.timestamp()
        candidate = (tier, failed_before, retry_attempts, -priority, -timestamp, item)
        old = candidates.get(video_id)
        if old is None or candidate[:5] < old[:5]:
            candidates[video_id] = candidate

    # Tier 0: hand-picked emergency links. Ignore still overrides them.
    for video_id, raw_url in priority_by_id.items():
        item = radar_by_id.get(video_id) or manual_item(video_id, raw_url, metadata_cache, "Priority Queue")
        add_candidate(item, tier=0, bypass_cutoff=True, bypass_retry_delay=True)

    # Tier 1: persistent forced-monitor video links.
    for video_id, raw_url in watchlist_by_id.items():
        item = radar_by_id.get(video_id) or manual_item(video_id, raw_url, metadata_cache, "Watchlist")
        add_candidate(item, tier=1, bypass_cutoff=True)

    # Tier 1 for matching watched channels/sources; tier 2 for ordinary radar items.
    for item in radar:
        watched = item_matches_tokens(item, watch_tokens)
        add_candidate(item, tier=1 if watched else 2, bypass_cutoff=watched)

    full_queue = sorted(candidates.values(), key=lambda row: row[:5])
    queue = full_queue[: max(0, args.limit)]
    attempts = saved = unavailable = retry_count = failed = 0
    blocked = False
    results = {"saved": [], "unavailable": [], "retry": []}
    print(
        f"Eligible queue: {len(full_queue)} "
        f"(priority {sum(1 for row in full_queue if row[0] == 0)}, "
        f"watchlist {sum(1 for row in full_queue if row[0] == 1)}, "
        f"normal {sum(1 for row in full_queue if row[0] == 2)}; "
        f"run limit {args.limit})"
    )
    print("\nTRANSCRIPT RECOVERY")

    save_json(YOUTUBE_METADATA, metadata_cache)
    saved_priority_ids = set()

    for number, (_, _, _, _, _, item) in enumerate(queue, 1):
        video_id = str(item["youtube_id"])
        attempts += 1
        progress("Recovering transcript", number - 1, len(queue), f"{item.get('source')} • {item.get('title')}", started)
        if number > 1:
            time.sleep(random.uniform(8, 15))
        try:
            segments, retrieval = recover_transcript(video_id)
            published = dt(item.get("published"))
            month = published.strftime("%Y-%m") if published else now.strftime("%Y-%m")
            path = TRANSCRIPTS_DIR / month / f"{safe_name(item.get('title', ''))} [{video_id}].json"
            payload = {
                "video_id": video_id,
                "title": item.get("title"),
                "source": item.get("source"),
                "platform": "YouTube",
                "published": item.get("published"),
                "url": item.get("url"),
                "downloaded_at": now_iso(),
                "segment_count": len(segments),
                "retrieval": retrieval,
                "segments": segments,
            }
            save_json(path, payload)
            retries.pop(video_id, None)
            if video_id in priority_by_id:
                saved_priority_ids.add(video_id)
            saved += 1
            results["saved"].append(item_receipt(item))
            method = "YouTube subtitles" if retrieval.get("retrieval_method") == "yt_dlp_subtitles" else "YouTube transcript"
            print(f"  Saved via {method}: {path.relative_to(ROOT)}", flush=True)
            progress("Transcript complete", number, len(queue), f"{item.get('source')} • saved", started)
        except Exception as exc:
            reason = type(exc).__name__
            message = str(exc)
            failed += 1
            if reason == "DualIpBlocked":
                blocked = True
                retry_count += 1
                until = now + timedelta(hours=12)
                save_json(
                    COOLDOWN,
                    {
                        "active": True,
                        "reason": reason,
                        "started_at": now_iso(),
                        "until": until.isoformat(),
                    },
                )
                retries[video_id] = {
                    "video_id": video_id,
                    "title": item.get("title"),
                    "reason": reason,
                    "status": "retry",
                    "next_retry": until.isoformat(),
                    "message": message,
                }
                results["retry"].append(item_receipt(item, reason))
                print(f"  Both retrieval routes are IP-blocked. Collection is paused until {until.isoformat()}.", flush=True)
                progress("Transcript blocked", number, len(queue), f"{item.get('source')} • {reason}", started)
                break

            lower_message = message.lower()
            if reason == "VideoUnplayable" and any(
                token in lower_message
                for token in ["live event will begin", "premiere will begin", "currently live"]
            ):
                status = "retry"
                next_retry = (now + timedelta(hours=6)).isoformat()
            elif reason in PERMANENT or (
                reason == "VideoUnplayable"
                and any(token in lower_message for token in ["members-only", "join this channel", "private"])
            ):
                status = "unavailable"
                next_retry = None
            else:
                old_attempts = int(retries.get(video_id, {}).get("attempts", 0)) + 1
                status = "unavailable" if old_attempts >= 3 else "retry"
                next_retry = (
                    None
                    if status == "unavailable"
                    else (now + timedelta(hours=[1, 6, 24][min(old_attempts - 1, 2)])).isoformat()
                )

            if status == "unavailable":
                unavailable += 1
                item["transcript_status"] = "unavailable"
                results["unavailable"].append(item_receipt(item, reason))
            else:
                retry_count += 1
                results["retry"].append(item_receipt(item, reason))

            retries[video_id] = {
                "video_id": video_id,
                "title": item.get("title"),
                "source": item.get("source"),
                "reason": reason,
                "message": message,
                "attempts": int(retries.get(video_id, {}).get("attempts", 0)) + 1,
                "status": status,
                "last_attempt": now_iso(),
                "next_retry": next_retry,
            }
            print(f"  {status.upper()}: {reason}", flush=True)
            progress("Transcript complete", number, len(queue), f"{item.get('source')} • {status}", started)

    if saved_priority_ids:
        remaining_priority = [
            value for value in read_control_file(PRIORITY_FILE)
            if youtube_id(value) not in saved_priority_ids
        ]
        write_control_file(
            PRIORITY_FILE,
            remaining_priority,
            "# HIGHEST PRIORITY TRANSCRIPTS\n"
            "# Paste one YouTube video or livestream URL per line.\n"
            "# Successful or already-downloaded links remove themselves automatically.",
        )

    save_json(RETRIES, retries)
    save_json(RADAR_PATH, radar)
    build()
    linked = link()
    radar = load_json(RADAR_PATH, [])

    transcript_status = "healthy"
    if blocked or (attempts > 0 and saved == 0):
        transcript_status = "stalled"
    elif unavailable or retry_count:
        transcript_status = "degraded"

    transcript_run = {
        "runner": "stalinvo",
        "started_at": datetime.fromtimestamp(started, timezone.utc).isoformat(),
        "finished_at": now_iso(),
        "duration_seconds": round(time.time() - started, 2),
        "attempts": attempts,
        "new_transcripts": saved,
        "failed": failed,
        "unavailable": unavailable,
        "retry_count": retry_count,
        "linked": linked,
        "blocked": blocked,
        "eligible_before_limit": len(full_queue),
        "remaining_after_run": max(0, len(full_queue) - attempts),
        "queue_counts": {
            "priority": sum(1 for row in full_queue if row[0] == 0),
            "watchlist": sum(1 for row in full_queue if row[0] == 1),
            "normal": sum(1 for row in full_queue if row[0] == 2),
        },
        "status": transcript_status,
        "results": results,
    }
    stats = update_stats(radar=radar, transcript_run=transcript_run)
    print_summary(stats, "TRANSCRIPT RECOVERY COMPLETE", receipt_kind="transcripts")

    if publish_enabled and (saved or saved_priority_ids):
        git([
            "add",
            "drama-radar.json",
            "radar-stats.json",
            "transcripts",
            "radar/receipts",
            "priority.txt",
            "watchlist.txt",
            "ignore.txt",
        ])
        subprocess.run(
            [
                "git",
                "add",
                "-f",
                "radar/brain/transcript-retries.json",
                "radar/brain/transcript-cooldown.json",
            ],
            cwd=ROOT,
        )
        git(["commit", "-m", f"Add {saved} transcript(s)"])
        git(["push"])
    elif publish_enabled:
        print("GIT: No new transcripts to publish.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
