from __future__ import annotations

import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
RUN_GUARD_PORT = 38745


def run(script: str, *arguments: str) -> int:
    return subprocess.run([sys.executable, str(APP / script), *arguments], cwd=ROOT, check=False).returncode


def main() -> int:
    guard = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        guard.bind(("127.0.0.1", RUN_GUARD_PORT))
    except OSError:
        print("Transcript Grabber is already running. This second click will now close.")
        return 0
    print("=" * 72)
    print("RTF TRANSCRIPT GRABBER - PRIORITY / PIPER / SLEEPERS / ARCHIVE")
    print("=" * 72)
    result = run("get_selected_transcripts.py")
    if result not in (0, 2):
        print("Transcript run failed; GitHub publishing was skipped.")
        return result
    print("\nPublishing any new transcripts to GitHub...")
    publish = run("github_sync.py", "push")
    if publish:
        print("Transcripts are safe locally, but GitHub publishing needs attention.")
        return publish
    print("\nTranscript Grabber finished and is now OFF.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
