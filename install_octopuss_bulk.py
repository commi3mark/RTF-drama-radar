from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SETTINGS = ROOT / "config" / "settings.json"


def deep_merge(target: dict, source: dict) -> dict:
    for key, value in source.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            deep_merge(target[key], value)
        else:
            target[key] = value
    return target


def main() -> int:
    if not SETTINGS.exists():
        print(f"ERROR: Could not find {SETTINGS}")
        return 1

    data = json.loads(SETTINGS.read_text(encoding="utf-8"))
    patch_path = ROOT / "config" / "octopuss-bulk-settings-patch.json"
    patch = json.loads(patch_path.read_text(encoding="utf-8"))
    deep_merge(data, patch)
    SETTINGS.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print("OCTOPUSS bulk paths added to config/settings.json.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
