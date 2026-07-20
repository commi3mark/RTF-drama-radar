# RUSSIAN TROLL FACTORY — Transcript and Radar System

The project contains two active applications: Drama Radar and Transcript Grabber.

## 01 — Drama Radar

Detects current activity from monitored sources and produces `output/drama-radar.json`.

Drama Radar does not read or rewrite transcript reports.

## 02 — Transcript Grabber

Owns the transcript archive. Use `START TRANSCRIPT WORKER.bat` for normal operation.

The worker:

- tries the normal YouTube transcript service first;
- falls back to YouTube subtitle/caption retrieval;
- declares an IP block only when both routes are blocked;
- pauses rather than closing;
- resumes automatically after cooldown;
- learns a conservative request pace;
- stores new transcripts without launching a separate analysis engine.

Use `STOP TRANSCRIPT WORKER.bat` to request a safe shutdown.

Technical state is stored in `02 - TRANSCRIPT GRABBER/state/`.

## Reports, profiles, and analysis

Completed human-readable editorial reports are stored beside their raw transcript JSON files:

`02 - TRANSCRIPT GRABBER/transcripts/YYYY-MM/`

Preserved profiles and Commi3 analysis are stored under:

- `02 - TRANSCRIPT GRABBER/analysis/profiles/`
- `02 - TRANSCRIPT GRABBER/analysis/commi3-mention-priority.txt`
- `02 - TRANSCRIPT GRABBER/analysis/commi3-mention-resweep.txt`

## Ownership

- Drama Radar owns live radar files.
- Transcript Grabber owns transcripts, reports, profiles, indexes, retrieval state, and preserved analysis.

Temporary operational files use `.tmp` and are replaced atomically where supported.
