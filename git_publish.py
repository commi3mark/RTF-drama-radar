from __future__ import annotations

import subprocess
from datetime import datetime, timezone

from radar_common import ROOT, settings


PUBLISH_PATHS = [
    "data",
    "archive",
    "transcripts",
    "intelligence/evidence-packets",
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
        repository_root = run_git(
            "rev-parse",
            "--show-toplevel",
            capture=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("Git publishing skipped: this folder is not a Git repository.")
        return 0

    if not repository_root:
        print("Git publishing skipped: repository root could not be resolved.")
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

    existing_paths = [
        path for path in PUBLISH_PATHS if (ROOT / path).exists()
    ]
    if not existing_paths:
        print("Git publishing skipped: no generated output paths exist.")
        return 0

    run_git("add", "--", *existing_paths)

    changed = run_git("diff", "--cached", "--name-only", capture=True)
    if not changed:
        print("Git publishing: no generated changes to upload.")
        return 0

    prefix = str(
        cfg.get("commit_message_prefix", "Automated Drama Radar update")
    ).strip()
    timestamp_text = datetime.now(timezone.utc).strftime(
        "%Y-%m-%d %H:%M UTC"
    )
    message = f"{prefix} — {timestamp_text}"

    run_git("commit", "-m", message)
    run_git("push", remote, branch)

    changed_count = len(
        [line for line in changed.splitlines() if line.strip()]
    )
    print(
        f"Git publishing complete: pushed {changed_count} changed files."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
