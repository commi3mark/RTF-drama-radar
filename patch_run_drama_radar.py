#!/usr/bin/env python3
from __future__ import annotations

import re
import shutil
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TARGET = ROOT / "run_drama_radar.py"

UTF8_BLOCK = '''# --- UTF-8 console safety patch ---
import os
import sys

os.environ.setdefault("PYTHONUTF8", "1")

for _stream_name in ("stdout", "stderr"):
    _stream = getattr(sys, _stream_name, None)
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
# --- end UTF-8 console safety patch ---
'''

SAFE_FUNCTION = '''def write_console(message: str) -> None:
    """Never allow console encoding errors to stop the pipeline."""
    try:
        print(message, flush=True)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
        safe = str(message).encode(encoding, errors="replace").decode(
            encoding, errors="replace"
        )
        print(safe, flush=True)
    except Exception:
        pass
'''


def main() -> int:
    if not TARGET.exists():
        print(f"ERROR: Could not find {TARGET}")
        print(r"Place this file in C:\AI\RTF-drama-radar and run it there.")
        return 1

    original = TARGET.read_text(encoding="utf-8-sig")

    if "UTF-8 console safety patch" in original and "Never allow console encoding errors" in original:
        print("run_drama_radar.py is already patched.")
        return 0

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = TARGET.with_name(f"run_drama_radar.py.backup-{stamp}")
    shutil.copy2(TARGET, backup)

    updated = original

    if "UTF-8 console safety patch" not in updated:
        lines = updated.splitlines(keepends=True)
        insert_at = 0
        for i, line in enumerate(lines[:100]):
            stripped = line.strip()
            if (
                not stripped
                or stripped.startswith("#")
                or stripped.startswith("import ")
                or stripped.startswith("from ")
            ):
                insert_at = i + 1
                continue
            break
        lines.insert(insert_at, "\n" + UTF8_BLOCK + "\n")
        updated = "".join(lines)

    pattern = re.compile(
r"^def write_console\(message(?::\s*str)?\)\s*(?:->\s*None)?\s*:\s*\n"
r"(?:^[ \t]+.*\n)+",
        re.MULTILINE,
    )
    match = pattern.search(updated)

    if not match:
        print("ERROR: Could not locate write_console().")
        print(f"Backup created: {backup}")
        print("No changes were written.")
        return 2

    updated = updated[:match.start()] + SAFE_FUNCTION + "\n" + updated[match.end():]
    TARGET.write_text(updated, encoding="utf-8", newline="\n")

    print("PATCH COMPLETE")
    print(f"Updated: {TARGET}")
    print(f"Backup:  {backup}")
    print()
    print("Now test with:")
    print("python run_drama_radar.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
