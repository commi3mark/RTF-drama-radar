# Drama Radar MK2

Two commands operate the system:

- `RUN RADAR.bat` scans configured sources and updates the live feed.
- `GET TRANSCRIPTS.bat` retrieves YouTube transcripts from Stalinvo.

Every completed run:

- updates `radar-stats.json`;
- prints a readable control-panel summary;
- lists new transcripts, unavailable videos, and scheduled retries;
- writes a timestamped text receipt under `radar/receipts/`.

The status vocabulary is:

- `HEALTHY` — all active parts worked normally;
- `DEGRADED` — working, but with failures or reduced coverage;
- `STALLED` — a subsystem attempted work but made no progress;
- `FAILED` — the run or output validation failed.

A folder that has not yet been connected to GitHub runs locally without flooding the console with Git errors.
