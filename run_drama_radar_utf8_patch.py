# --- UTF-8 console patch for run_drama_radar.py ---
# Place this near the top of run_drama_radar.py, immediately after the imports.

import os
import sys

# Force UTF-8 console output where supported.
os.environ.setdefault("PYTHONUTF8", "1")

for stream_name in ("stdout", "stderr"):
    stream = getattr(sys, stream_name, None)
    if hasattr(stream, "reconfigure"):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


# Replace your existing write_console() function with this version.

def write_console(message: str) -> None:
    """Never let console encoding errors stop the pipeline."""
    try:
        print(message, flush=True)
    except UnicodeEncodeError:
        safe = message.encode(
            getattr(sys.stdout, "encoding", "utf-8") or "utf-8",
            errors="replace",
        ).decode(
            getattr(sys.stdout, "encoding", "utf-8") or "utf-8",
            errors="replace",
        )
        print(safe, flush=True)
    except Exception:
        # Logging must never stop the automation.
        pass
