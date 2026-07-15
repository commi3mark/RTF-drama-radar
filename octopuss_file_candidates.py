from __future__ import annotations

import shutil
import re
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


def write_markdown(path: Path, title: str, sections: list[tuple[str, list[str]]]) -> None:
    lines = [f"# {title}", ""]
    for heading, items in sections:
        lines.extend([f"## {heading}", ""])
        lines.extend(items or ["_None recorded._"])
        lines.append("")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def main() -> int:
    pre_root = path_for("octopuss_preanalysis")
    candidates_root = path_for("octopuss_candidates")

    if candidates_root.exists():
        shutil.rmtree(candidates_root)

    people_root = candidates_root / "people"
    shows_root = candidates_root / "shows"
    stories_root = candidates_root / "stories"
    relationships_root = candidates_root / "relationships"
    episodes_root = candidates_root / "episodes"
    review_root = candidates_root / "review"

    for path in [
        people_root,
        shows_root,
        stories_root,
        relationships_root,
        episodes_root,
        review_root,
    ]:
        path.mkdir(parents=True, exist_ok=True)

    index = load_json(pre_root / "index.json", {"items": []})
    review_queue = []

    for item in index.get("items", []):
        video_id = str(item.get("video_id") or "")
        data = load_json(pre_root / f"{video_id}.json", {})
        if not data:
            continue

        title = str(data.get("title") or video_id)
        source = str(data.get("source") or "Unknown")

        save_json(
            episodes_root / f"{video_id}.json",
            {
                "schema_version": "octopuss-candidate-episode-v0.1",
                "video_id": video_id,
                "title": title,
                "source": source,
                "published": data.get("published"),
                "review_priority": data.get("review_priority", 0),
                "participant_candidates": data.get("participant_candidates", []),
                "subjects": data.get("subjects", []),
                "chapters": data.get("chapters", []),
                "claims": data.get("claim_candidates", []),
                "quotes": data.get("quote_candidates", []),
                "relationships": data.get("relationship_signals", []),
                "story_matches": data.get("story_matches", []),
                "uncertainty_flags": data.get("uncertainty_flags", []),
            },
        )

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
            },
        )
        show["episodes"] = merge_unique(
            show["episodes"],
            [{"video_id": video_id, "title": title}],
            ("video_id",),
        )
        show["participant_candidates"] = merge_unique(
            show["participant_candidates"],
            [{"video_id": video_id, **row} for row in data.get("participant_candidates", [])],
            ("video_id", "entity"),
        )
        show["subject_history"] = merge_unique(
            show["subject_history"],
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
        save_json(show_path, show)

        for subject in data.get("subjects", []):
            entity = str(subject.get("entity") or "")
            if not entity:
                continue

            entity_id = slugify(entity)
            person_path = people_root / f"{entity_id}.json"
            person = load_json(
                person_path,
                {
                    "entity_id": entity_id,
                    "name": entity,
                    "appearance_candidates": [],
                    "discussions": [],
                    "claim_candidates": [],
                    "relationship_candidates": [],
                    "story_candidates": [],
                },
            )

            participant = next(
                (
                    row for row in data.get("participant_candidates", [])
                    if row.get("entity") == entity
                ),
                None,
            )
            if participant:
                person["appearance_candidates"] = merge_unique(
                    person["appearance_candidates"],
                    [{"video_id": video_id, "title": title, "source": source, **participant}],
                    ("video_id",),
                )

            person["discussions"] = merge_unique(
                person["discussions"],
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

            person["claim_candidates"] = merge_unique(
                person["claim_candidates"],
                [
                    {"video_id": video_id, "title": title, **claim}
                    for claim in data.get("claim_candidates", [])
                    if entity in claim.get("subjects", [])
                ],
                ("video_id", "claim"),
            )

            person["relationship_candidates"] = merge_unique(
                person["relationship_candidates"],
                [
                    {"video_id": video_id, "title": title, **relation}
                    for relation in data.get("relationship_signals", [])
                    if entity in {relation.get("left"), relation.get("right")}
                ],
                ("video_id", "left", "right", "signal_type"),
            )

            person["story_candidates"] = merge_unique(
                person["story_candidates"],
                [{"video_id": video_id, **story} for story in data.get("story_matches", [])],
                ("video_id", "story_id"),
            )

            save_json(person_path, person)

        for relation in data.get("relationship_signals", []):
            left = str(relation.get("left") or "")
            right = str(relation.get("right") or "")
            if not left or not right:
                continue

            relation_id = "--".join(sorted([slugify(left), slugify(right)]))
            path = relationships_root / f"{relation_id}.json"
            record = load_json(
                path,
                {
                    "relationship_id": relation_id,
                    "left": left,
                    "right": right,
                    "signals": [],
                },
            )
            record["signals"] = merge_unique(
                record["signals"],
                [{"video_id": video_id, "title": title, "source": source, **relation}],
                ("video_id", "signal_type", "timestamp"),
            )
            save_json(path, record)

        for story in data.get("story_matches", []):
            story_id = str(story.get("story_id") or slugify(story.get("title") or "story"))
            path = stories_root / f"{story_id}.json"
            record = load_json(
                path,
                {
                    "story_id": story_id,
                    "title": story.get("title") or story_id,
                    "candidate_updates": [],
                },
            )
            record["candidate_updates"] = merge_unique(
                record["candidate_updates"],
                [{
                    "video_id": video_id,
                    "title": title,
                    "source": source,
                    "score": story.get("score"),
                }],
                ("video_id",),
            )
            save_json(path, record)

        review_queue.append(
            {
                "video_id": video_id,
                "title": title,
                "source": source,
                "priority": data.get("review_priority", 0),
                "reasons": {
                    "claims": len(data.get("claim_candidates", [])),
                    "quotes": len(data.get("quote_candidates", [])),
                    "relationships": len(data.get("relationship_signals", [])),
                    "story_matches": len(data.get("story_matches", [])),
                    "major_subjects": sum(
                        row.get("importance_guess") == "major"
                        for row in data.get("subjects", [])
                    ),
                },
            }
        )

    review_queue.sort(key=lambda row: row["priority"], reverse=True)

    save_json(
        review_root / "queue.json",
        {
            "schema_version": "octopuss-review-queue-v0.1",
            "generated_at": now_iso(),
            "count": len(review_queue),
            "items": review_queue,
        },
    )

    write_markdown(
        review_root / "queue.md",
        "OCTOPUSS Review Queue",
        [
            (
                "Ranked episodes",
                [
                    f"- **{row['priority']}/100 — {row['title']}** "
                    f"(`{row['video_id']}`; {row['source']})"
                    for row in review_queue
                ],
            )
        ],
    )

    counts = {
        "episodes": len(list(episodes_root.glob("*.json"))),
        "people": len(list(people_root.glob("*.json"))),
        "shows": len(list(shows_root.glob("*.json"))),
        "stories": len(list(stories_root.glob("*.json"))),
        "relationships": len(list(relationships_root.glob("*.json"))),
    }

    save_json(
        candidates_root / "index.json",
        {
            "schema_version": "octopuss-candidates-index-v0.1",
            "generated_at": now_iso(),
            "counts": counts,
        },
    )

    print("OCTOPUSS compact candidate layer rebuilt.")
    for key, value in counts.items():
        print(f"{key.capitalize():14}: {value}")
    print(f"Review queue   : {len(review_queue)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
