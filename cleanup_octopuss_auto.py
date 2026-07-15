from __future__ import annotations

import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def main() -> int:
    noisy = ROOT / "intelligence" / "auto"

    if noisy.exists():
        shutil.rmtree(noisy)
        print("Deleted noisy intelligence/auto output.")
    else:
        print("No intelligence/auto folder found.")

    print("Compact candidates will be rebuilt on the next Drama Radar run.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
