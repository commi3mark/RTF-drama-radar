#!/usr/bin/env python3
"""
Scheduled Drama Radar pipeline.

Sequence:
1. Run the existing collection/orchestration script.
2. File new transcript JSON files into transcripts/YYYY/MM.
3. Rebuild intelligence indexes.
4. Commit and push only when tracked output changed.

This version streams child-process output live instead of holding it until
the child finishes.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parent
LOG_DIR = ROOT / "logs"
LOCK_PATH = ROOT / ".scheduled-pipeline.lock"
PYTHON = sys.executable

COLLECTOR_CANDIDATES = (
    "run_drama_radar.py",
    "drama_radar.py",
)

PIPELINE_STEPS = (
    ("Archive transcripts", [PYTHON, "archive_transcripts.py", "--apply"]),
    ("Build intelligence", [PYTHON, "build_intelligence.py"]),
)


def log(message: str, fh) -> None:
    line = f"[{datetime.now().astimezone().isoformat(timespec='seconds')}] {message}"
    print(line, flush=True)
    fh.write(line + "\n")
    fh.flush()


def run(
    command: Sequence[str],
    fh,
    *,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run a command and stream combined stdout/stderr live."""
    command_list = list(command)
    log("RUN " + subprocess.list2cmdline(command_list), fh)

    process = subprocess.Popen(
        command_list,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        errors="replace",
        bufsize=1,
    )

    captured: list[str] = []

    assert process.stdout is not None
    for line in process.stdout:
        captured.append(line)
        print(line, end="", flush=True)
        fh.write(line)
        fh.flush()

    return_code = process.wait()
    stdout = "".join(captured)

    log(f"EXIT {return_code}", fh)

    result = subprocess.CompletedProcess(
        args=command_list,
        returncode=return_code,
        stdout=stdout,
        stderr=None,
    )

    if check and return_code != 0:
        raise subprocess.CalledProcessError(return_code, command_list)

    return result


def acquire_lock(fh) -> bool:
    if LOCK_PATH.exists():
        try:
            payload = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
            created = float(payload.get("created_epoch", 0))
        except Exception:
            created = 0

        age = time.time() - created
        if 0 <= age < 3 * 60 * 60:
            log(
                f"Another run appears active; lock age is {age/60:.1f} minutes. Exiting.",
                fh,
            )
            return False

        log("Removing stale lock file.", fh)
        LOCK_PATH.unlink(missing_ok=True)

    LOCK_PATH.write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "created": datetime.now().astimezone().isoformat(),
                "created_epoch": time.time(),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return True


def find_collector() -> str:
    for candidate in COLLECTOR_CANDIDATES:
        if (ROOT / candidate).exists():
            return candidate
    raise FileNotFoundError(
        "No collector found. Expected one of: " + ", ".join(COLLECTOR_CANDIDATES)
    )


def git_has_changes(fh) -> bool:
    result = run(["git", "status", "--porcelain"], fh, check=True)
    return bool((result.stdout or "").strip())


def main() -> int:
    LOG_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    log_path = LOG_DIR / f"scheduled-run-{stamp}.log"

    with log_path.open("a", encoding="utf-8", newline="\n") as fh:
        log("Drama Radar scheduled run started.", fh)

        if not acquire_lock(fh):
            return 0

        try:
            collector = find_collector()
            steps = (("Collect new material", [PYTHON, collector]),) + PIPELINE_STEPS

            for name, command in steps:
                log(f"STEP: {name}", fh)
                run(command, fh)

            inside = run(
                ["git", "rev-parse", "--is-inside-work-tree"],
                fh,
                check=False,
            )
            if inside.returncode != 0 or (inside.stdout or "").strip().lower() != "true":
                log("Not inside a Git repository; skipping commit and push.", fh)
                return 0

            if not git_has_changes(fh):
                log("No repository changes detected; nothing to commit.", fh)
                return 0

            run(["git", "add", "-A"], fh)

            commit_message = (
                "Automated Drama Radar update "
                + datetime.now().astimezone().strftime("%Y-%m-%d %H:%M")
            )
            commit = run(["git", "commit", "-m", commit_message], fh, check=False)

            if commit.returncode != 0:
                status = run(["git", "status", "--porcelain"], fh, check=False)
                if not (status.stdout or "").strip():
                    log("Nothing remained to commit.", fh)
                    return 0
                raise subprocess.CalledProcessError(commit.returncode, commit.args)

            run(["git", "push"], fh)
            log("Changes committed and pushed successfully.", fh)
            return 0

        except Exception as exc:
            log(f"FAILED: {type(exc).__name__}: {exc}", fh)
            return 1
        finally:
            LOCK_PATH.unlink(missing_ok=True)
            log(f"Run finished. Log: {log_path}", fh)


if __name__ == "__main__":
    raise SystemExit(main())
