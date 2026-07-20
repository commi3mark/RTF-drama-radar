from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

GRABBER_ROOT = Path(__file__).resolve().parents[1]
SYSTEM_ROOT = GRABBER_ROOT.parent
ROOT = SYSTEM_ROOT
BRAIN = GRABBER_ROOT / "state"
MIRROR = BRAIN / "github-sync"
REMOTE = "https://github.com/commi3mark/RTF-drama-radar.git"
BRANCH = "main"


def run_git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=MIRROR if MIRROR.exists() else ROOT,
        text=True,
        check=check,
    )


def ensure_mirror() -> bool:
    BRAIN.mkdir(parents=True, exist_ok=True)
    if (MIRROR / ".git").exists():
        return True
    if MIRROR.exists():
        shutil.rmtree(MIRROR)
    print("Creating private GitHub sync mirror...")
    result = subprocess.run(
        ["git", "clone", "--branch", BRANCH, "--single-branch", REMOTE, str(MIRROR)],
        cwd=ROOT,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        print("GitHub sync setup failed. Git may require you to sign in once.")
        return False
    return True


def update_mirror() -> bool:
    if not ensure_mirror():
        return False
    result = run_git("pull", "--rebase", check=False)
    if result.returncode != 0:
        print("Could not update the GitHub sync mirror.")
        return False
    return True


def copy_file(source: Path, destination: Path) -> None:
    if not source.exists():
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def pull_to_local() -> int:
    if not update_mirror():
        return 1
    copy_file(
        MIRROR / "transcripts" / "selected-transcripts.txt",
        GRABBER_ROOT / "config" / "selected-transcripts.txt",
    )
    print("GitHub transcript selections synced to Stalinvo.")
    return 0


def push_from_local() -> int:
    if not update_mirror():
        return 1

    copy_file(
        GRABBER_ROOT / "config" / "selected-transcripts.txt",
        MIRROR / "transcripts" / "selected-transcripts.txt",
    )

    local_transcripts = GRABBER_ROOT / "transcripts"
    remote_transcripts = MIRROR / "transcripts" / "archive"
    if local_transcripts.exists():
        shutil.copytree(local_transcripts, remote_transcripts, dirs_exist_ok=True)

    for name in ["transcript-manifest.json", "transcript-index.json"]:
        copy_file(GRABBER_ROOT / "transcripts" / name, MIRROR / "transcripts" / name)

    run_git(
        "add",
        "transcripts/selected-transcripts.txt",
        "transcripts/archive",
        "transcripts/transcript-manifest.json",
        "transcripts/transcript-index.json",
        check=False,
    )
    changed = subprocess.run(
        ["git", "diff", "--cached", "--quiet"],
        cwd=MIRROR,
        check=False,
    ).returncode != 0
    if not changed:
        print("GitHub upload: no transcript changes to publish.")
        return 0

    count = len(list(local_transcripts.rglob("*.json"))) if local_transcripts.exists() else 0
    if run_git("commit", "-m", f"Update transcript repository ({count} files)", check=False).returncode != 0:
        print("GitHub upload: commit failed.")
        return 1
    if run_git("push", "origin", BRANCH, check=False).returncode != 0:
        print("GitHub upload: push failed. Run SET UP GITHUB SYNC.bat and sign in if prompted.")
        return 1
    print("GitHub upload complete.")
    return 0


def setup() -> int:
    if not ensure_mirror():
        return 1
    print("Testing GitHub access...")
    if run_git("pull", "--rebase", check=False).returncode != 0:
        return 1
    print("GitHub transcript sync is ready.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["setup", "pull", "push"])
    args = parser.parse_args()
    if args.command == "setup":
        return setup()
    if args.command == "pull":
        return pull_to_local()
    return push_from_local()


if __name__ == "__main__":
    raise SystemExit(main())
