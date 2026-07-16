# OCTOPUSS investigators

The deep entity build executes twenty specialist passes. The current implementation keeps orchestration in `pipelines/deep_entity_build.py` so every pass shares one in-memory transcript corpus. This avoids reopening the same large transcript files twenty times.

The passes cover identity, aliases, public links, channels, shows, projects, associates, appearances, relationships, comics, behaviour, terminology, quotes, claims, communities, narratives, influence, threat, quality control, and profile rebuilding.
