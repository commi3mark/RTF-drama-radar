from __future__ import annotations

import os
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from common import (
    RECEIPTS_DIR,
    SOURCES_PATH,
    STATS_PATH,
    TRANSCRIPT_INDEX_PATH,
    load_json,
    now_iso,
    save_json,
)


def _source_count() -> tuple[int, int]:
    sources = load_json(SOURCES_PATH, [])
    return len(sources), sum(1 for source in sources if not source.get("disabled"))


def _transcript_health(last_run: dict, pending: int) -> tuple[str, str]:
    attempts = int(last_run.get("attempts", 0) or 0)
    saved = int(last_run.get("new_transcripts", 0) or 0)
    if bool(last_run.get("blocked")):
        return "stalled", "Transcript collection is blocked by YouTube."
    if attempts > 0 and saved == 0:
        return "stalled", "The latest transcript run made no progress."
    if pending > 0 or int(last_run.get("unavailable", 0) or 0) > 0:
        return "degraded", "Transcript collection is working with a remaining backlog."
    return "healthy", "Transcript collection is operating normally."


def _auto_transcripts_enabled() -> bool:
    if os.name != "nt":
        return False
    try:
        result = subprocess.run(
            ["schtasks", "/Query", "/TN", "Drama Radar - Auto Transcripts"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        return result.returncode == 0 and "Disabled" not in (result.stdout or "")
    except Exception:
        return False


def update_stats(
    *,
    radar: list[dict],
    source_results: dict | None = None,
    scan_run: dict | None = None,
    transcript_run: dict | None = None,
    archive_index: dict | None = None,
    validation_errors: list[str] | None = None,
) -> dict:
    current = load_json(STATS_PATH, {})
    source_results = source_results or current.get("sources", {})
    archive_index = archive_index or current.get("archive", {})
    validation_errors = validation_errors or []

    if scan_run is not None:
        current["last_scan"] = scan_run
    if transcript_run is not None:
        current["last_transcript_run"] = transcript_run

    platforms = Counter(str(item.get("platform", "Unknown")) for item in radar)
    transcript_index = load_json(TRANSCRIPT_INDEX_PATH, {"transcripts": []})
    total_transcripts = len(transcript_index.get("transcripts", []))
    youtube = [item for item in radar if item.get("youtube_id")]
    linked = sum(1 for item in youtube if item.get("transcript_status") == "available")
    unavailable = sum(
        1 for item in youtube
        if item.get("transcript_status") in {"unavailable", "permanent_failure"}
    )
    pending = max(0, len(youtube) - linked - unavailable)
    coverage = round((linked / len(youtube) * 100), 1) if youtube else 100.0

    configured, enabled = _source_count()
    source_results = {"configured": configured, "enabled": enabled, **source_results}

    last_scan = current.get("last_scan") or {}
    last_transcript = current.get("last_transcript_run") or {}
    transcript_status, transcript_summary = _transcript_health(last_transcript, pending)

    warnings: list[str] = []
    source_failures = int(source_results.get("failed", 0) or 0)
    if source_failures:
        warnings.append(f"{source_failures} source(s) failed during the latest scan.")
    if transcript_status == "stalled":
        warnings.append(transcript_summary)
    if pending:
        warnings.append(f"{pending} YouTube detections are awaiting transcripts.")

    if validation_errors:
        overall, summary = "failed", "Output validation failed."
    elif transcript_status == "stalled":
        overall, summary = "stalled", transcript_summary
    elif source_failures or transcript_status == "degraded":
        overall = "degraded"
        summary = warnings[0] if warnings else "Radar is working with reduced coverage."
    else:
        overall, summary = "healthy", "Radar is operating normally."

    stats = {
        "updated_at": now_iso(),
        "health": {
            "status": overall,
            "summary": summary,
            "scan": "degraded" if source_failures else "healthy",
            "transcripts": transcript_status,
            "archive": "healthy",
            "validation": "failed" if validation_errors else "healthy",
        },
        "last_scan": last_scan,
        "last_transcript_run": last_transcript,
        "detections": {
            "total": len(radar),
            "new_this_run": int(last_scan.get("new_detections", 0) or 0),
            "by_platform": dict(platforms),
        },
        "transcripts": {
            "downloaded_total": total_transcripts,
            "downloaded_this_run": int(last_transcript.get("new_transcripts", 0) or 0),
            "linked_to_live_feed": linked,
            "awaiting_collection": pending,
            "unavailable": unavailable,
            "coverage_percent": coverage,
            "retry_queue": int(last_transcript.get("retry_count", 0) or 0),
            "automatic_recovery_enabled": _auto_transcripts_enabled(),
        },
        "sources": source_results,
        "archive": archive_index,
        "warnings": warnings,
        "errors": validation_errors,
    }
    save_json(STATS_PATH, stats)
    return stats


def _lines_for_items(title: str, items: list[dict], empty: str) -> list[str]:
    lines = ["", title]
    if not items:
        lines.append(f"  {empty}")
        return lines
    for item in items:
        source = item.get("source") or "Unknown source"
        name = item.get("title") or item.get("video_id") or "Untitled"
        reason = item.get("reason")
        suffix = f" ({reason})" if reason else ""
        lines.append(f"  - {source}: {name}{suffix}")
    return lines


def _next_action(stats: dict) -> str:
    health = (stats.get("health") or {}).get("status", "unknown")
    transcripts = stats.get("transcripts") or {}
    last_transcript = stats.get("last_transcript_run") or {}
    sources = stats.get("sources") or {}
    if health == "failed":
        return "Investigate the validation errors above before the next sweep."
    if int(sources.get("failed", 0) or 0) > 0:
        return "Investigate the failed source feed(s)."
    if bool(last_transcript.get("blocked")):
        return "Wait for the YouTube cooldown to expire."
    if int(transcripts.get("awaiting_collection", 0) or 0) > 0:
        if transcripts.get("automatic_recovery_enabled"):
            return "None required — automatic transcript recovery will continue hourly."
        return "Run GET TRANSCRIPTS on Stalinvo."
    return "None required."


def _human_duration(seconds: float | int | None) -> str:
    total = max(0, int(round(float(seconds or 0))))
    minutes, secs = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes}m {secs}s"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def _archive_summary(archive: dict) -> list[str]:
    months = archive.get("months", []) if isinstance(archive, dict) else []
    if not isinstance(months, list):
        months = []
    month_count = int(archive.get("month_count", len(months)) or len(months))
    total = int(
        archive.get("total_archived_detections", archive.get("total_items", archive.get("total_detections", 0)))
        or 0
    )
    latest = max(months, key=lambda row: str(row.get("month", "")), default={})
    lines = [
        "",
        "ARCHIVE",
        f"  Months archived: {month_count}",
        f"  Total detections: {total}",
    ]
    if latest:
        lines.append(f"  Latest month: {latest.get('month')} ({latest.get('items', 0)} detections)")
    return lines


def render_summary(stats: dict, label: str = "DRAMA RADAR SWEEP COMPLETE") -> str:
    scan = stats.get("last_scan") or {}
    transcript_run = stats.get("last_transcript_run") or {}
    health = stats.get("health") or {}
    transcript_stats = stats.get("transcripts") or {}
    sources = stats.get("sources") or {}
    archive = stats.get("archive") or {}
    results = transcript_run.get("results") or {}

    duration = transcript_run.get("duration_seconds")
    if duration is None:
        duration = scan.get("duration_seconds", 0)
    sweep = scan.get("sweep_number")
    status = str(health.get("status", "unknown")).upper()

    lines = ["=" * 72, label]
    if sweep:
        lines.append(f"SWEEP #{sweep}")
    lines.extend([
        "=" * 72,
        "",
        "STATUS",
        f"  [{status}]",
        f"  Reason: {health.get('summary', '')}",
        "",
        "LAST RADAR SCAN",
        f"  New detections: {int(scan.get('new_detections', 0) or 0):+d}",
        f"  Sources online: {sources.get('successful', 0)} / {sources.get('checked', sources.get('enabled', 0))}",
        "",
        "LAST TRANSCRIPT RUN",
        f"  New transcripts: {int(transcript_run.get('new_transcripts', 0) or 0):+d}",
        f"  Attempts: {transcript_run.get('attempts', 0)}",
        f"  Unavailable: {transcript_run.get('unavailable', 0)}",
        f"  Retry later: {transcript_run.get('retry_count', 0)}",
        "",
        "LIVE DATABASE",
        f"  Detections: {stats.get('detections', {}).get('total', 0)}",
        f"  Transcripts: {transcript_stats.get('downloaded_total', 0)}",
        f"  Coverage: {transcript_stats.get('coverage_percent', 0)}%",
        f"  Awaiting transcripts: {transcript_stats.get('awaiting_collection', 0)}",
    ])
    lines.extend(_archive_summary(archive))
    lines.extend(_lines_for_items("NEW TRANSCRIPTS", results.get("saved", []), "None this run."))
    lines.extend(_lines_for_items("UNAVAILABLE", results.get("unavailable", []), "None this run."))
    lines.extend(_lines_for_items("RETRY LATER", results.get("retry", []), "None this run."))

    if stats.get("warnings"):
        lines.extend(["", "WARNINGS"])
        lines.extend(f"  - {warning}" for warning in stats["warnings"])
    if stats.get("errors"):
        lines.extend(["", "ERRORS"])
        lines.extend(f"  - {error}" for error in stats["errors"])

    lines.extend([
        "",
        "NEXT ACTION",
        f"  {_next_action(stats)}",
        "",
        "RUNTIME",
        f"  {_human_duration(duration)}",
        "=" * 72,
    ])
    return "\n".join(lines)


def write_receipt(stats: dict, kind: str, label: str) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")
    path = RECEIPTS_DIR / f"{stamp}_{kind}.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_summary(stats, label) + "\n", encoding="utf-8")
    return path


def print_summary(stats: dict, label: str = "DRAMA RADAR SWEEP COMPLETE", receipt_kind: str | None = None) -> None:
    print("\n" + render_summary(stats, label))
    if receipt_kind:
        path = write_receipt(stats, receipt_kind, label)
        print("Receipt saved:")
        print(f"  {path.parent.name}\\{path.name}")
