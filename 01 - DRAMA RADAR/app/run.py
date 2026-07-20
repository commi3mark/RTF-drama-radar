from __future__ import annotations
import sys, time
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from common import RADAR_PATH, BRAIN_DIR, load_json, save_json, now_iso
from scan import scan_sources
from archive import update_archive
from validate import validate
from stats import update_stats, print_summary
from console import stage

ROLLING_DAYS = 30


def main() -> int:
    started = time.time()
    lock = BRAIN_DIR / "radar.lock"
    if lock.exists():
        print("Radar is already running. Remove radar/brain/radar.lock only if no run is active.")
        return 1
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(now_iso(), encoding="utf-8")
    try:
        counter_path = BRAIN_DIR / "sweep-count.json"
        counter = load_json(counter_path, {"count": 0})
        sweep_number = int(counter.get("count", 0) or 0) + 1
        save_json(counter_path, {"count": sweep_number, "updated_at": now_iso()})

        print("=" * 72)
        print(f"DRAMA RADAR v2 — BEGINNING SWEEP #{sweep_number}")
        print("=" * 72)
        stage(1, 5, "Loading existing radar")
        existing = load_json(RADAR_PATH, [])
        old_by_id = {x.get("id"): x for x in existing if isinstance(x, dict) and x.get("id")}

        stage(2, 5, "Scanning sources")
        found, source_results = scan_sources()
        new_count = 0
        for fresh in found:
            previous = old_by_id.get(fresh["id"])
            if previous is None:
                old_by_id[fresh["id"]] = fresh
                new_count += 1
            else:
                preserve = {k: previous.get(k) for k in (
                    "discovered_at", "transcript_status", "transcript_path", "transcript_url"
                ) if previous.get(k) is not None}
                old_by_id[fresh["id"]] = {**fresh, **preserve}

        cutoff = datetime.now(timezone.utc) - timedelta(days=ROLLING_DAYS)
        live = []
        for item in old_by_id.values():
            try:
                published = datetime.fromisoformat(str(item.get("published")))
                if published.tzinfo is None:
                    published = published.replace(tzinfo=timezone.utc)
                if published >= cutoff:
                    live.append(item)
            except Exception:
                live.append(item)
        live.sort(key=lambda x: str(x.get("published") or ""), reverse=True)
        save_json(RADAR_PATH, live)

        stage(3, 5, "Updating archive")
        archive_index = update_archive(list(old_by_id.values()))

        stage(4, 5, "Validating outputs")
        errors = validate(live)

        stage(5, 5, "Building control panel")
        scan_run = {
            "runner": "github" if __import__('os').environ.get('GITHUB_ACTIONS') else "local",
            "started_at": datetime.fromtimestamp(started, timezone.utc).isoformat(),
            "finished_at": now_iso(),
            "duration_seconds": round(time.time() - started, 2),
            "new_detections": new_count,
            "sweep_number": sweep_number,
            "status": "failed" if errors else ("warning" if source_results.get("failed") else "healthy"),
        }
        stats = update_stats(
            radar=live,
            source_results=source_results,
            scan_run=scan_run,
            archive_index=archive_index,
            validation_errors=errors,
        )
        save_json(BRAIN_DIR / "last-run.json", scan_run)
        print_summary(stats, "DRAMA RADAR SWEEP COMPLETE", receipt_kind="scan")
        return 1 if errors else 0
    finally:
        lock.unlink(missing_ok=True)

if __name__ == "__main__":
    raise SystemExit(main())
