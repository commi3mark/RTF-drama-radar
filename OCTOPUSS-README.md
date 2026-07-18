# OCTOPUSS Intelligence System

Copy this package into the root of `C:\AI\DRAMA RADAR`.

## Launchers

- `RUN OCTOPUSS - COMMI3 WATCH.bat` — deepest protected scan for Commi3 Mark and RTF mentions.
- `RUN OCTOPUSS - ENTITY SCAN.bat` — fast entity resolution and incremental profile update.
- `RUN OCTOPUSS - DEEP ENTITY BUILD.bat` — full twenty-pass dossier build over every transcript.
- `RUN OCTOPUSS - ALL.bat` — Commi3 Watch, entity resolution, deep dossier build, stories and reports.

## Deep build output

Detailed dossiers are written beneath:

`octopuss\intelligence\entities\people\<entity-id>\`

Each profile can contain identity, aliases, channels, websites, socials, public emails, recurring shows, projects, associates, guest appearances, relationships, comic history, behaviour, terminology, quotes, claims, communities, stories, influence, threat, evidence, timeline, activity, quality control and run history.

The deep build opens the transcript corpus once, creates evidence objects, then runs all investigators over that shared evidence. Candidate information remains labelled as candidate; the script does not silently present inferred facts as confirmed.

## Open-world person discovery

The Entity Scan now discovers new people and personas instead of limiting the build to seeded identities. It:

- extracts likely personal names and handles from channel names and titles;
- separates shows, comics, projects, institutions and organisations before creating person folders;
- clusters likely aliases and recurring transcription variants;
- creates provisional profiles for strong unseeded people/personas;
- keeps weaker names in `octopuss/intelligence/entities/candidates/unresolved-names.json`;
- passes every resolved and provisional person into Deep Entity Build.

Automatically discovered profiles are marked `provisional_open_world` and carry a review warning until confirmed.
