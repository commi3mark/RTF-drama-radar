from __future__ import annotations

from run_sweep import STAGES
from radar_pipeline import run_pipeline


if __name__ == "__main__":
    raise SystemExit(
        run_pipeline(
            "DRAMA RADAR — SOURCE SWEEP",
            STAGES,
        )
    )
