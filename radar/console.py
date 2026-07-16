from __future__ import annotations

import sys
import time


def bar(current: int, total: int, width: int = 28) -> str:
    total = max(total, 1)
    current = max(0, min(current, total))
    filled = round(width * current / total)
    return "█" * filled + "░" * (width - filled)


def progress(label: str, current: int, total: int, detail: str = "", started: float | None = None) -> None:
    elapsed = ""
    if started is not None:
        elapsed = f" | {time.time() - started:0.1f}s"
    suffix = f" | {detail}" if detail else ""
    print(f"[{bar(current, total)}] {current}/{total}  {label}{suffix}{elapsed}", flush=True)


def stage(current: int, total: int, label: str) -> None:
    print(f"\n[{bar(current, total)}] {current}/{total}  {label}", flush=True)
