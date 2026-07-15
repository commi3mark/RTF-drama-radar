# Drama Radar Intelligence Builder

## Install

Place `build_intelligence.py` in the root of the `RTF-drama-radar` repository.

No extra Python packages are required.

## Run

```bat
cd C:\AI\RTF-drama-radar
python build_intelligence.py
```

Force a complete rebuild:

```bat
python build_intelligence.py --force
```

## Outputs

The script creates or replaces:

- `transcript-manifest.json`
- `mention-index.json`
- `entities.json`
- `relationships.json`
- `stories.json`
- `campaigns.json`
- `evidence-index.json`
- `processing-state.json`

## What it solves immediately

- Full-transcript Commi3 Mark alias detection
- Canonical identities and aliases
- Timestamped quotations and YouTube receipts
- Global mention index
- Co-mention relationship graph
- Early story clustering
- Campaign leads
- Incremental file hashing
- Manifest regeneration
- Evidence indexing
- Duplicate processing avoidance

## Important limitation

This is the first intelligence foundation, not the complete final form of all 27 roadmap items.

The following still need dedicated modules or live data access:

- fully automated browser screenshots;
- X/Twitter ingestion and campaign heat weighting;
- robust semantic story clustering;
- sentiment and allegation classification;
- source reliability scoring;
- archive snapshot scheduling;
- report prose generation;
- campaign status verification;
- cross-platform identity discovery.

## Adding aliases

Edit `ENTITY_DEFINITIONS` near the top of `build_intelligence.py`.

Example:

```python
"new-person": {
    "name": "New Person",
    "priority": 50,
    "aliases": [
        "New Person",
        "Common ASR Mistake",
    ],
},
```
