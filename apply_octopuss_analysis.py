from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from radar_common import ROOT, load_json, save_json, now_iso


INTEL_ROOT = ROOT / "intelligence"
INBOX = INTEL_ROOT / "inbox"
PROCESSED = INTEL_ROOT / "processed"
REJECTED = INTEL_ROOT / "rejected"
EPISODES = INTEL_ROOT / "episodes"
PEOPLE = INTEL_ROOT / "people"
SHOWS = INTEL_ROOT / "shows"
STORIES = INTEL_ROOT / "stories"
FACTIONS = INTEL_ROOT / "factions"
CLAIMS = INTEL_ROOT / "claims"
QUOTES = INTEL_ROOT / "quotes"
STATE_PATH = INTEL_ROOT / "state.json"


ALLOWED_PARTICIPANT_ROLES = {
    "host",
    "co-host",
    "panelist",
    "guest",
    "caller",
    "interviewee",
    "producer",
    "unknown",
}

ALLOWED_IMPORTANCE = {"major", "secondary", "passing"}
ALLOWED_SENTIMENT = {
    "positive",
    "negative",
    "neutral",
    "mixed",
    "predominantly_positive",
    "predominantly_negative",
    "unclear",
}


def slugify(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-") or "unknown"


def ensure_dirs() -> None:
    for path in [
        INBOX, PROCESSED, REJECTED, EPISODES, PEOPLE, SHOWS,
        STORIES, FACTIONS, CLAIMS, QUOTES,
    ]:
        path.mkdir(parents=True, exist_ok=True)


def validate_analysis(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []

    if data.get("schema_version") != "octopuss-episode-analysis-v0.1":
        errors.append("Unsupported or missing schema_version")

    if not data.get("video_id"):
        errors.append("Missing video_id")

    if not data.get("title"):
        errors.append("Missing title")

    show = data.get("show")
    if not isinstance(show, dict) or not show.get("name"):
        errors.append("Missing show.name")

    if not isinstance(data.get("participants", []), list):
        errors.append("participants must be a list")

    if not isinstance(data.get("subjects", []), list):
        errors.append("subjects must be a list")

    participant_ids: set[str] = set()

    for index, row in enumerate(data.get("participants", []), start=1):
        role = row.get("role")
        entity_id = row.get("entity_id")
        confidence = row.get("confidence")

        if not entity_id or not row.get("name"):
            errors.append(f"participant {index}: missing entity_id or name")

        if role not in ALLOWED_PARTICIPANT_ROLES:
            errors.append(f"participant {index}: invalid role {role!r}")

        if not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
            errors.append(f"participant {index}: confidence must be 0..1")

        if entity_id:
            if entity_id in participant_ids:
                errors.append(f"duplicate participant entity_id: {entity_id}")
            participant_ids.add(entity_id)

    for index, row in enumerate(data.get("subjects", []), start=1):
        if not row.get("entity_id") or not row.get("name"):
            errors.append(f"subject {index}: missing entity_id or name")

        if row.get("importance") not in ALLOWED_IMPORTANCE:
            errors.append(
                f"subject {index}: invalid importance {row.get('importance')!r}"
            )

        if row.get("sentiment") not in ALLOWED_SENTIMENT:
            errors.append(
                f"subject {index}: invalid sentiment {row.get('sentiment')!r}"
            )

        if row.get("appeared") is not False and row.get("entity_id") not in participant_ids:
            errors.append(
                f"subject {index}: appeared=true but entity is not in participants"
            )

    return errors


def merge_unique(existing: list[dict], additions: list[dict], key_fields: tuple[str, ...]) -> list[dict]:
    result = list(existing)
    seen = {
        tuple(str(row.get(field, "")) for field in key_fields)
        for row in result
    }

    for row in additions:
        key = tuple(str(row.get(field, "")) for field in key_fields)
        if key not in seen:
            result.append(row)
            seen.add(key)

    return result


def evidence_text(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "No receipt recorded."

    parts = []
    for row in rows:
        timestamp = row.get("timestamp") or "unknown time"
        note = row.get("note") or row.get("text") or ""
        parts.append(f"- **{timestamp}:** {note}".rstrip())

    return "\n".join(parts)


def write_episode(data: dict[str, Any]) -> None:
    video_id = str(data["video_id"])
    save_json(EPISODES / f"{video_id}.json", data)

    show = data["show"]
    lines = [
        f"# {data['title']}",
        "",
        f"- **Video ID:** `{video_id}`",
        f"- **Show:** {show['name']}",
        f"- **Channel:** {show.get('channel') or 'Unknown'}",
        f"- **Analysed:** {now_iso()}",
        "",
        "## Episode summary",
        "",
        data.get("episode_summary") or "_No summary supplied._",
        "",
        "## Participants",
        "",
    ]

    if data.get("participants"):
        for row in data["participants"]:
            lines.append(
                f"- **{row['name']}** — {row['role']} "
                f"(confidence {row['confidence']:.0%})"
            )
    else:
        lines.append("_No participants identified._")

    lines.extend(["", "## Subjects discussed", ""])

    if data.get("subjects"):
        for row in data["subjects"]:
            appearance = "appeared" if row.get("appeared") else "not present / no appearance evidence"
            lines.append(
                f"- **{row['name']}** — {row['importance']}; "
                f"{row['sentiment']}; {appearance}; "
                f"confidence {row['confidence']:.0%}"
            )
    else:
        lines.append("_No subjects identified._")

    lines.extend(["", "## Chapters", ""])

    for row in data.get("chapters", []):
        lines.extend(
            [
                f"### {row.get('start', '?')}–{row.get('end', '?')} — {row.get('title', 'Untitled')}",
                "",
                row.get("summary") or "_No summary supplied._",
                "",
            ]
        )

    lines.extend(["## Story updates", ""])
    for row in data.get("story_updates", []):
        lines.extend(
            [
                f"### {row.get('title') or row.get('story_id')}",
                "",
                row.get("summary") or "_No summary supplied._",
                "",
                evidence_text(row.get("evidence", [])),
                "",
            ]
        )

    lines.extend(["## Uncertainties", ""])
    uncertainties = data.get("uncertainties", [])
    if uncertainties:
        for item in uncertainties:
            lines.append(f"- {item}")
    else:
        lines.append("_None recorded._")

    (EPISODES / f"{video_id}.md").write_text(
        "\n".join(lines).rstrip() + "\n",
        encoding="utf-8",
    )


def update_person_files(data: dict[str, Any]) -> None:
    video_id = str(data["video_id"])
    title = data["title"]
    show_name = data["show"]["name"]

    participant_map = {
        row["entity_id"]: row for row in data.get("participants", [])
    }
    subject_map = {
        row["entity_id"]: row for row in data.get("subjects", [])
    }
    all_ids = set(participant_map) | set(subject_map)

    for entity_id in all_ids:
        participant = participant_map.get(entity_id)
        subject = subject_map.get(entity_id)
        name = (
            (participant or {}).get("name")
            or (subject or {}).get("name")
            or entity_id
        )

        path = PEOPLE / f"{entity_id}.json"
        profile = load_json(
            path,
            {
                "entity_id": entity_id,
                "name": name,
                "aliases": [],
                "appearances": [],
                "discussions": [],
                "story_links": [],
                "updated_at": None,
            },
        )

        if participant:
            profile["appearances"] = merge_unique(
                profile.get("appearances", []),
                [
                    {
                        "video_id": video_id,
                        "episode_title": title,
                        "show": show_name,
                        "role": participant["role"],
                        "confidence": participant["confidence"],
                        "evidence": participant.get("evidence", []),
                    }
                ],
                ("video_id", "role"),
            )

        if subject:
            profile["discussions"] = merge_unique(
                profile.get("discussions", []),
                [
                    {
                        "video_id": video_id,
                        "episode_title": title,
                        "show": show_name,
                        "importance": subject["importance"],
                        "sentiment": subject["sentiment"],
                        "appeared": subject.get("appeared", False),
                        "confidence": subject["confidence"],
                        "topic_blocks": subject.get("topic_blocks", []),
                        "evidence": subject.get("evidence", []),
                    }
                ],
                ("video_id", "importance", "sentiment"),
            )

        story_links = []
        for story in data.get("story_updates", []):
            if entity_id in story.get("participants", []):
                story_links.append(
                    {
                        "story_id": story["story_id"],
                        "title": story.get("title"),
                        "video_id": video_id,
                    }
                )

        profile["story_links"] = merge_unique(
            profile.get("story_links", []),
            story_links,
            ("story_id", "video_id"),
        )
        profile["updated_at"] = now_iso()
        save_json(path, profile)

        lines = [
            f"# {profile['name']}",
            "",
            f"- **Entity ID:** `{entity_id}`",
            f"- **Updated:** {profile['updated_at']}",
            "",
            "## Appearances",
            "",
        ]

        if profile["appearances"]:
            for row in profile["appearances"]:
                lines.append(
                    f"- **{row['episode_title']}** — {row['role']} on "
                    f"{row['show']} (confidence {row['confidence']:.0%})"
                )
        else:
            lines.append("_No confirmed appearances._")

        lines.extend(["", "## Discussions about this person", ""])

        if profile["discussions"]:
            for row in profile["discussions"]:
                lines.append(
                    f"- **{row['episode_title']}** — {row['importance']} subject; "
                    f"{row['sentiment']}; confidence {row['confidence']:.0%}"
                )
        else:
            lines.append("_No recorded discussions._")

        lines.extend(["", "## Story links", ""])
        if profile["story_links"]:
            for row in profile["story_links"]:
                lines.append(f"- {row.get('title') or row['story_id']}")
        else:
            lines.append("_No story links._")

        (PEOPLE / f"{entity_id}.md").write_text(
            "\n".join(lines).rstrip() + "\n",
            encoding="utf-8",
        )


def update_show_file(data: dict[str, Any]) -> None:
    show = data["show"]
    show_id = show.get("id") or slugify(show["name"])
    path = SHOWS / f"{show_id}.json"

    record = load_json(
        path,
        {
            "show_id": show_id,
            "name": show["name"],
            "channel": show.get("channel"),
            "episodes": [],
            "appearance_history": [],
            "subject_history": [],
            "updated_at": None,
        },
    )

    video_id = str(data["video_id"])
    record["episodes"] = merge_unique(
        record.get("episodes", []),
        [
            {
                "video_id": video_id,
                "title": data["title"],
                "summary": data.get("episode_summary"),
            }
        ],
        ("video_id",),
    )

    appearance_rows = [
        {
            "video_id": video_id,
            "entity_id": row["entity_id"],
            "name": row["name"],
            "role": row["role"],
            "confidence": row["confidence"],
        }
        for row in data.get("participants", [])
    ]
    record["appearance_history"] = merge_unique(
        record.get("appearance_history", []),
        appearance_rows,
        ("video_id", "entity_id", "role"),
    )

    subject_rows = [
        {
            "video_id": video_id,
            "entity_id": row["entity_id"],
            "name": row["name"],
            "importance": row["importance"],
            "sentiment": row["sentiment"],
        }
        for row in data.get("subjects", [])
    ]
    record["subject_history"] = merge_unique(
        record.get("subject_history", []),
        subject_rows,
        ("video_id", "entity_id", "importance", "sentiment"),
    )

    record["updated_at"] = now_iso()
    save_json(path, record)

    lines = [
        f"# {record['name']}",
        "",
        f"- **Show ID:** `{show_id}`",
        f"- **Channel:** {record.get('channel') or 'Unknown'}",
        f"- **Updated:** {record['updated_at']}",
        "",
        "## Episodes",
        "",
    ]

    for row in record["episodes"]:
        lines.append(f"- **{row['title']}** (`{row['video_id']}`)")

    lines.extend(["", "## Appearance history", ""])
    for row in record["appearance_history"]:
        lines.append(
            f"- **{row['name']}** — {row['role']} in `{row['video_id']}`"
        )

    lines.extend(["", "## Subject history", ""])
    for row in record["subject_history"]:
        lines.append(
            f"- **{row['name']}** — {row['importance']}, "
            f"{row['sentiment']} in `{row['video_id']}`"
        )

    (SHOWS / f"{show_id}.md").write_text(
        "\n".join(lines).rstrip() + "\n",
        encoding="utf-8",
    )


def update_story_files(data: dict[str, Any]) -> None:
    video_id = str(data["video_id"])

    for row in data.get("story_updates", []):
        story_id = row.get("story_id") or slugify(row.get("title") or "story")
        path = STORIES / f"{story_id}.json"

        record = load_json(
            path,
            {
                "story_id": story_id,
                "title": row.get("title") or story_id,
                "status": row.get("status") or "unknown",
                "timeline": [],
                "participants": [],
                "updated_at": None,
            },
        )

        record["status"] = row.get("status") or record.get("status")
        record["participants"] = sorted(
            set(record.get("participants", []))
            | set(row.get("participants", []))
        )
        record["timeline"] = merge_unique(
            record.get("timeline", []),
            [
                {
                    "video_id": video_id,
                    "episode_title": data["title"],
                    "summary": row.get("summary"),
                    "evidence": row.get("evidence", []),
                    "recorded_at": now_iso(),
                }
            ],
            ("video_id",),
        )
        record["updated_at"] = now_iso()
        save_json(path, record)

        lines = [
            f"# {record['title']}",
            "",
            f"- **Story ID:** `{story_id}`",
            f"- **Status:** {record['status']}",
            f"- **Updated:** {record['updated_at']}",
            "",
            "## Participants",
            "",
        ]
        for entity_id in record["participants"]:
            lines.append(f"- `{entity_id}`")

        lines.extend(["", "## Timeline", ""])
        for event in record["timeline"]:
            lines.extend(
                [
                    f"### {event['episode_title']}",
                    "",
                    event.get("summary") or "_No summary supplied._",
                    "",
                    evidence_text(event.get("evidence", [])),
                    "",
                ]
            )

        (STORIES / f"{story_id}.md").write_text(
            "\n".join(lines).rstrip() + "\n",
            encoding="utf-8",
        )


def update_factions(data: dict[str, Any]) -> None:
    for row in data.get("faction_signals", []):
        faction_id = row.get("faction_id") or slugify(row.get("name") or "faction")
        path = FACTIONS / f"{faction_id}.json"
        record = load_json(
            path,
            {
                "faction_id": faction_id,
                "name": row.get("name") or faction_id,
                "signals": [],
                "updated_at": None,
            },
        )

        signal = dict(row)
        signal["video_id"] = data["video_id"]
        signal["episode_title"] = data["title"]

        record["signals"] = merge_unique(
            record.get("signals", []),
            [signal],
            ("video_id", "entity_id", "signal_type"),
        )
        record["updated_at"] = now_iso()
        save_json(path, record)

        lines = [
            f"# {record['name']}",
            "",
            f"- **Faction ID:** `{faction_id}`",
            f"- **Updated:** {record['updated_at']}",
            "",
            "## Signals",
            "",
        ]
        for signal in record["signals"]:
            lines.append(
                f"- **{signal.get('entity_name') or signal.get('entity_id')}** — "
                f"{signal.get('signal_type')} "
                f"(confidence {float(signal.get('confidence', 0)):.0%}) "
                f"in `{signal.get('video_id')}`"
            )

        (FACTIONS / f"{faction_id}.md").write_text(
            "\n".join(lines).rstrip() + "\n",
            encoding="utf-8",
        )


def write_claims_and_quotes(data: dict[str, Any]) -> None:
    for row in data.get("claims", []):
        claim_id = row.get("claim_id") or slugify(
            f"{data['video_id']}-{row.get('claimant')}-{row.get('claim')}"
        )
        row = dict(row)
        row["claim_id"] = claim_id
        row["video_id"] = data["video_id"]
        row["episode_title"] = data["title"]
        save_json(CLAIMS / f"{claim_id}.json", row)

    for row in data.get("quotes", []):
        quote_id = row.get("quote_id") or slugify(
            f"{data['video_id']}-{row.get('timestamp')}-{row.get('text')}"
        )
        row = dict(row)
        row["quote_id"] = quote_id
        row["video_id"] = data["video_id"]
        row["episode_title"] = data["title"]
        save_json(QUOTES / f"{quote_id}.json", row)


def process_file(path: Path) -> tuple[bool, str]:
    try:
        data = load_json(path, {})
        errors = validate_analysis(data)

        if errors:
            rejected_path = REJECTED / path.name
            rejected_path.write_text(
                json.dumps(
                    {"errors": errors, "analysis": data},
                    indent=2,
                    ensure_ascii=False,
                ) + "\n",
                encoding="utf-8",
            )
            path.unlink(missing_ok=True)
            return False, "; ".join(errors)

        write_episode(data)
        update_person_files(data)
        update_show_file(data)
        update_story_files(data)
        update_factions(data)
        write_claims_and_quotes(data)

        processed_path = PROCESSED / path.name
        processed_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        path.unlink(missing_ok=True)
        return True, str(data["video_id"])

    except Exception as exc:
        return False, str(exc)


def main() -> int:
    ensure_dirs()
    state = load_json(
        STATE_PATH,
        {
            "schema_version": "octopuss-filing-state-v0.1",
            "processed_video_ids": [],
            "last_run": None,
        },
    )

    files = sorted(
        path for path in INBOX.glob("*.json")
        if not path.name.startswith("EXAMPLE-")
    )

    if not files:
        print("OCTOPUSS filing: no analysis files waiting in intelligence/inbox.")
        return 0

    successes = 0
    failures = 0

    for path in files:
        ok, result = process_file(path)
        if ok:
            successes += 1
            state["processed_video_ids"] = sorted(
                set(state.get("processed_video_ids", [])) | {result}
            )
            print(f"FILED: {path.name} -> {result}")
        else:
            failures += 1
            print(f"REJECTED: {path.name}: {result}")

    state["last_run"] = now_iso()
    save_json(STATE_PATH, state)

    print(f"OCTOPUSS analyses filed: {successes}")
    print(f"OCTOPUSS analyses rejected: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
