from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from youtube_transcript_api import YouTubeTranscriptApi

try:
    import yt_dlp
except ImportError:
    yt_dlp = None

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "radar"))
sys.path.insert(0, str(ROOT / "transcripts"))

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

COOLDOWN = BRAIN_DIR / "transcript-cooldown.json"
RETRIES = BRAIN_DIR / "transcript-retries.json"
YOUTUBE_METADATA = BRAIN_DIR / "youtube-metadata.json"
PERMANENT = {"TranscriptsDisabled", "AgeRestricted", "VideoUnavailable"}
BLOCKED = {"IpBlocked", "RequestBlocked"}


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
        }
        cache[video_id] = result
        return result
    except Exception as exc:
        cache[video_id] = {"checked_at": now_iso(), "metadata_error": f"{type(exc).__name__}: {exc}"}
        return cache[video_id]


def livestream_ready(item: dict, published: datetime | None, now: datetime, cache: dict) -> tuple[bool, str | None]:
    """Return whether a recent YouTube item is ready for transcript retrieval.

    Ordinary uploads pass through. Livestreams are held until six hours after
    the estimated stream end. Live and upcoming broadcasts are deferred.
    """
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

    candidates: dict[str, tuple[int, datetime, dict]] = {}
    for item in radar:
        video_id = item.get("youtube_id")
        if not video_id:
            continue
        video_id = str(video_id)
        if video_id in existing:
            continue
        if item.get("transcript_status") in {"available", "unavailable", "permanent_failure"}:
            continue
        retry = retries.get(video_id, {})
        next_retry = dt(retry.get("next_retry"))
        if next_retry and now < next_retry:
            continue
        published = dt(item.get("published"))
        config = sources.get((item.get("source"), item.get("platform")), {})
        delay = float(config.get("transcript_delay_hours", 6))
        if published and (now - published).total_seconds() < delay * 3600:
            continue
        ready, defer_reason = livestream_ready(item, published, now, metadata_cache)
        if not ready:
            continue
        has_failed_before = 1 if retry else 0
        retry_attempts = int(retry.get("attempts", 0) or 0)
        candidate = (has_failed_before, retry_attempts, int(config.get("priority", 50)), published or now, item)
        old = candidates.get(video_id)
        if old is None or candidate[:4] < old[:4]:
            candidates[video_id] = candidate

    # Fresh eligible items always come first. Previously failed items are
    # deliberately pushed behind them, then ordered by fewest attempts.
    full_queue = sorted(candidates.values(), key=lambda row: (row[0], row[1], -row[2], row[3]))
    queue = full_queue[: max(0, args.limit)]
    attempts = saved = unavailable = retry_count = failed = 0
    blocked = False
    results = {"saved": [], "unavailable": [], "retry": []}
    print(f"Eligible queue: {len(full_queue)} (run limit {args.limit})")
    print("\nTRANSCRIPT RECOVERY")

    api = YouTubeTranscriptApi()
    save_json(YOUTUBE_METADATA, metadata_cache)

    for number, (_, _, _, _, item) in enumerate(queue, 1):
        video_id = str(item["youtube_id"])
        attempts += 1
        progress("Recovering transcript", number - 1, len(queue), f"{item.get('source')} • {item.get('title')}", started)
        if number > 1:
            time.sleep(random.uniform(8, 15))
        try:
            fetched = api.fetch(video_id)
            segments = [
                {
                    "text": segment.text,
                    "start": round(float(segment.start), 3),
                    "duration": round(float(segment.duration), 3),
                }
                for segment in fetched
            ]
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
                "segments": segments,
            }
            save_json(path, payload)
            retries.pop(video_id, None)
            saved += 1
            results["saved"].append(item_receipt(item))
            print(f"  SAVED: {path.relative_to(ROOT)}", flush=True)
            progress("Transcript complete", number, len(queue), f"{item.get('source')} • saved", started)
        except Exception as exc:
            reason = type(exc).__name__
            message = str(exc)
            failed += 1
            if reason in BLOCKED:
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
                print(f"  BLOCKED: cooldown until {until.isoformat()}", flush=True)
                progress("Transcript blocked", number, len(queue), f"{item.get('source')} • {reason}", started)
                break

            lower_message = message.lower()
            if reason == "VideoUnplayable" and any(token in lower_message for token in ["live event will begin", "premiere will begin", "currently live"]):
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
        "status": transcript_status,
        "results": results,
    }
    stats = update_stats(radar=radar, transcript_run=transcript_run)
    print_summary(stats, "TRANSCRIPT RECOVERY COMPLETE", receipt_kind="transcripts")

    if publish_enabled and saved:
        git(["add", "drama-radar.json", "radar-stats.json", "transcripts", "radar/receipts"])
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
