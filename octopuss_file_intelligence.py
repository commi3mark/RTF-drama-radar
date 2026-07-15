from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from radar_common import load_json, save_json, path_for, now_iso


def slugify(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-") or "unknown"


def merge_unique(
    existing: list[dict[str, Any]],
    additions: list[dict[str, Any]],
    keys: tuple[str, ...],
) -> list[dict[str, Any]]:
    result = list(existing)
    seen = {
        tuple(str(row.get(key, "")) for key in keys)
        for row in result
    }

    for row in additions:
        marker = tuple(str(row.get(key, "")) for key in keys)
        if marker not in seen:
            result.append(row)
            seen.add(marker)

    return result


def write_md(path: Path, title: str, sections: list[tuple[str, list[str]]]) -> None:
    lines = [f"# {title}", ""]
    for heading, items in sections:
        lines.extend([f"## {heading}", ""])
        if items:
            lines.extend(items)
        else:
            lines.append("_None recorded._")
        lines.append("")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def main() -> int:
    pre_root = path_for("octopuss_preanalysis")
    auto_root = path_for("octopuss_auto")
    people_root = auto_root / "people"
    shows_root = auto_root / "shows"
    stories_root = auto_root / "stories"
    relationships_root = auto_root / "relationships"
    claims_root = auto_root / "claims"
    quotes_root = auto_root / "quotes"
    events_root = auto_root / "events"

    for path in [
        people_root,
        shows_root,
        stories_root,
        relationships_root,
        claims_root,
        quotes_root,
        events_root,
    ]:
        path.mkdir(parents=True, exist_ok=True)

    index = load_json(pre_root / "index.json", {"items": []})
    processed = 0

    for item in index.get("items", []):
        video_id = str(item.get("video_id") or "")
        data = load_json(pre_root / f"{video_id}.json", {})
        if not data:
            continue

        source = str(data.get("source") or "Unknown")
        title = str(data.get("title") or video_id)

        # Show/source file
        show_id = slugify(source)
        show_path = shows_root / f"{show_id}.json"
        show = load_json(
            show_path,
            {
                "show_id": show_id,
                "name": source,
                "episodes": [],
                "participant_candidates": [],
                "subject_history": [],
                "updated_at": None,
            },
        )
        show["episodes"] = merge_unique(
            show.get("episodes", []),
            [{
                "video_id": video_id,
                "title": title,
                "published": data.get("published"),
            }],
            ("video_id",),
        )
        show["participant_candidates"] = merge_unique(
            show.get("participant_candidates", []),
            [
                {
                    "video_id": video_id,
                    **row,
                }
                for row in data.get("participant_candidates", [])
            ],
            ("video_id", "entity"),
        )
        show["subject_history"] = merge_unique(
            show.get("subject_history", []),
            [
                {
                    "video_id": video_id,
                    "entity": row.get("entity"),
                    "importance": row.get("importance_guess"),
                    "sentiment": row.get("sentiment"),
                }
                for row in data.get("subjects", [])
            ],
            ("video_id", "entity"),
        )
        show["updated_at"] = now_iso()
        save_json(show_path, show)
        write_md(
            shows_root / f"{show_id}.md",
            show["name"],
            [
                (
                    "Episodes",
                    [
                        f"- **{row['title']}** (`{row['video_id']}`)"
                        for row in show.get("episodes", [])
                    ],
                ),
                (
                    "Participant candidates",
                    [
                        f"- **{row['entity']}** — {row['role_guess']} "
                        f"({float(row['confidence']):.0%}) in `{row['video_id']}`"
                        for row in show.get("participant_candidates", [])
                    ],
                ),
                (
                    "Subject history",
                    [
                        f"- **{row['entity']}** — {row['importance']}; "
                        f"{(row.get('sentiment') or {}).get('label', 'unclear')} "
                        f"in `{row['video_id']}`"
                        for row in show.get("subject_history", [])
                    ],
                ),
            ],
        )

        # People/topic files
        participant_lookup = {
            row.get("entity"): row
            for row in data.get("participant_candidates", [])
        }
        for subject in data.get("subjects", []):
            entity = str(subject.get("entity") or "")
            if not entity:
                continue

            entity_id = slugify(entity)
            path = people_root / f"{entity_id}.json"
            person = load_json(
                path,
                {
                    "entity_id": entity_id,
                    "name": entity,
                    "appearance_candidates": [],
                    "discussions": [],
                    "claims_by_or_about": [],
                    "story_matches": [],
                    "updated_at": None,
                },
            )

            if entity in participant_lookup:
                participant = participant_lookup[entity]
                person["appearance_candidates"] = merge_unique(
                    person.get("appearance_candidates", []),
                    [{
                        "video_id": video_id,
                        "title": title,
                        "source": source,
                        "role_guess": participant.get("role_guess"),
                        "confidence": participant.get("confidence"),
                        "signals": participant.get("signals", []),
                    }],
                    ("video_id",),
                )

            person["discussions"] = merge_unique(
                person.get("discussions", []),
                [{
                    "video_id": video_id,
                    "title": title,
                    "source": source,
                    "importance": subject.get("importance_guess"),
                    "mentions": subject.get("mentions"),
                    "sentiment": subject.get("sentiment"),
                    "receipts": subject.get("receipts", []),
                }],
                ("video_id",),
            )

            for story in data.get("story_matches", []):
                person["story_matches"] = merge_unique(
                    person.get("story_matches", []),
                    [{
                        "video_id": video_id,
                        **story,
                    }],
                    ("video_id", "story_id"),
                )

            person["updated_at"] = now_iso()
            save_json(path, person)
            write_md(
                people_root / f"{entity_id}.md",
                person["name"],
                [
                    (
                        "Appearance candidates",
                        [
                            f"- **{row['title']}** — {row['role_guess']} "
                            f"on {row['source']} ({float(row['confidence']):.0%})"
                            for row in person.get("appearance_candidates", [])
                        ],
                    ),
                    (
                        "Discussions",
                        [
                            f"- **{row['title']}** — {row['importance']}; "
                            f"{(row.get('sentiment') or {}).get('label', 'unclear')}; "
                            f"{row.get('mentions', 0)} mentions"
                            for row in person.get("discussions", [])
                        ],
                    ),
                    (
                        "Candidate story links",
                        [
                            f"- **{row['title']}** — similarity {float(row['score']):.3f}"
                            for row in person.get("story_matches", [])
                        ],
                    ),
                ],
            )

        # Claims
        for number, claim in enumerate(data.get("claim_candidates", []), start=1):
            claim_id = slugify(
                f"{video_id}-{number}-{claim.get('claimant')}-{claim.get('claim', '')[:80]}"
            )
            payload = {
                "schema_version": "octopuss-auto-claim-v0.1",
                "claim_id": claim_id,
                "video_id": video_id,
                "episode_title": title,
                "source": source,
                **claim,
            }
            save_json(claims_root / f"{claim_id}.json", payload)

        # Quotes
        for number, quote in enumerate(data.get("quote_candidates", []), start=1):
            quote_id = slugify(
                f"{video_id}-{number}-{quote.get('text', '')[:80]}"
            )
            payload = {
                "schema_version": "octopuss-auto-quote-v0.1",
                "quote_id": quote_id,
                "video_id": video_id,
                "episode_title": title,
                "source": source,
                **quote,
            }
            save_json(quotes_root / f"{quote_id}.json", payload)

        # Relationship candidates
        for relation in data.get("relationship_signals", []):
            left = slugify(str(relation.get("left") or "unknown"))
            right = slugify(str(relation.get("right") or "unknown"))
            pair = "--".join(sorted([left, right]))
            path = relationships_root / f"{pair}.json"
            record = load_json(
                path,
                {
                    "relationship_id": pair,
                    "left": relation.get("left"),
                    "right": relation.get("right"),
                    "signals": [],
                    "updated_at": None,
                },
            )
            record["signals"] = merge_unique(
                record.get("signals", []),
                [{
                    "video_id": video_id,
                    "episode_title": title,
                    "source": source,
                    **relation,
                }],
                ("video_id", "signal_type", "timestamp"),
            )
            record["updated_at"] = now_iso()
            save_json(path, record)
            write_md(
                relationships_root / f"{pair}.md",
                f"{record['left']} ↔ {record['right']}",
                [
                    (
                        "Candidate signals",
                        [
                            f"- **{row['signal_type']}** in `{row['video_id']}` "
                            f"at {row.get('timestamp')} "
                            f"({float(row.get('confidence', 0)):.0%})"
                            for row in record.get("signals", [])
                        ],
                    )
                ],
            )

        # Story candidate files
        for match in data.get("story_matches", []):
            story_id = slugify(str(match.get("story_id") or match.get("title") or "story"))
            path = stories_root / f"{story_id}.json"
            record = load_json(
                path,
                {
                    "story_id": story_id,
                    "title": match.get("title") or story_id,
                    "candidate_updates": [],
                    "updated_at": None,
                },
            )
            record["candidate_updates"] = merge_unique(
                record.get("candidate_updates", []),
                [{
                    "video_id": video_id,
                    "episode_title": title,
                    "source": source,
                    "similarity": match.get("score"),
                }],
                ("video_id",),
            )
            record["updated_at"] = now_iso()
            save_json(path, record)
            write_md(
                stories_root / f"{story_id}.md",
                record["title"],
                [
                    (
                        "Candidate updates",
                        [
                            f"- **{row['episode_title']}** — "
                            f"similarity {float(row['similarity']):.3f}"
                            for row in record.get("candidate_updates", [])
                        ],
                    )
                ],
            )

        # Event record per transcript
        save_json(
            events_root / f"{video_id}.json",
            {
                "schema_version": "octopuss-auto-event-v0.1",
                "event_id": video_id,
                "title": title,
                "source": source,
                "published": data.get("published"),
                "chapters": data.get("chapters", []),
                "subjects": [
                    {
                        "entity": row.get("entity"),
                        "importance": row.get("importance_guess"),
                        "sentiment": row.get("sentiment"),
                    }
                    for row in data.get("subjects", [])
                ],
                "story_matches": data.get("story_matches", []),
                "uncertainty_flags": data.get("uncertainty_flags", []),
            },
        )

        processed += 1

    save_json(
        auto_root / "index.json",
        {
            "schema_version": "octopuss-auto-intelligence-index-v0.1",
            "generated_at": now_iso(),
            "processed_preanalyses": processed,
            "folders": {
                "people": "intelligence/auto/people",
                "shows": "intelligence/auto/shows",
                "stories": "intelligence/auto/stories",
                "relationships": "intelligence/auto/relationships",
                "claims": "intelligence/auto/claims",
                "quotes": "intelligence/auto/quotes",
                "events": "intelligence/auto/events",
            },
        },
    )

    print(f"OCTOPUSS automatic intelligence filed from {processed} pre-analyses.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
