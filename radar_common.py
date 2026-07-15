from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path, default: Any):
    if not path.exists():
        return default

    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")

    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")

    temporary.replace(path)


def settings() -> dict:
    return load_json(ROOT / "config" / "settings.json", {})


def path_for(key: str) -> Path:
    configured = settings().get("paths", {})
    if key not in configured:
        raise KeyError(f"Missing path setting: {key}")
    return ROOT / configured[key]


def safe_filename(text: str, max_len: int = 150) -> str:
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", text).strip().rstrip(".")
    text = re.sub(r"\s+", " ", text)
    return (text[:max_len].strip() or "untitled")


def stable_id(*parts: str) -> str:
    raw = "\n".join(str(part or "") for part in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]
