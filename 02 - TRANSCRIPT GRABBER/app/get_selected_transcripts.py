from __future__ import annotations

import argparse
import json
import os
import random
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from build_index import build
from youtube_retrieval import (
    COOLDOWN,
    PERMANENT,
    RETRIES,
    TRANSCRIPTS_DIR,
    YOUTUBE_METADATA,
    dt,
    livestream_ready,
    manual_item,
    now_iso,
    recover_transcript,
    safe_name,
    youtube_id,
)

GRABBER_ROOT = Path(__file__).resolve().parents[1]
SYSTEM_ROOT = GRABBER_ROOT.parent
QUEUE_FILE = GRABBER_ROOT / "config" / "selected-transcripts.txt"

QUEUE_HEADER = """# SELECTED TRANSCRIPTS
#
# This local copy is replaced from GitHub before Stalinvo checks the queue.
# The shared source of truth is:
#   transcripts/selected-transcripts.txt
#
# Place one selected YouTube URL or video ID per line.
"""


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def read_queue() -> list[str]:
    if not QUEUE_FILE.exists():
        return []
    values = []
    seen = set()
    for raw in QUEUE_FILE.read_text(encoding="utf-8-sig").splitlines():
        value = raw.strip()
        if not value or value.startswith("#"):
            continue
        video_id = youtube_id(value)
        if not video_id:
            print(f"QUEUE WARNING: ignored invalid YouTube entry: {value}")
            continue
        if video_id not in seen:
            seen.add(video_id)
            values.append(value)
    return values


def write_queue(values: list[str]) -> None:
    body = QUEUE_HEADER.rstrip() + "\n"
    if values:
        body += "\n" + "\n".join(values) + "\n"
    temporary = QUEUE_FILE.with_suffix(".txt.tmp")
    temporary.write_text(body, encoding="utf-8")
    os.replace(temporary, QUEUE_FILE)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Recover only transcripts explicitly selected in the GitHub queue."
    )
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--ignore-cooldown", action="store_true")
    args = parser.parse_args()

    cooldown = load_json(COOLDOWN, {})
    until = dt(cooldown.get("until"))
    now = datetime.now(timezone.utc)
    if (
        not args.ignore_cooldown
        and cooldown.get("active")
        and until
        and now < until
    ):
        print(f"Transcript cooldown active until {until.isoformat()}")
        return 2
    if cooldown.get("active"):
        save_json(COOLDOWN, {**cooldown, "active": False, "cleared_at": now_iso()})

    queue_values = read_queue()
    existing = {
        str(item.get("video_id"))
        for item in build().get("transcripts", [])
        if item.get("video_id")
    }
    retries = load_json(RETRIES, {})
    metadata_cache = load_json(YOUTUBE_METADATA, {})

    # Already completed selections leave the shared queue immediately.
    pending_values = [
        value for value in queue_values if youtube_id(value) not in existing
    ]
    if pending_values != queue_values:
        write_queue(pending_values)

    candidates = []
    for value in pending_values:
        video_id = youtube_id(value)
        if not video_id:
            continue
        retry = retries.get(video_id, {})
        next_retry = dt(retry.get("next_retry"))
        if next_retry and now < next_retry:
            continue
        item = manual_item(video_id, value, metadata_cache, "GitHub Selection")
        published = dt(item.get("published"))
        ready, reason = livestream_ready(item, published, now, metadata_cache)
        if not ready:
            print(f"QUEUE WAITING: {video_id} - {reason}")
            continue
        candidates.append((value, item))

    save_json(YOUTUBE_METADATA, metadata_cache)
    selected = candidates[: max(0, args.limit)]
    print(
        f"GitHub selection queue: {len(pending_values)} pending, "
        f"{len(candidates)} ready, run limit {args.limit}"
    )

    completed_ids = set()
    blocked = False
    saved = 0
    unavailable = 0

    for number, (queue_value, item) in enumerate(selected, 1):
        video_id = str(item["youtube_id"])
        print(f"\n[{number}/{len(selected)}] {item.get('title')} [{video_id}]")
        if number > 1:
            time.sleep(random.uniform(8, 15))
        try:
            segments, retrieval = recover_transcript(video_id)
            published = dt(item.get("published"))
            month = published.strftime("%Y-%m") if published else now.strftime("%Y-%m")
            path = (
                TRANSCRIPTS_DIR
                / month
                / f"{safe_name(item.get('title', ''))} [{video_id}].json"
            )
            save_json(
                path,
                {
                    "video_id": video_id,
                    "title": item.get("title"),
                    "source": item.get("source"),
                    "platform": "YouTube",
                    "published": item.get("published"),
                    "url": item.get("url"),
                    "downloaded_at": now_iso(),
                    "segment_count": len(segments),
                    "retrieval": retrieval,
                    "selection_source": "github_queue",
                    "segments": segments,
                },
            )
            retries.pop(video_id, None)
            completed_ids.add(video_id)
            saved += 1
            print(f"  Saved: {path.relative_to(SYSTEM_ROOT)}")
        except Exception as exc:
            reason = type(exc).__name__
            message = str(exc)
            if reason == "DualIpBlocked":
                blocked = True
                retry_at = now + timedelta(hours=12)
                save_json(
                    COOLDOWN,
                    {
                        "active": True,
                        "reason": reason,
                        "started_at": now_iso(),
                        "until": retry_at.isoformat(),
                    },
                )
                retries[video_id] = {
                    "video_id": video_id,
                    "title": item.get("title"),
                    "reason": reason,
                    "status": "retry",
                    "next_retry": retry_at.isoformat(),
                    "message": message,
                }
                print("  Both retrieval routes are blocked; selection remains queued.")
                break

            attempts = int(retries.get(video_id, {}).get("attempts", 0)) + 1
            lower_message = message.casefold()
            permanent = reason in PERMANENT or (
                reason == "VideoUnplayable"
                and any(
                    token in lower_message
                    for token in ("members-only", "join this channel", "private")
                )
            )
            if permanent or attempts >= 3:
                completed_ids.add(video_id)
                unavailable += 1
                status = "unavailable"
                next_retry = None
            else:
                status = "retry"
                delay_hours = (1, 6, 24)[min(attempts - 1, 2)]
                next_retry = (now + timedelta(hours=delay_hours)).isoformat()

            retries[video_id] = {
                "video_id": video_id,
                "title": item.get("title"),
                "source": item.get("source"),
                "reason": reason,
                "message": message,
                "attempts": attempts,
                "status": status,
                "last_attempt": now_iso(),
                "next_retry": next_retry,
            }
            print(f"  {status.upper()}: {reason}")

    if completed_ids:
        write_queue(
            [
                value
                for value in read_queue()
                if youtube_id(value) not in completed_ids
            ]
        )

    save_json(RETRIES, retries)
    build()
    print(
        f"\nSelection run complete: {saved} saved, "
        f"{unavailable} unavailable, {len(read_queue())} still queued."
    )
    return 2 if blocked else 0


if __name__ == "__main__":
    raise SystemExit(main())
