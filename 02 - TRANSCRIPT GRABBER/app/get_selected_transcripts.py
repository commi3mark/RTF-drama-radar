from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from adaptive_pacing import clean_attempt, cooldown_remaining, describe, dual_block, wait_before_video
from build_index import build
from candidate_queue import assemble, config
from channel_inventory import refresh
from youtube_retrieval import (
    PERMANENT, RETRIES, TRANSCRIPTS_DIR, YOUTUBE_METADATA, DualIpBlocked,
    dt, livestream_ready, manual_item, now_iso, recover_transcript, safe_name,
    save_transcript_text, youtube_id,
)

ROOT = Path(__file__).resolve().parents[1]
QUEUE_FILE = ROOT / "PRIORITY TRANSCRIPTS.txt"


def load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def read_manual() -> list[str]:
    if not QUEUE_FILE.exists():
        return []
    output, seen = [], set()
    for raw in QUEUE_FILE.read_text(encoding="utf-8-sig").splitlines():
        value = raw.strip()
        video = youtube_id(value)
        if value and not value.startswith("#") and video and video not in seen:
            seen.add(video)
            output.append(value)
    return output


def rewrite_manual(remove_ids: set[str]) -> None:
    lines = QUEUE_FILE.read_text(encoding="utf-8-sig").splitlines() if QUEUE_FILE.exists() else []
    kept = [line for line in lines if not youtube_id(line.strip()) or youtube_id(line.strip()) not in remove_ids]
    QUEUE_FILE.write_text("\n".join(kept).rstrip() + "\n", encoding="utf-8")


def select_queue(items: list[dict], limit: int, manual_count: int, radar_share: float) -> list[dict]:
    manual = [item for item in items if item.get("selection_source") == "manual_priority"]
    nonmanual = [item for item in items if item.get("selection_source") != "manual_priority"]
    available = max(0, limit - len(manual))
    archive = [item for item in nonmanual if item.get("selection_source") == "commi3_back_catalogue"]
    active = [item for item in nonmanual if item.get("selection_source") != "commi3_back_catalogue"]
    active_slots = min(len(active), round(available * radar_share))
    chosen = manual + active[:active_slots]
    remaining = available - active_slots
    chosen += archive[:remaining]
    remaining = max(0, limit - len(chosen))
    if remaining:
        chosen += active[active_slots:active_slots + remaining]
    # Preserve one discovery wildcard whenever an outsider sleeper exists.
    # It may replace the final non-manual candidate, but never a manual item.
    sleepers = [item for item in active if item.get("selection_source") == "sleeper_outsider"]
    if sleepers and not any(item.get("selection_source") == "sleeper_outsider" for item in chosen):
        replaceable = next(
            (index for index in range(len(chosen) - 1, -1, -1)
             if chosen[index].get("selection_source") != "manual_priority"),
            None,
        )
        if replaceable is not None:
            chosen[replaceable] = sleepers[0]
    return chosen


def main() -> int:
    cfg = config()
    parser = argparse.ArgumentParser(description="Priority, Radar, sleeper, Piper and archive transcript run.")
    parser.add_argument("--limit", type=int, default=int(cfg.get("run_limit", 20)))
    parser.add_argument("--refresh-channels", action="store_true")
    args = parser.parse_args()

    remaining = cooldown_remaining()
    if remaining > 0:
        print(f"YouTube cooldown remains active for about {max(1, round(remaining / 60))} minute(s).")
        return 2

    refresh(force=args.refresh_channels)
    manifest = build()
    existing = {str(item.get("video_id")): item for item in manifest.get("transcripts", [])}
    manual = read_manual()
    candidates = [item for item in assemble(manual) if item.get("video_id") not in existing]
    queue = select_queue(candidates, max(args.limit, len(manual)), len(manual), float(cfg.get("radar_share", .7)))
    retries = load(RETRIES, {})
    metadata = load(YOUTUBE_METADATA, {})
    now = datetime.now(timezone.utc)
    completed_manual, saved_count, unavailable = set(), 0, 0

    counts = {}
    for item in queue:
        label = item.get("selection_source", "unknown")
        counts[label] = counts.get(label, 0) + 1
    print(f"Queue: {len(queue)} videos; pace is {describe()}.")
    print("Queue mix: " + ", ".join(f"{key}={value}" for key, value in counts.items()))

    for number, item in enumerate(queue, 1):
        video = str(item.get("video_id") or "")
        retry = retries.get(video, {})
        next_retry = dt(retry.get("next_retry"))
        if next_retry and now < next_retry and item.get("selection_source") != "manual_priority":
            continue
        wait_before_video(f"video {number}/{len(queue)} [{video}]")
        old_selection = item.get("selection_source")
        generic_title = str(item.get("title") or "").startswith("Radar-recommended YouTube video [")
        unknown_source = str(item.get("source") or "").startswith("Unknown Radar-linked")
        if not item.get("title") or old_selection == "manual_priority" or generic_title or unknown_source:
            item.update(manual_item(video, str(item.get("url") or ""), metadata, "Priority Queue"))
            item["video_id"] = video
            item["selection_source"] = old_selection or "manual_priority"
            save(YOUTUBE_METADATA, metadata)
        published = dt(item.get("published"))
        ready, reason = livestream_ready(item, published, now, metadata)
        if not ready:
            print(f"[{number}/{len(queue)}] WAITING: {item.get('title')} - {reason}")
            clean_attempt()
            continue
        print(f"[{number}/{len(queue)}] {item.get('selection_source')}: {item.get('title')} [{video}]")
        try:
            segments, retrieval = recover_transcript(video)
            published = dt(item.get("published"))
            month = published.strftime("%Y-%m") if published else now.strftime("%Y-%m")
            path = TRANSCRIPTS_DIR / month / f"{safe_name(str(item.get('title') or ''))} [{video}].json"
            payload = {
                "video_id": video, "title": item.get("title"), "source": item.get("source"),
                "platform": "YouTube", "published": item.get("published"), "url": item.get("url"),
                "downloaded_at": now_iso(), "segment_count": len(segments), "retrieval": retrieval,
                "selection_source": item.get("selection_source"), "segments": segments,
            }
            save(path, payload)
            save_transcript_text(path, payload)
            retries.pop(video, None)
            saved_count += 1
            if item.get("selection_source") == "manual_priority":
                completed_manual.add(video)
            clean_attempt()
            print(f"  SAVED: {path.relative_to(ROOT)}")
        except DualIpBlocked as exc:
            until = dual_block(str(exc))
            retries[video] = {"status": "retry", "reason": "DualIpBlocked", "next_retry": until.isoformat()}
            save(RETRIES, retries)
            print(f"  BOTH ROUTES BLOCKED. Run stopped; cooldown until {until.isoformat()}.")
            break
        except Exception as exc:
            reason, message = type(exc).__name__, str(exc)
            attempts = int(retry.get("attempts", 0)) + 1
            permanent = reason in PERMANENT or attempts >= 3
            retries[video] = {
                "video_id": video, "title": item.get("title"), "reason": reason, "message": message,
                "attempts": attempts, "status": "unavailable" if permanent else "retry",
                "last_attempt": now_iso(),
                "next_retry": None if permanent else (now + timedelta(hours=(24, 72)[min(attempts - 1, 1)])).isoformat(),
            }
            if permanent:
                unavailable += 1
                if item.get("selection_source") == "manual_priority":
                    completed_manual.add(video)
            clean_attempt()  # No-caption and unavailable failures are not IP blocks.
            print(f"  {retries[video]['status'].upper()}: {reason}: {message}")

    if completed_manual:
        rewrite_manual(completed_manual)
    save(RETRIES, retries)
    build()
    print(f"Run complete: {saved_count} saved, {unavailable} unavailable, {len(read_manual())} manual priorities remain.")
    return 2 if cooldown_remaining() > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
