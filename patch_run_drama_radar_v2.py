#!/usr/bin/env python3
"""
Robust automatic patcher for run_drama_radar.py.

It does not depend on the exact write_console() signature. It:
1. creates a timestamped backup;
2. inserts UTF-8 stdout/stderr configuration near the top;
3. replaces the exact crashing line `print(message, flush=True)` with a safe block.
"""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TARGET = ROOT / "run_drama_radar.py"

UTF8_BLOCK = """
# --- UTF-8 console safety patch ---
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

"""

OLD_LINE = "    print(message, flush=True)\n"

NEW_BLOCK = """    try:
        print(message, flush=True)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
        safe_message = str(message).encode(
            encoding, errors="replace"
        ).decode(
            encoding, errors="replace"
        )
        print(safe_message, flush=True)
    except Exception:
        pass
"""


def main() -> int:
    if not TARGET.exists():
        print(f"ERROR: Could not find {TARGET}")
        print(r"Put this file in C:\AI\RTF-drama-radar and run it there.")
        return 1

    original = TARGET.read_text(encoding="utf-8-sig")

    if OLD_LINE not in original:
        print("ERROR: Could not find the exact crashing line:")
        print("    print(message, flush=True)")
        print("No changes were written.")
        return 2

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = TARGET.with_name(f"run_drama_radar.py.backup-{stamp}")
    shutil.copy2(TARGET, backup)

    updated = original.replace(OLD_LINE, NEW_BLOCK, 1)

    if "UTF-8 console safety patch" not in updated:
        lines = updated.splitlines(keepends=True)
        insert_at = 0

        # Insert after the initial import section.
        for index, line in enumerate(lines[:120]):
            stripped = line.strip()
            if (
                not stripped
                or stripped.startswith("#")
                or stripped.startswith("import ")
                or stripped.startswith("from ")
                or stripped.startswith('"""')
                or stripped.endswith('"""')
            ):
                insert_at = index + 1
                continue
            break

        lines.insert(insert_at, UTF8_BLOCK)
        updated = "".join(lines)

    TARGET.write_text(updated, encoding="utf-8", newline="\n")

    print("PATCH COMPLETE")
    print(f"Updated: {TARGET}")
    print(f"Backup:  {backup}")
    print("")
    print("Now test with:")
    print("python run_drama_radar.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
