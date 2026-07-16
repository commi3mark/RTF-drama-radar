from __future__ import annotations

import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BRAIN = ROOT / "radar" / "brain"
LOCK = BRAIN / "auto-transcripts.lock"
LOG = BRAIN / "auto-transcripts.log"
GETTER = ROOT / "transcripts" / "get_transcripts.py"
SYNC = ROOT / "transcripts" / "github_sync.py"
MAX_LOCK_AGE_SECONDS = 3 * 60 * 60


def stamp() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


def log(message: str) -> None:
    BRAIN.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(f"[{stamp()}] {message}\n")


def acquire_lock() -> bool:
    BRAIN.mkdir(parents=True, exist_ok=True)
    if LOCK.exists():
        age = time.time() - LOCK.stat().st_mtime
        if age < MAX_LOCK_AGE_SECONDS:
            log("Skipped: another transcript recovery run appears to be active.")
            return False
        log("Removed stale transcript recovery lock.")
        LOCK.unlink(missing_ok=True)
    try:
        LOCK.write_text(str(time.time()), encoding="utf-8")
        return True
    except OSError as exc:
        log(f"Could not create lock: {exc}")
        return False


def main() -> int:
    if not acquire_lock():
        return 0

    log("Automatic transcript recovery started.")
    try:
        with LOG.open("a", encoding="utf-8") as handle:
            pull = subprocess.run(
                [sys.executable, str(SYNC), "pull"],
                cwd=ROOT,
                stdout=handle,
                stderr=subprocess.STDOUT,
                text=True,
                check=False,
            )
            if pull.returncode != 0:
                log("GitHub pull skipped or failed; continuing with the local feed.")

            result = subprocess.run(
                [sys.executable, str(GETTER), "--limit", "10"],
                cwd=ROOT,
                stdout=handle,
                stderr=subprocess.STDOUT,
                text=True,
                check=False,
            )

            if result.returncode == 0:
                push = subprocess.run(
                    [sys.executable, str(SYNC), "push"],
                    cwd=ROOT,
                    stdout=handle,
                    stderr=subprocess.STDOUT,
                    text=True,
                    check=False,
                )
                if push.returncode != 0:
                    log("Transcript recovery worked, but GitHub upload failed.")

        log(f"Automatic transcript recovery finished with exit code {result.returncode}.")
        return result.returncode
    except Exception as exc:
        log(f"Automatic transcript recovery failed: {exc}")
        return 1
    finally:
        LOCK.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
