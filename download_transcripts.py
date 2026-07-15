from __future__ import annotations

import argparse
import random
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from youtube_transcript_api import YouTubeTranscriptApi

from radar_common import (
    ROOT,
    load_json,
    save_json,
    settings,
    path_for,
    safe_filename,
    now_iso,
)


DEFAULT_RETRY_DELAYS_HOURS = [1, 2, 4, 8, 16, 24, 48, 96, 168]
DEFAULT_BLOCK_COOLDOWNS_HOURS = [1, 3, 8]


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None

    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def exception_name(exc: Exception) -> str:
    return type(exc).__name__


def due(retry: dict) -> bool:
    next_retry = parse_datetime(retry.get("next_retry"))
    return next_retry is None or datetime.now(timezone.utc) >= next_retry


def cooldown_state() -> dict:
    return load_json(path_for("transcript_cooldown"), {})


def cooldown_active() -> tuple[bool, datetime | None, str | None]:
    state = cooldown_state()
    until = parse_datetime(state.get("until"))

    if until and datetime.now(timezone.utc) < until:
        return True, until, state.get("reason")

    return False, until, state.get("reason")


def block_count_within_window(state: dict, hours: float = 24) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    events = state.get("block_events", [])
    count = 0

    for value in events:
        parsed = parse_datetime(value)
        if parsed and parsed >= cutoff:
            count += 1

    return count


def set_global_cooldown(reason: str, cfg: dict) -> datetime:
    state = cooldown_state()
    now = datetime.now(timezone.utc)

    recent_events = []
    for value in state.get("block_events", []):
        parsed = parse_datetime(value)
        if parsed and parsed >= now - timedelta(hours=24):
            recent_events.append(parsed.isoformat())

    recent_events.append(now.isoformat())

    configured = cfg.get(
        "transcript_block_cooldowns_hours",
        DEFAULT_BLOCK_COOLDOWNS_HOURS,
    )
    cooldowns = [float(value) for value in configured] or DEFAULT_BLOCK_COOLDOWNS_HOURS
    block_index = min(len(recent_events) - 1, len(cooldowns) - 1)
    hours = cooldowns[block_index]
    until = now + timedelta(hours=hours)

    save_json(
        path_for("transcript_cooldown"),
        {
            "active": True,
            "reason": reason,
            "started_at": now.isoformat(),
            "until": until.isoformat(),
            "cooldown_hours": hours,
            "block_events": recent_events,
            "blocks_in_last_24_hours": len(recent_events),
        },
    )

    return until


def clear_expired_cooldown() -> None:
    state = cooldown_state()
    until = parse_datetime(state.get("until"))

    if state and (until is None or datetime.now(timezone.utc) >= until):
        save_json(
            path_for("transcript_cooldown"),
            {
                "active": False,
                "reason": state.get("reason"),
                "started_at": state.get("started_at"),
                "until": state.get("until"),
                "cleared_at": now_iso(),
                "block_events": state.get("block_events", []),
                "blocks_in_last_24_hours": block_count_within_window(state),
            },
        )


def transcript_map() -> dict[str, str]:
    result: dict[str, str] = {}
    transcript_root = path_for("transcripts")

    for path in transcript_root.rglob("*.json"):
        try:
            data = load_json(path, {})
            video_id = data.get("video_id") or data.get("youtube_id")

            if video_id:
                result[str(video_id)] = str(
                    path.relative_to(transcript_root.parent)
                ).replace("\\", "/")
        except Exception:
            continue

    return result


def source_config_map() -> dict[tuple[str, str], dict]:
    sources = load_json(ROOT / "config" / "sources.json", [])

    return {
        (
            str(source.get("name", "")),
            str(source.get("platform", "")),
        ): source
        for source in sources
    }


def fetch_segments(video_id: str) -> list[dict]:
    fetched = YouTubeTranscriptApi().fetch(video_id)

    return [
        {
            "text": item.text,
            "start": round(float(item.start), 3),
            "duration": round(float(item.duration), 3),
        }
        for item in fetched
    ]


def video_age_hours(item: dict) -> float | None:
    published = parse_datetime(item.get("published"))

    if published is None:
        return None

    return max(
        0.0,
        (datetime.now(timezone.utc) - published).total_seconds() / 3600.0,
    )


def priority_for(item: dict, source_configs: dict) -> int:
    source = source_configs.get(
        (
            str(item.get("source", "")),
            str(item.get("platform", "")),
        ),
        {},
    )
    return int(source.get("priority", 50))


def transcript_delay_for(item: dict, source_configs: dict, cfg: dict) -> float:
    source = source_configs.get(
        (
            str(item.get("source", "")),
            str(item.get("platform", "")),
        ),
        {},
    )
    return float(
        source.get(
            "transcript_delay_hours",
            cfg.get("default_transcript_delay_hours", 6),
        )
    )


def eligible_for_attempt(
    item: dict,
    source_configs: dict,
    cfg: dict,
) -> tuple[bool, str]:
    age = video_age_hours(item)

    if age is None:
        return True, "published time unavailable"

    grace_period = transcript_delay_for(item, source_configs, cfg)

    if age < grace_period:
        return (
            False,
            f"only {age:.1f}h old; grace period is {grace_period:.1f}h",
        )

    return True, f"{age:.1f}h old"


def retry_delay_hours(attempt_number: int, cfg: dict) -> float:
    values = cfg.get(
        "transcript_retry_delays_hours",
        DEFAULT_RETRY_DELAYS_HOURS,
    )
    delays = [float(value) for value in values] or DEFAULT_RETRY_DELAYS_HOURS
    index = min(max(attempt_number - 1, 0), len(delays) - 1)
    return delays[index]


def unavailable_retry_hours(reason: str, attempt_number: int, cfg: dict) -> float:
    if reason == "TranscriptsDisabled":
        disabled_schedule = cfg.get(
            "transcripts_disabled_retry_hours",
            [24, 72, 168],
        )
        delays = [float(value) for value in disabled_schedule]
        index = min(max(attempt_number - 1, 0), len(delays) - 1)
        return delays[index]

    if reason in {"VideoUnavailable", "CouldNotRetrieveTranscript"}:
        unavailable_schedule = cfg.get(
            "video_unavailable_retry_hours",
            [6, 24, 72],
        )
        delays = [float(value) for value in unavailable_schedule]
        index = min(max(attempt_number - 1, 0), len(delays) - 1)
        return delays[index]

    return retry_delay_hours(attempt_number, cfg)


def max_attempts_for(reason: str, cfg: dict) -> int:
    if reason == "TranscriptsDisabled":
        return int(cfg.get("transcripts_disabled_max_attempts", 3))

    if reason in {"VideoUnavailable", "CouldNotRetrieveTranscript"}:
        return int(cfg.get("video_unavailable_max_attempts", 3))

    return int(cfg.get("transcript_max_attempts", 10))


def candidate_sort_key(
    item: dict,
    retry: dict,
    source_configs: dict,
) -> tuple:
    # Due retries first by scheduled time, then priority, then publication time.
    next_retry = parse_datetime(retry.get("next_retry"))
    due_time = next_retry or datetime.min.replace(tzinfo=timezone.utc)
    published = (
        parse_datetime(item.get("published"))
        or datetime.min.replace(tzinfo=timezone.utc)
    )

    return (
        due_time.timestamp(),
        -priority_for(item, source_configs),
        -published.timestamp(),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--ignore-cooldown",
        action="store_true",
        help="Attempt transcripts even when the global cooldown is active.",
    )
    args = parser.parse_args()

    cfg = settings()

    active, until, reason = cooldown_active()

    if active and not args.ignore_cooldown:
        print("Transcript stage skipped: global YouTube cooldown is active.")
        print(f"Cooldown reason: {reason or 'unknown'}")
        print(f"Cooldown ends:   {until.isoformat() if until else 'unknown'}")
        return 0

    clear_expired_cooldown()

    radar = load_json(path_for("radar"), [])
    retries = load_json(path_for("transcript_retries"), {})
    archived = transcript_map()
    source_configs = source_config_map()

    # Conservative defaults. Config can raise these deliberately later.
    limit = int(cfg.get("transcripts_per_run", 3))
    per_source_limit = int(cfg.get("transcripts_per_source_per_run", 1))
    recovery_limit = int(cfg.get("transcript_recovery_probe_limit", 1))

    cooldown = cooldown_state()
    recently_cleared = bool(cooldown.get("cleared_at")) and not cooldown.get("active")
    if recently_cleared:
        limit = min(limit, recovery_limit)

    attempts = 0
    saved = 0
    skipped = 0
    deferred = 0
    failed = 0
    attempted_by_source: dict[str, int] = defaultdict(int)

    candidates = [
        item
        for item in radar
        if item.get("platform") == "YouTube"
        and item.get("youtube_id")
    ]

    queued: list[tuple[dict, dict, str]] = []

    for item in candidates:
        video_id = str(item["youtube_id"])

        if video_id in archived:
            skipped += 1
            continue

        retry = retries.get(video_id, {})

        if retry.get("status") in {"permanent_failure", "unavailable"}:
            skipped += 1
            continue

        eligible, eligibility_reason = eligible_for_attempt(
            item,
            source_configs,
            cfg,
        )

        if not eligible:
            deferred += 1
            continue

        if retry and not due(retry):
            skipped += 1
            continue

        queued.append((item, retry, eligibility_reason))

    queued.sort(
        key=lambda row: candidate_sort_key(
            row[0],
            row[1],
            source_configs,
        )
    )

    print(f"Found {len(candidates)} YouTube items.")
    print(f"Eligible queue: {len(queued)}")
    print(f"Per-run fetch cap: {limit}")
    print(f"Per-source cap: {per_source_limit}")
    if recently_cleared:
        print("Recovery mode: one probe request after an expired block cooldown.")

    for item, retry, eligibility_reason in queued:
        if attempts >= limit:
            print("Transcript attempt cap reached; remaining work resumes next run.")
            break

        source_name = str(item.get("source") or "Unknown source")
        if attempted_by_source[source_name] >= per_source_limit:
            continue

        video_id = str(item["youtube_id"])
        title = item.get("title") or video_id
        source_priority = priority_for(item, source_configs)

        attempts += 1
        attempted_by_source[source_name] += 1

        print(
            f"[{attempts}/{limit}] FETCHING: {title} "
            f"[{source_name}; priority {source_priority}; {eligibility_reason}]"
        )

        delay = random.uniform(
            float(cfg.get("request_delay_min_seconds", 20)),
            float(cfg.get("request_delay_max_seconds", 30)),
        )
        print(f"Waiting {delay:.1f} seconds before request...")
        time.sleep(delay)

        try:
            segments = fetch_segments(video_id)
            published = parse_datetime(item.get("published"))
            destination_date = published or datetime.now(timezone.utc)

            folder = (
                path_for("transcripts")
                / f"{destination_date.year:04d}"
                / f"{destination_date.month:02d}"
            )
            path = folder / f"{safe_filename(title)} [{video_id}].json"

            save_json(
                path,
                {
                    "video_id": video_id,
                    "title": title,
                    "source": item.get("source"),
                    "url": item.get("url"),
                    "published": item.get("published"),
                    "downloaded_at": now_iso(),
                    "segment_count": len(segments),
                    "segments": segments,
                },
            )

            archived[video_id] = str(
                path.relative_to(path_for("transcripts").parent)
            ).replace("\\", "/")
            retries.pop(video_id, None)
            saved += 1

            print(f"SAVED: {len(segments)} segments -> {path}")

        except Exception as exc:
            failed += 1
            reason = exception_name(exc)
            attempt_number = int(retry.get("attempts", 0)) + 1
            configured_permanent = reason in set(
                cfg.get("permanent_failures", [])
            )

            max_attempts = max_attempts_for(reason, cfg)
            exhausted = attempt_number >= max_attempts
            status = (
                "permanent_failure"
                if configured_permanent
                else "unavailable"
                if exhausted
                else "pending"
            )

            retry_hours = unavailable_retry_hours(
                reason,
                attempt_number,
                cfg,
            )

            retries[video_id] = {
                "video_id": video_id,
                "title": title,
                "source": item.get("source"),
                "priority": source_priority,
                "reason": reason,
                "message": str(exc),
                "attempts": attempt_number,
                "last_attempt": now_iso(),
                "status": status,
                "next_retry": None
                if status != "pending"
                else (
                    datetime.now(timezone.utc)
                    + timedelta(hours=retry_hours)
                ).isoformat(),
                "retry_note": (
                    "attempt-based exponential schedule"
                    if status == "pending"
                    else f"attempt limit reached ({max_attempts})"
                ),
            }

            print(f"FAILED: {title}")
            print(f"Reason: {reason}")
            print(f"Status: {status}")

            if status == "pending":
                print(
                    f"Next retry in {retry_hours:g} hours "
                    f"(attempt {attempt_number}/{max_attempts})."
                )

            save_json(path_for("transcript_retries"), retries)

            if (
                reason in {"IpBlocked", "RequestBlocked"}
                and cfg.get("stop_on_ip_block", True)
            ):
                until = set_global_cooldown(reason, cfg)
                print(
                    "YouTube rate block detected. "
                    "Adaptive transcript cooldown activated."
                )
                print(f"No transcript requests until: {until.isoformat()}")
                break

    save_json(path_for("transcript_retries"), retries)

    print()
    print("Transcript download complete.")
    print(f"Attempts: {attempts}")
    print(f"Saved:    {saved}")
    print(f"Deferred: {deferred}")
    print(f"Skipped:  {skipped}")
    print(f"Failed:   {failed}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
