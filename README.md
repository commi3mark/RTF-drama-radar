# Drama Radar v5

This package removes speaker detection entirely.

It builds the four core persistent databases:

1. `data/people.json`
2. `data/quote-index.json`
3. `data/stories.json`
4. `data/evidence-index.json`

It also creates context bundles, narrative units, relationships, risk signals, and incremental processing state.

## Install

Copy the package contents into:

```text
C:\AI\RTF-drama-radar
```

## Run everything

```bat
python run_radar.py
```

Or run the core builder directly:

```bat
python build_radar.py
```

## Query exact quotes

```bat
python query_quotes.py "Liam Gray"
python query_quotes.py "Liam Gray" "Katy"
python query_quotes.py "Commi3 Mark" --contains "report"
```

## Query story memory

```bat
python query_stories.py Dropbox
python query_stories.py Liam Katy
python query_stories.py --active-only
```

## Query a person dossier

```bat
python query_people.py "Johnny Rocket"
```

## Archive current state

```bat
python archive_snapshot.py
```

## What is included

- canonical entities and aliases;
- persistent people database;
- exact quote index;
- timestamped YouTube receipts;
- context bundles;
- extractive narrative units;
- persistent story memory;
- candidate relationship graph;
- Commi3 Mark interaction-risk signals;
- incremental hashing and caching;
- one-command orchestration;
- archive snapshots.

## What still requires external integration

These cannot be completed by local transcript processing alone:

- automatic discovery of new YouTube/X/Substack sources;
- downloading new transcripts;
- uploading files to GitHub;
- X/Twitter ingestion;
- screenshot capture;
- scheduled execution on Windows;
- automatic Git commits/pushes.

The package is structured so those connectors can be added without changing the core databases.
