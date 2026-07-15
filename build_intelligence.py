#!/usr/bin/env python3
"""
Drama Radar intelligence builder.

Scans local transcript JSON files and produces structured intelligence outputs:

- transcript-manifest.json
- mention-index.json
- entities.json
- relationships.json
- stories.json
- campaigns.json
- evidence-index.json
- processing-state.json

The script is deliberately dependency-free and safe to run repeatedly.
It uses file hashes to avoid reprocessing unchanged transcripts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote


DEFAULT_REPO = "commi3mark/RTF-drama-radar"
DEFAULT_BRANCH = "main"
TRANSCRIPT_ROOT = Path("transcripts")

OUTPUT_FILES = {
    "manifest": Path("transcript-manifest.json"),
    "mentions": Path("mention-index.json"),
    "entities": Path("entities.json"),
    "relationships": Path("relationships.json"),
    "stories": Path("stories.json"),
    "campaigns": Path("campaigns.json"),
    "evidence": Path("evidence-index.json"),
    "state": Path("processing-state.json"),
}

# Add aliases here as new identities are learned.
# Keep aliases specific enough to avoid false positives.
ENTITY_DEFINITIONS: dict[str, dict[str, Any]] = {
    "commi3-mark": {
        "name": "Commi3 Mark",
        "priority": 100,
        "aliases": [
            "Commi3 Mark",
            "Commie Mark",
            "Comey Mark",
            "Kami Mark",
            "Commy Mark",
            "Communist Mark",
        ],
        "platforms": {
            "x": ["commi3mark"],
            "youtube": ["commi3mark"],
        },
    },
    "ethan-van-sciver": {
        "name": "Ethan Van Sciver",
        "priority": 70,
        "aliases": [
            "Ethan Van Sciver",
            "Ethan Van Skyver",
            "Ethan Van Scriber",
            "EVS",
        ],
    },
    "liam-gray": {
        "name": "Liam Gray",
        "priority": 80,
        "aliases": [
            "Liam Gray",
            "Liam Grey",
        ],
    },
    "jon-del-arroz": {
        "name": "Jon Del Arroz",
        "priority": 60,
        "aliases": [
            "Jon Del Arroz",
            "John Del Arroz",
            "JDA",
        ],
    },
    "vito-gesualdi": {
        "name": "Vito Gesualdi",
        "priority": 60,
        "aliases": [
            "Vito Gesualdi",
            "Veto Gesualdi",
            "Vito",
        ],
    },
    "frog-tony": {
        "name": "Frog Tony",
        "priority": 80,
        "aliases": [
            "Frog Tony",
            "Frig Tony",
            "Tony the Frog",
        ],
    },
    "johnny-rocket": {
        "name": "Johnny Rocket",
        "priority": 70,
        "aliases": [
            "Johnny Rocket",
            "Johnny Rockets",
        ],
    },
    "katy": {
        "name": "Katy",
        "priority": 50,
        "aliases": [
            "Katy",
            "Katie",
        ],
    },
    "riley": {
        "name": "Riley",
        "priority": 50,
        "aliases": [
            "Riley",
            "Rylie",
        ],
    },
}

CAMPAIGN_TERMS = [
    "indiegogo",
    "kickstarter",
    "campaign",
    "back the book",
    "back this book",
    "pre-order",
    "preorder",
    "comic book",
    "graphic novel",
    "sign up page",
    "mailing list",
    "funding",
    "stretch goal",
]

STORY_TERMS: dict[str, list[str]] = {
    "fencegate": ["fencegate", "fence gate"],
    "false-flag": ["false flag", "false-flag"],
    "copyright-strike": ["copyright strike", "copyright claim", "dmca"],
    "doxxing": ["dox", "doxxing", "doxed"],
    "swatting": ["swat", "swatting"],
    "campaign-drama": [
        "campaign",
        "indiegogo",
        "kickstarter",
        "refund",
        "backer",
        "fulfillment",
        "fulfilment",
    ],
    "platform-ban": [
        "suspended",
        "unsuspended",
        "banned",
        "terminated",
        "channel strike",
    ],
}

BOILERPLATE_PATTERNS = [
    re.compile(r"\b(?:like|subscribe|share|hit the bell)\b", re.I),
    re.compile(r"\b(?:links? in the description|check the description)\b", re.I),
    re.compile(r"\b(?:superchat|super chat)\b", re.I),
]


@dataclass(frozen=True)
class Mention:
    entity_id: str
    canonical_name: str
    alias: str
    video_id: str
    title: str
    source: str | None
    published: str | None
    url: str | None
    transcript_file: str
    segment_index: int
    start: float
    timestamp: str
    quote: str
    context_before: str
    context_after: str
    receipt_url: str | None
    confidence: float


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (json.JSONDecodeError, OSError):
        return default


def write_json(path: Path, data: Any) -> None:
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with temp_path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    temp_path.replace(path)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def timestamp_text(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def youtube_timestamp_url(url: str | None, seconds: float) -> str | None:
    if not url:
        return None
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}t={max(0, int(seconds))}s"


def github_raw_url(repo: str, branch: str, relative_path: str) -> str:
    encoded = "/".join(quote(part, safe="") for part in relative_path.split("/"))
    return f"https://raw.githubusercontent.com/{repo}/refs/heads/{branch}/{encoded}"


def normalise_text(text: str) -> str:
    text = text.replace("\u2019", "'").replace("\u2018", "'")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def compile_alias_patterns() -> dict[str, list[tuple[str, re.Pattern[str]]]]:
    compiled: dict[str, list[tuple[str, re.Pattern[str]]]] = {}
    for entity_id, definition in ENTITY_DEFINITIONS.items():
        patterns = []
        for alias in definition["aliases"]:
            escaped = re.escape(alias)
            pattern = re.compile(rf"(?<!\w){escaped}(?!\w)", re.I)
            patterns.append((alias, pattern))
        compiled[entity_id] = patterns
    return compiled


ALIAS_PATTERNS = compile_alias_patterns()


def iter_transcript_files(root: Path) -> Iterable[Path]:
    if not root.exists():
        return []
    return sorted(
        path
        for path in root.rglob("*.json")
        if path.is_file() and not path.name.startswith(".")
    )


def extract_mentions(
    transcript: dict[str, Any],
    transcript_path: Path,
) -> list[Mention]:
    segments = transcript.get("segments") or []
    if not isinstance(segments, list):
        return []

    video_id = str(transcript.get("video_id") or "")
    title = str(transcript.get("title") or transcript_path.stem)
    source = transcript.get("source")
    published = transcript.get("published")
    url = transcript.get("url")
    relative_path = transcript_path.as_posix()

    mentions: list[Mention] = []

    for index, segment in enumerate(segments):
        if not isinstance(segment, dict):
            continue

        text = normalise_text(str(segment.get("text") or ""))
        if not text:
            continue

        before = ""
        after = ""
        if index > 0 and isinstance(segments[index - 1], dict):
            before = normalise_text(str(segments[index - 1].get("text") or ""))
        if index + 1 < len(segments) and isinstance(segments[index + 1], dict):
            after = normalise_text(str(segments[index + 1].get("text") or ""))

        start = float(segment.get("start") or 0)

        for entity_id, patterns in ALIAS_PATTERNS.items():
            definition = ENTITY_DEFINITIONS[entity_id]
            for alias, pattern in patterns:
                if not pattern.search(text):
                    continue

                confidence = 1.0
                if len(alias) <= 5:
                    confidence = 0.78
                if entity_id in {"katy", "riley"}:
                    confidence = min(confidence, 0.65)

                mentions.append(
                    Mention(
                        entity_id=entity_id,
                        canonical_name=definition["name"],
                        alias=alias,
                        video_id=video_id,
                        title=title,
                        source=source,
                        published=published,
                        url=url,
                        transcript_file=relative_path,
                        segment_index=index,
                        start=start,
                        timestamp=timestamp_text(start),
                        quote=text,
                        context_before=before,
                        context_after=after,
                        receipt_url=youtube_timestamp_url(url, start),
                        confidence=confidence,
                    )
                )
                break

    return mentions


def mention_to_dict(mention: Mention) -> dict[str, Any]:
    return {
        "entity_id": mention.entity_id,
        "canonical_name": mention.canonical_name,
        "matched_alias": mention.alias,
        "video_id": mention.video_id,
        "title": mention.title,
        "source": mention.source,
        "published": mention.published,
        "url": mention.url,
        "transcript_file": mention.transcript_file,
        "segment_index": mention.segment_index,
        "start": mention.start,
        "timestamp": mention.timestamp,
        "quote": mention.quote,
        "context_before": mention.context_before,
        "context_after": mention.context_after,
        "receipt_url": mention.receipt_url,
        "confidence": mention.confidence,
    }


def transcript_text(transcript: dict[str, Any]) -> str:
    parts = []
    for segment in transcript.get("segments") or []:
        if isinstance(segment, dict):
            parts.append(str(segment.get("text") or ""))
    return normalise_text(" ".join(parts))


def is_boilerplate(text: str) -> bool:
    if len(text) > 300:
        return False
    return any(pattern.search(text) for pattern in BOILERPLATE_PATTERNS)


def detect_story_tags(text: str) -> list[str]:
    lowered = text.lower()
    tags = []
    for story_id, terms in STORY_TERMS.items():
        if any(term in lowered for term in terms):
            tags.append(story_id)
    return tags


def detect_campaign_activity(
    transcript: dict[str, Any],
    mentions: list[Mention],
    transcript_path: Path,
) -> list[dict[str, Any]]:
    segments = transcript.get("segments") or []
    campaign_hits = []

    for index, segment in enumerate(segments):
        if not isinstance(segment, dict):
            continue
        text = normalise_text(str(segment.get("text") or ""))
        lowered = text.lower()
        matched_terms = sorted({term for term in CAMPAIGN_TERMS if term in lowered})
        if not matched_terms:
            continue
        if is_boilerplate(text):
            continue

        start = float(segment.get("start") or 0)
        nearby_entities = sorted(
            {
                mention.entity_id
                for mention in mentions
                if abs(mention.segment_index - index) <= 3
            }
        )

        campaign_hits.append(
            {
                "video_id": transcript.get("video_id"),
                "title": transcript.get("title"),
                "source": transcript.get("source"),
                "published": transcript.get("published"),
                "url": transcript.get("url"),
                "transcript_file": transcript_path.as_posix(),
                "segment_index": index,
                "start": start,
                "timestamp": timestamp_text(start),
                "quote": text,
                "matched_terms": matched_terms,
                "nearby_entities": nearby_entities,
                "receipt_url": youtube_timestamp_url(transcript.get("url"), start),
                "status": "unverified-current",
            }
        )

    return campaign_hits


def build_relationships(
    mentions_by_video: dict[str, list[Mention]],
) -> list[dict[str, Any]]:
    pair_data: dict[tuple[str, str], dict[str, Any]] = {}

    for video_id, mentions in mentions_by_video.items():
        entities = sorted({mention.entity_id for mention in mentions})
        if len(entities) < 2:
            continue

        first = mentions[0]
        for left, right in combinations(entities, 2):
            key = (left, right)
            record = pair_data.setdefault(
                key,
                {
                    "entity_a": left,
                    "entity_b": right,
                    "co_mention_count": 0,
                    "video_count": 0,
                    "videos": [],
                    "first_seen": None,
                    "last_seen": None,
                },
            )
            record["video_count"] += 1

            left_count = sum(1 for mention in mentions if mention.entity_id == left)
            right_count = sum(1 for mention in mentions if mention.entity_id == right)
            record["co_mention_count"] += min(left_count, right_count)
            record["videos"].append(
                {
                    "video_id": video_id,
                    "title": first.title,
                    "source": first.source,
                    "published": first.published,
                    "url": first.url,
                }
            )

            published = first.published
            if published:
                if record["first_seen"] is None or published < record["first_seen"]:
                    record["first_seen"] = published
                if record["last_seen"] is None or published > record["last_seen"]:
                    record["last_seen"] = published

    output = []
    for record in pair_data.values():
        record["entity_a_name"] = ENTITY_DEFINITIONS[record["entity_a"]]["name"]
        record["entity_b_name"] = ENTITY_DEFINITIONS[record["entity_b"]]["name"]
        record["strength"] = min(
            1.0,
            round(
                (record["video_count"] * 0.15)
                + (record["co_mention_count"] * 0.03),
                3,
            ),
        )
        output.append(record)

    return sorted(
        output,
        key=lambda item: (
            -item["strength"],
            -item["video_count"],
            item["entity_a"],
            item["entity_b"],
        ),
    )


def build_story_files(
    transcripts: list[dict[str, Any]],
    mentions_by_video: dict[str, list[Mention]],
) -> list[dict[str, Any]]:
    clusters: dict[str, dict[str, Any]] = {}

    for record in transcripts:
        tags = record.get("story_tags") or []
        for story_id in tags:
            cluster = clusters.setdefault(
                story_id,
                {
                    "story_id": story_id,
                    "title": story_id.replace("-", " ").title(),
                    "status": "developing",
                    "participants": set(),
                    "events": [],
                    "first_seen": None,
                    "last_seen": None,
                    "confidence": 0.55,
                },
            )

            video_id = record.get("video_id")
            mentions = mentions_by_video.get(video_id, [])
            cluster["participants"].update(
                mention.entity_id for mention in mentions
            )

            event = {
                "video_id": video_id,
                "title": record.get("title"),
                "source": record.get("source"),
                "published": record.get("published"),
                "url": record.get("url"),
                "transcript_file": record.get("transcript_file"),
                "receipt_count": len(mentions),
            }
            cluster["events"].append(event)

            published = record.get("published")
            if published:
                if cluster["first_seen"] is None or published < cluster["first_seen"]:
                    cluster["first_seen"] = published
                if cluster["last_seen"] is None or published > cluster["last_seen"]:
                    cluster["last_seen"] = published

    output = []
    for cluster in clusters.values():
        cluster["participants"] = sorted(cluster["participants"])
        cluster["participant_names"] = [
            ENTITY_DEFINITIONS[entity_id]["name"]
            for entity_id in cluster["participants"]
            if entity_id in ENTITY_DEFINITIONS
        ]
        cluster["events"] = sorted(
            cluster["events"],
            key=lambda event: event.get("published") or "",
        )
        cluster["confidence"] = min(
            0.95,
            round(0.55 + 0.05 * len(cluster["events"]), 2),
        )
        output.append(cluster)

    return sorted(
        output,
        key=lambda item: item.get("last_seen") or "",
        reverse=True,
    )


def build_entity_output(
    all_mentions: list[Mention],
) -> list[dict[str, Any]]:
    grouped: dict[str, list[Mention]] = defaultdict(list)
    for mention in all_mentions:
        grouped[mention.entity_id].append(mention)

    output = []
    for entity_id, definition in ENTITY_DEFINITIONS.items():
        mentions = grouped.get(entity_id, [])
        videos = {mention.video_id for mention in mentions if mention.video_id}
        sources = Counter(
            mention.source for mention in mentions if mention.source
        )
        dates = sorted(
            mention.published for mention in mentions if mention.published
        )

        output.append(
            {
                "entity_id": entity_id,
                "name": definition["name"],
                "aliases": definition["aliases"],
                "priority": definition.get("priority", 50),
                "platforms": definition.get("platforms", {}),
                "mention_count": len(mentions),
                "video_count": len(videos),
                "first_seen": dates[0] if dates else None,
                "last_seen": dates[-1] if dates else None,
                "top_sources": [
                    {"source": source, "count": count}
                    for source, count in sources.most_common(10)
                ],
            }
        )

    return sorted(
        output,
        key=lambda entity: (-entity["priority"], entity["name"]),
    )


def build_evidence_index(
    all_mentions: list[Mention],
    campaigns: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    evidence = []

    for mention in all_mentions:
        evidence.append(
            {
                "evidence_id": (
                    f"mention:{mention.video_id}:"
                    f"{mention.segment_index}:{mention.entity_id}"
                ),
                "type": "entity-mention",
                "entity_ids": [mention.entity_id],
                "video_id": mention.video_id,
                "title": mention.title,
                "source": mention.source,
                "published": mention.published,
                "timestamp": mention.timestamp,
                "quote": mention.quote,
                "url": mention.url,
                "receipt_url": mention.receipt_url,
                "transcript_file": mention.transcript_file,
                "confidence": mention.confidence,
            }
        )

    for campaign in campaigns:
        evidence.append(
            {
                "evidence_id": (
                    f"campaign:{campaign.get('video_id')}:"
                    f"{campaign.get('segment_index')}"
                ),
                "type": "campaign-activity",
                "entity_ids": campaign.get("nearby_entities", []),
                "video_id": campaign.get("video_id"),
                "title": campaign.get("title"),
                "source": campaign.get("source"),
                "published": campaign.get("published"),
                "timestamp": campaign.get("timestamp"),
                "quote": campaign.get("quote"),
                "url": campaign.get("url"),
                "receipt_url": campaign.get("receipt_url"),
                "transcript_file": campaign.get("transcript_file"),
                "confidence": 0.55,
            }
        )

    return sorted(
        evidence,
        key=lambda item: (
            item.get("published") or "",
            item.get("video_id") or "",
            item.get("timestamp") or "",
        ),
        reverse=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build Drama Radar intelligence outputs."
    )
    parser.add_argument(
        "--transcripts",
        default=str(TRANSCRIPT_ROOT),
        help="Transcript directory. Default: transcripts",
    )
    parser.add_argument(
        "--repo",
        default=DEFAULT_REPO,
        help=f"GitHub owner/repo. Default: {DEFAULT_REPO}",
    )
    parser.add_argument(
        "--branch",
        default=DEFAULT_BRANCH,
        help=f"GitHub branch. Default: {DEFAULT_BRANCH}",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Ignore saved hashes and rebuild all transcript intelligence.",
    )
    args = parser.parse_args()

    transcript_root = Path(args.transcripts)
    previous_state = load_json(OUTPUT_FILES["state"], {})
    previous_hashes = previous_state.get("files", {})

    manifest: list[dict[str, Any]] = []
    all_mentions: list[Mention] = []
    mentions_by_video: dict[str, list[Mention]] = defaultdict(list)
    transcript_records: list[dict[str, Any]] = []
    campaign_activity: list[dict[str, Any]] = []
    current_hashes: dict[str, dict[str, Any]] = {}

    files = list(iter_transcript_files(transcript_root))
    if not files:
        print(f"No transcript JSON files found in: {transcript_root}")
        return 1

    processed = 0
    unchanged = 0
    failed = 0

    for path in files:
        relative_path = path.as_posix()
        digest = file_sha256(path)
        prior = previous_hashes.get(relative_path, {})

        try:
            transcript = load_json(path, None)
            if not isinstance(transcript, dict):
                raise ValueError("Transcript root is not a JSON object")

            mentions = extract_mentions(transcript, path)
            text = transcript_text(transcript)
            story_tags = detect_story_tags(text)
            campaigns = detect_campaign_activity(transcript, mentions, path)

            video_id = str(transcript.get("video_id") or "")
            segment_count = len(transcript.get("segments") or [])
            raw_url = github_raw_url(args.repo, args.branch, relative_path)

            manifest_record = {
                "video_id": video_id,
                "title": transcript.get("title"),
                "source": transcript.get("source"),
                "published": transcript.get("published"),
                "url": transcript.get("url"),
                "language": transcript.get("language"),
                "is_generated": transcript.get("is_generated"),
                "segment_count": segment_count,
                "transcript_file": relative_path,
                "raw_url": raw_url,
                "sha256": digest,
                "mention_count": len(mentions),
                "entities": sorted({mention.entity_id for mention in mentions}),
                "story_tags": story_tags,
                "campaign_hit_count": len(campaigns),
                "processed_at": now_iso(),
            }

            manifest.append(manifest_record)
            transcript_records.append(manifest_record)
            all_mentions.extend(mentions)
            mentions_by_video[video_id].extend(mentions)
            campaign_activity.extend(campaigns)

            unchanged_file = (
                not args.force
                and prior.get("sha256") == digest
            )
            if unchanged_file:
                unchanged += 1
            else:
                processed += 1

            current_hashes[relative_path] = {
                "sha256": digest,
                "video_id": video_id,
                "processed_at": now_iso(),
                "status": "ok",
            }

        except Exception as error:
            failed += 1
            current_hashes[relative_path] = {
                "sha256": digest,
                "processed_at": now_iso(),
                "status": "failed",
                "error": f"{error.__class__.__name__}: {error}",
            }
            print(f"FAILED: {relative_path}: {error}", file=sys.stderr)

    manifest.sort(
        key=lambda item: item.get("published") or "",
        reverse=True,
    )
    all_mentions.sort(
        key=lambda mention: (
            mention.published or "",
            mention.video_id,
            mention.start,
        ),
        reverse=True,
    )
    campaign_activity.sort(
        key=lambda item: (
            item.get("published") or "",
            item.get("start") or 0,
        ),
        reverse=True,
    )

    relationships = build_relationships(mentions_by_video)
    stories = build_story_files(transcript_records, mentions_by_video)
    entities = build_entity_output(all_mentions)
    evidence = build_evidence_index(all_mentions, campaign_activity)

    mention_output = {
        "generated_at": now_iso(),
        "schema_version": 1,
        "total_mentions": len(all_mentions),
        "mentions": [mention_to_dict(mention) for mention in all_mentions],
    }

    entity_output = {
        "generated_at": now_iso(),
        "schema_version": 1,
        "entities": entities,
    }

    relationship_output = {
        "generated_at": now_iso(),
        "schema_version": 1,
        "relationships": relationships,
    }

    story_output = {
        "generated_at": now_iso(),
        "schema_version": 1,
        "stories": stories,
    }

    campaign_output = {
        "generated_at": now_iso(),
        "schema_version": 1,
        "warning": (
            "Campaign hits are leads, not confirmation that campaigns "
            "are currently active. Verify against live campaign and X data."
        ),
        "campaign_activity": campaign_activity,
    }

    evidence_output = {
        "generated_at": now_iso(),
        "schema_version": 1,
        "evidence": evidence,
    }

    state_output = {
        "generated_at": now_iso(),
        "schema_version": 1,
        "transcript_root": transcript_root.as_posix(),
        "file_count": len(files),
        "processed_changed_files": processed,
        "unchanged_files": unchanged,
        "failed_files": failed,
        "files": current_hashes,
    }

    write_json(OUTPUT_FILES["manifest"], manifest)
    write_json(OUTPUT_FILES["mentions"], mention_output)
    write_json(OUTPUT_FILES["entities"], entity_output)
    write_json(OUTPUT_FILES["relationships"], relationship_output)
    write_json(OUTPUT_FILES["stories"], story_output)
    write_json(OUTPUT_FILES["campaigns"], campaign_output)
    write_json(OUTPUT_FILES["evidence"], evidence_output)
    write_json(OUTPUT_FILES["state"], state_output)

    print()
    print("Drama Radar intelligence build complete.")
    print(f"Transcript files: {len(files)}")
    print(f"Changed/new: {processed}")
    print(f"Unchanged: {unchanged}")
    print(f"Failed: {failed}")
    print(f"Mentions: {len(all_mentions)}")
    print(f"Relationships: {len(relationships)}")
    print(f"Stories: {len(stories)}")
    print(f"Campaign leads: {len(campaign_activity)}")
    print()
    for name, path in OUTPUT_FILES.items():
        print(f"{name:14}: {path}")

    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
