from __future__ import annotations

import os
import subprocess
import sys
import time
import traceback
from datetime import datetime

from radar_common import ROOT, load_json, save_json, path_for, now_iso


def log(message: str = "") -> None:
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}"
    print(line, flush=True)

    path = path_for("latest_log")
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def run_stage(name: str, command: list[str]) -> dict:
    log("")
    log("=" * 65)
    log(f"STARTING — {name}")
    log(f"COMMAND  — {' '.join(command)}")
    log("=" * 65)

    started = time.monotonic()

    process = subprocess.Popen(
        command,
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )

    assert process.stdout is not None

    for line in process.stdout:
        log(f"[{name}] {line.rstrip()}")

    return_code = process.wait()
    duration = round(time.monotonic() - started, 2)

    log(
        f"FINISHED — {name}: "
        f"{'SUCCESS' if return_code == 0 else 'FAILED'} "
        f"after {duration:.1f} seconds."
    )

    return {
        "name": name,
        "return_code": return_code,
        "duration_seconds": duration,
    }


def run_pipeline(title: str, stages: list[tuple[str, list[str]]]) -> int:
    path_for("latest_log").write_text("", encoding="utf-8")
    lock = path_for("lock")

    if lock.exists():
        log(f"Another run may be active. Lock file: {lock}")
        return 3

    save_json(lock, {"pid": os.getpid(), "started_at": now_iso()})
    results = []
    status = "HEALTHY"

    try:
        log("=" * 65)
        log(title)
        log(f"Project folder: {ROOT}")
        log("=" * 65)

        for name, command in stages:
            result = run_stage(name, command)
            results.append(result)

            if result["return_code"] not in (0, 2):
                status = "FAILED"
                break

        radar = load_json(path_for("radar"), [])
        transcript_index = load_json(
            path_for("transcript_index"),
            {"count": 0},
        )
        retries = load_json(path_for("transcript_retries"), {})
        cooldown = load_json(path_for("transcript_cooldown"), {})

        health = {
            "completed_at": now_iso(),
            "status": status,
            "run_type": title,
            "radar_entries": len(radar),
            "transcripts": transcript_index.get("count", 0),
            "pending_retries": sum(
                1
                for retry in retries.values()
                if retry.get("status") == "pending"
            ),
            "permanent_failures": sum(
                1
                for retry in retries.values()
                if retry.get("status") == "permanent_failure"
            ),
            "transcript_cooldown": cooldown,
            "stages": results,
        }

        save_json(path_for("health"), health)

        log("")
        log("=" * 65)
        log(f"FINAL STATUS: {status}")
        log(f"Radar entries: {health['radar_entries']}")
        log(f"Transcripts: {health['transcripts']}")
        log(
            f"Pending transcript retries: "
            f"{health['pending_retries']}"
        )
        log(
            f"Permanent transcript failures: "
            f"{health['permanent_failures']}"
        )

        if cooldown.get("active"):
            log(
                "Transcript cooldown active until: "
                f"{cooldown.get('until')}"
            )

        log("=" * 65)

        return 0 if status == "HEALTHY" else 1

    except Exception:
        log("FATAL ERROR")
        log(traceback.format_exc())
        return 1

    finally:
        try:
            lock.unlink(missing_ok=True)
            log("Lock released.")
        except Exception:
            pass
