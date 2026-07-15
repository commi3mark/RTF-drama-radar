#!/usr/bin/env python3
from __future__ import annotations
import subprocess
import sys
from datetime import datetime
from pathlib import Path

LOGS = Path("logs")
LOGS.mkdir(exist_ok=True)

def run(command):
    print(">", " ".join(command))
    result = subprocess.run(command, text=True, capture_output=True)
    print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    if result.returncode != 0:
        raise SystemExit(result.returncode)

def main():
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run([sys.executable, "build_radar.py"])
    print("Pipeline complete.")
    print("Next external steps, when configured: source discovery, transcript download, GitHub upload, X ingestion, screenshots.")

if __name__ == "__main__":
    main()
