from __future__ import annotations

import json
import os
import random
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
GRABBER_ROOT = HERE.parent
STATE_DIR = GRABBER_ROOT / "state"
LOCK = STATE_DIR / "transcript-worker.lock"
STOP = STATE_DIR / "stop-worker"
RATE = STATE_DIR / "retrieval-rate.json"
COOLDOWN = STATE_DIR / "transcript-cooldown.json"
TRANSCRIPTS = GRABBER_ROOT / "transcripts"

DEFAULT_STATE = {
    "schema_version": "1.0",
    "minimum_delay_seconds": 180,
    "idle_check_seconds": 900,
    "clean_successes": 0,
    "events": [],
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def atomic_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def transcript_count() -> int:
    return sum(1 for p in TRANSCRIPTS.rglob("*.json") if not p.name.endswith(".intelligence.json") and p.name not in {"transcript-index.json", "transcript-manifest.json"})


def human_wait(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds >= 3600:
        return f"about {seconds // 3600} hour(s)"
    if seconds >= 60:
        return f"about {max(1, seconds // 60)} minute(s)"
    return f"about {seconds} seconds"


def sleep_interruptibly(seconds: float) -> None:
    end = time.time() + max(0, seconds)
    while time.time() < end:
        if STOP.exists():
            return
        time.sleep(min(5, end - time.time()))


def event(state: dict, kind: str, duration: float, new_transcripts: int) -> None:
    events = state.setdefault("events", [])
    events.append({"at": now_iso(), "kind": kind, "duration_seconds": round(duration, 2), "new_transcripts": new_transcripts})
    state["events"] = events[-200:]
    successful = [e for e in state["events"] if e.get("new_transcripts", 0) > 0]
    total_seconds = sum(float(e.get("duration_seconds", 0)) for e in successful)
    total_new = sum(int(e.get("new_transcripts", 0)) for e in successful)
    state["observed_successes_per_hour"] = round((total_new / total_seconds) * 3600, 2) if total_seconds else 0
    atomic_json(RATE, state)


def main() -> int:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode("ascii"))
        os.close(fd)
    except FileExistsError:
        print("The Transcript Worker is already running.")
        return 2

    STOP.unlink(missing_ok=True)
    state = read_json(RATE, dict(DEFAULT_STATE))
    for key, value in DEFAULT_STATE.items():
        state.setdefault(key, value)

    print("TRANSCRIPT WORKER")
    print("The worker is now always on. It will wait quietly between attempts and resume after cooldowns.")
    try:
        while not STOP.exists():
            cooldown = read_json(COOLDOWN, {})
            if cooldown.get("active") and cooldown.get("until"):
                try:
                    until = datetime.fromisoformat(cooldown["until"])
                    if until.tzinfo is None:
                        until = until.replace(tzinfo=timezone.utc)
                    remaining = (until - datetime.now(timezone.utc)).total_seconds()
                except Exception:
                    remaining = 0
                if remaining > 0:
                    print(f"Both retrieval routes are blocked. Requests are paused for {human_wait(remaining)}; the worker remains running.")
                    sleep_interruptibly(min(remaining, 900))
                    continue

            before = transcript_count()
            started = time.time()
            result = subprocess.run([sys.executable, str(HERE / "get_transcripts.py"), "--limit", "1"], cwd=GRABBER_ROOT)
            elapsed = time.time() - started
            after = transcript_count()
            gained = max(0, after - before)
            cooldown_after = read_json(COOLDOWN, {})
            dual_block = bool(cooldown_after.get("active"))

            if gained:
                state["clean_successes"] = int(state.get("clean_successes", 0)) + gained
                if state["clean_successes"] >= 5:
                    state["minimum_delay_seconds"] = max(90, int(float(state.get("minimum_delay_seconds", 180)) * 0.92))
                    state["clean_successes"] = 0
                event(state, "success", elapsed, gained)
                base = float(state.get("minimum_delay_seconds", 180))
                wait = random.uniform(base * 0.8, base * 1.2)
                print(f"Current safe pace: approximately one attempt every {max(1, round(base / 60))} minute(s).")
                print(f"Waiting {human_wait(wait)} before checking the next video.")
                sleep_interruptibly(wait)
            elif dual_block:
                state["clean_successes"] = 0
                state["minimum_delay_seconds"] = min(3600, int(float(state.get("minimum_delay_seconds", 180)) * 1.5))
                event(state, "dual_ip_block", elapsed, 0)
                print("Both routes were IP-blocked. The safe pace has been reduced; the worker will resume automatically after cooldown.")
            else:
                event(state, "no_transcript", elapsed, 0)
                idle = float(state.get("idle_check_seconds", 900))
                print(f"No transcript was added. Checking again in {human_wait(idle)}.")
                sleep_interruptibly(idle)
    finally:
        LOCK.unlink(missing_ok=True)
        STOP.unlink(missing_ok=True)
        print("Transcript Worker stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
