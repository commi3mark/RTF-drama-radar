from __future__ import annotations

import json
import os
import random
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / "state"
PACE_FILE = STATE_DIR / "adaptive-pace.json"
COOLDOWN_FILE = STATE_DIR / "transcript-cooldown.json"

DEFAULT = {
    "schema_version": "2.0",
    "delay_seconds": 60,
    "minimum_delay_seconds": 15,
    "maximum_delay_seconds": 300,
    "clean_video_attempts": 0,
    "block_streak": 0,
    "last_video_attempt": None,
}
COOLDOWNS_MINUTES = (30, 120, 480, 1440, 4320)


def now() -> datetime:
    return datetime.now(timezone.utc)


def load(path: Path, default):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else dict(default)
    except Exception:
        return dict(default)


def save(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def parse_time(value):
    try:
        parsed = datetime.fromisoformat(str(value))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def cooldown_remaining() -> float:
    state = load(COOLDOWN_FILE, {})
    until = parse_time(state.get("until"))
    if state.get("active") and until and now() < until:
        return (until - now()).total_seconds()
    if state.get("active"):
        save(COOLDOWN_FILE, {**state, "active": False, "cleared_at": now().isoformat()})
    return 0.0


def wait_before_video(label: str) -> None:
    """Enforce one shared delay between complete video attempts across separate runs."""
    state = load(PACE_FILE, DEFAULT)
    for key, value in DEFAULT.items():
        state.setdefault(key, value)
    previous = parse_time(state.get("last_video_attempt"))
    delay = float(state["delay_seconds"])
    target = random.uniform(delay * 0.85, delay * 1.15)
    elapsed = (now() - previous).total_seconds() if previous else target
    remaining = max(0.0, target - elapsed)
    if remaining:
        print(f"PACE: waiting {remaining:.0f}s before {label}.", flush=True)
        time.sleep(remaining)
    state["last_video_attempt"] = now().isoformat()
    save(PACE_FILE, state)


def clean_attempt() -> None:
    state = load(PACE_FILE, DEFAULT)
    for key, value in DEFAULT.items():
        state.setdefault(key, value)
    state["clean_video_attempts"] = int(state.get("clean_video_attempts", 0)) + 1
    if state["clean_video_attempts"] >= 3:
        state["delay_seconds"] = max(
            int(state["minimum_delay_seconds"]), int(state["delay_seconds"]) - 5
        )
        state["clean_video_attempts"] = 0
    if int(state.get("block_streak", 0)) and state["clean_video_attempts"] == 0:
        state["block_streak"] = max(0, int(state["block_streak"]) - 1)
    save(PACE_FILE, state)


def dual_block(reason: str) -> datetime:
    state = load(PACE_FILE, DEFAULT)
    for key, value in DEFAULT.items():
        state.setdefault(key, value)
    streak = int(state.get("block_streak", 0)) + 1
    minutes = COOLDOWNS_MINUTES[min(streak - 1, len(COOLDOWNS_MINUTES) - 1)]
    state["block_streak"] = streak
    state["clean_video_attempts"] = 0
    state["delay_seconds"] = min(
        int(state["maximum_delay_seconds"]), max(30, int(state["delay_seconds"]) + 15)
    )
    until = now() + timedelta(minutes=minutes)
    save(PACE_FILE, state)
    save(COOLDOWN_FILE, {
        "active": True,
        "reason": reason,
        "block_streak": streak,
        "started_at": now().isoformat(),
        "until": until.isoformat(),
    })
    return until


def describe() -> str:
    state = load(PACE_FILE, DEFAULT)
    return f"about {int(state.get('delay_seconds', 60))} seconds between complete video attempts"
