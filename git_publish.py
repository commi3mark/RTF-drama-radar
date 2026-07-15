from __future__ import annotations

import subprocess
from datetime import datetime, timezone

from radar_common import ROOT, settings


PUBLISH_PATHS = [
    "data",
    "archive",
    "transcripts",
    "intelligence/evidence-packets",
    "intelligence/preanalysis",
    "intelligence/auto",
    "intelligence/episodes",
    "intelligence/people",
    "intelligence/shows",
    "intelligence/stories",
    "intelligence/factions",
    "intelligence/claims",
    "intelligence/quotes",
    "intelligence/processed",
    "intelligence/rejected",
    "intelligence/state.json",
    "state/transcript-retries.json",
    "logs/health.json",
]


def run_git(*args: str, capture: bool = False) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.STDOUT if capture else None,
    )
    return (completed.stdout or "").strip()


def main() -> int:
    cfg = settings().get("git", {})

    if not cfg.get("enabled", False):
        print("Git publishing is disabled in config/settings.json.")
        return 0

    try:
        run_git("rev-parse", "--show-toplevel", capture=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("Git publishing skipped: this folder is not a Git repository.")
        return 0

    branch = str(cfg.get("branch", "main")).strip() or "main"
    remote = str(cfg.get("remote", "origin")).strip() or "origin"

    try:
        run_git("pull", "--ff-only", remote, branch)
    except subprocess.CalledProcessError:
        print(
            "Git publishing stopped: remote changes exist or pull failed. "
            "No files were committed or pushed."
        )
        return 1

    existing_paths = [path for path in PUBLISH_PATHS if (ROOT / path).exists()]
    run_git("add", "--", *existing_paths)

    changed = run_git("diff", "--cached", "--name-only", capture=True)
    if not changed:
        print("Git publishing: no generated changes to upload.")
        return 0

    prefix = str(
        cfg.get("commit_message_prefix", "Automated Drama Radar update")
    ).strip()
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    run_git("commit", "-m", f"{prefix} — {timestamp}")
    run_git("push", remote, branch)

    count = len([line for line in changed.splitlines() if line.strip()])
    print(f"Git publishing complete: pushed {count} changed files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
