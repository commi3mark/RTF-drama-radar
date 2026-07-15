from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse, parse_qs

from youtube_transcript_api import YouTubeTranscriptApi


# ============================================================
# CONFIGURATION
# ============================================================

ROOT = Path(__file__).resolve().parent

RADAR_FILE = ROOT / "drama-radar.json"
TRANSCRIPTS_DIR = ROOT / "transcripts"

MANIFEST_FILE = ROOT / "transcript-manifest.json"
RETRY_FILE = ROOT / "transcript-retries.json"
MENTION_INDEX_FILE = ROOT / "mention-index.json"

GITHUB_OWNER = "commi3mark"
GITHUB_REPOSITORY = "RTF-drama-radar"
GITHUB_BRANCH = "main"

PREFERRED_LANGUAGES = ["en", "en-US", "en-GB"]

MINIMUM_WAIT_SECONDS = 16
MAXIMUM_WAIT_SECONDS = 26

MAX_RECEIPTS_PER_NAME_PER_VIDEO = 10

# A failed transcript remains retryable for this long after publication.
FINAL_FAILURE_AFTER_HOURS = 48

# Retry stages measured from the video's publication time.
RETRY_AFTER_MINUTES = [
    30,
    120,
    360,
    720,
    1440,
    2880,
]


# ============================================================
# KNOWN PEOPLE, PROJECTS AND ALIASES
# ============================================================
#
# The dictionary key is the canonical name stored in the index.
# Every item in the list is an alternative transcription that
# should count as that same person or subject.
#
# Add new names here as Drama Radar learns the community.
#

ENTITY_ALIASES: dict[str, list[str]] = {
    "Ethan Van Sciver": [
        "Ethan Van Sciver",
        "Ethan Van Skyver",
        "Ethan Van Skyver",
        "Ethan",
        "EVS",
    ],
    "Eric July": [
        "Eric July",
        "Eric Julio",
        "Eric Julie",
        "Young Rippa",
        "Young Ripa",
        "Rippa",
    ],
    "Liam Gray": [
        "Liam Gray",
        "Liam Grey",
        "Liam",
    ],
    "KatyDid": [
        "KatyDid",
        "Katy Did",
        "Katydid",
        "Katy",
    ],
    "Frog Tony": [
        "Frog Tony",
        "FrogTony",
        "Frog",
        "Tony",
    ],
    "Jon Malin": [
        "Jon Malin",
        "John Malin",
        "John Mailin",
        "John Mailen",
        "John Milan",
        "Malin",
    ],
    "Shane Davis": [
        "Shane Davis",
        "Shane",
    ],
    "Yanzi Lin": [
        "Yanzi Lin",
        "Yanzi",
        "Yonzi",
    ],
    "Jon Del Arroz": [
        "Jon Del Arroz",
        "John Del Arroz",
        "Jon Dela Rose",
        "John Dela Rose",
        "JDA",
    ],
    "Chrissie Mayr": [
        "Chrissie Mayr",
        "Chrissy Mayr",
        "Chrissy Mayor",
        "Chrissie",
        "Chrissy",
    ],
    "Nerdrotic": [
        "Nerdrotic",
        "Gary Buechler",
        "Gary",
    ],
    "TheQuartering": [
        "TheQuartering",
        "The Quartering",
        "Jeremy Hambly",
        "Jeremy",
    ],
    "Nick Rekieta": [
        "Nick Rekieta",
        "Nick Rekeita",
        "Nick Ricada",
        "Nick Roccata",
        "Nick Ricotta",
        "Rekieta",
    ],
    "Aaron Imholte": [
        "Aaron Imholte",
        "Aaron",
        "Steel Toe",
    ],
    "Vito Gesualdi": [
        "Vito Gesualdi",
        "Vito",
    ],
    "Dick Masterson": [
        "Dick Masterson",
        "Dick Mastersonson",
        "Dick",
    ],
    "Sturgis": [
        "Sturgis",
        "Sturgis's",
        "Sturgis’",
    ],
    "DarkGift Comics": [
        "DarkGift Comics",
        "Dark Gift Comics",
        "DarkGift",
        "Dark Gift",
    ],
    "Stray Beans": [
        "Stray Beans",
        "Straight Beans",
        "Straybeans",
    ],
    "Yellow Flash": [
        "Yellow Flash",
    ],
    "Little Movie Perp": [
        "Little Movie Perp",
        "Little Movie Purp",
    ],
    "Cecil": [
        "Cecil",
    ],
    "Anna That Star Wars Girl": [
        "Anna That Star Wars Girl",
        "That Star Wars Girl",
        "Anna TSWG",
    ],
    "BackMeBro": [
        "BackMeBro",
        "Back Me Bro",
        "Backmebro",
    ],
    "Rippaverse": [
        "Rippaverse",
        "Rippa Verse",
        "Ripaverse",
    ],
    "ComicsGate": [
        "ComicsGate",
        "Comics Gate",
        "ComicGate",
        "Comic Gate",
    ],
    "Cyberfrog": [
        "Cyberfrog",
        "Cyber Frog",
    ],
    "Alpha Core": [
        "Alpha Core",
        "Alphacore",
    ],
    "Friday Night Tights": [
        "Friday Night Tights",
        "FNT",
    ],
    "Kino Casino": [
        "Kino Casino",
    ],
    "Trashcast": [
        "Trashcast",
        "Trash Cast",
        "Trash Cash",
    ],
    "Dropbox Drama": [
        "Dropbox Drama",
        "Dropbox",
        "Slick Jimmy's Dropbox",
        "Slick Jimmy’s Dropbox",
    ],
    "Fencegate": [
        "Fencegate",
        "Fence Gate",
        "the fence",
        "picket fence",
    ],
}


# Aliases this short or generic must only be counted with exact
# word boundaries. They can still produce false positives, so
# remove any that prove too noisy.
GENERIC_ALIASES = {
    "ethan",
    "liam",
    "katy",
    "frog",
    "tony",
    "shane",
    "gary",
    "jeremy",
    "aaron",
    "vito",
    "dick",
    "cecil",
    "anna",
    "rippa",
    "malin",
}


# ============================================================
# BASIC FILE HELPERS
# ============================================================

def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None

    return value.astimezone(timezone.utc).isoformat()


def parse_datetime(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None

    cleaned = value.strip().replace("Z", "+00:00")

    try:
        parsed = datetime.fromisoformat(cleaned)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    return parsed.astimezone(timezone.utc)


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default

    try:
        with path.open("r", encoding="utf-8") as file:
            return json.load(file)
    except (json.JSONDecodeError, OSError) as error:
        print(f"WARNING - could not read {path.name}: {error}")
        return default


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    temporary_path = path.with_suffix(path.suffix + ".tmp")

    with temporary_path.open("w", encoding="utf-8") as file:
        json.dump(
            data,
            file,
            ensure_ascii=False,
            indent=2,
        )
        file.write("\n")

    temporary_path.replace(path)


def safe_filename(value: str, maximum_length: int = 150) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", value)
    value = re.sub(r"\s+", " ", value).strip()
    value = value.rstrip(". ")

    if not value:
        value = "untitled"

    return value[:maximum_length].rstrip()


def relative_posix_path(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def raw_github_url(relative_path: str) -> str:
    encoded_parts = [
        quote(part, safe="")
        for part in Path(relative_path).as_posix().split("/")
    ]

    encoded_path = "/".join(encoded_parts)

    return (
        f"https://raw.githubusercontent.com/"
        f"{GITHUB_OWNER}/{GITHUB_REPOSITORY}/"
        f"refs/heads/{GITHUB_BRANCH}/{encoded_path}"
    )


# ============================================================
# VIDEO HELPERS
# ============================================================

def extract_video_id(url: str | None) -> str | None:
    if not url:
        return None

    url = url.strip()

    direct_match = re.fullmatch(r"[A-Za-z0-9_-]{11}", url)
    if direct_match:
        return url

    try:
        parsed = urlparse(url)
    except ValueError:
        return None

    hostname = parsed.netloc.lower().replace("www.", "")

    if hostname == "youtu.be":
        candidate = parsed.path.strip("/").split("/")[0]
        return candidate if candidate else None

    if hostname in {"youtube.com", "m.youtube.com", "music.youtube.com"}:
        if parsed.path == "/watch":
            return parse_qs(parsed.query).get("v", [None])[0]

        path_parts = [part for part in parsed.path.split("/") if part]

        if len(path_parts) >= 2 and path_parts[0] in {
            "shorts",
            "live",
            "embed",
        }:
            return path_parts[1]

    query_match = re.search(r"(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})", url)
    if query_match:
        return query_match.group(1)

    return None


def find_youtube_items(radar_data: Any) -> list[dict[str, Any]]:
    if not isinstance(radar_data, list):
        raise ValueError("drama-radar.json must contain a JSON list.")

    items: list[dict[str, Any]] = []

    for item in radar_data:
        if not isinstance(item, dict):
            continue

        platform = str(item.get("platform", "")).lower()
        url = item.get("url")
        video_id = extract_video_id(url)

        if not video_id:
            continue

        if platform and platform != "youtube":
            continue

        copied_item = dict(item)
        copied_item["video_id"] = video_id
        items.append(copied_item)

    return items


# ============================================================
# TRANSCRIPT API COMPATIBILITY
# ============================================================

def serialise_fetched_transcript(fetched: Any) -> list[dict[str, Any]]:
    if hasattr(fetched, "to_raw_data"):
        raw_data = fetched.to_raw_data()
    else:
        raw_data = fetched

    segments: list[dict[str, Any]] = []

    for segment in raw_data:
        if isinstance(segment, dict):
            text = segment.get("text", "")
            start = segment.get("start", 0)
            duration = segment.get("duration", 0)
        else:
            text = getattr(segment, "text", "")
            start = getattr(segment, "start", 0)
            duration = getattr(segment, "duration", 0)

        segments.append(
            {
                "start": float(start or 0),
                "duration": float(duration or 0),
                "text": str(text or "").strip(),
            }
        )

    return segments


def fetch_transcript(video_id: str) -> tuple[list[dict[str, Any]], str, bool]:
    """
    Returns:
        segments
        language code
        whether captions are automatically generated
    """

    api = YouTubeTranscriptApi()

    # Newer youtube-transcript-api releases.
    if hasattr(api, "list"):
        transcript_list = api.list(video_id)

        transcript = None

        try:
            transcript = transcript_list.find_manually_created_transcript(
                PREFERRED_LANGUAGES
            )
        except Exception:
            pass

        if transcript is None:
            try:
                transcript = transcript_list.find_generated_transcript(
                    PREFERRED_LANGUAGES
                )
            except Exception:
                pass

        if transcript is None:
            transcript = transcript_list.find_transcript(
                PREFERRED_LANGUAGES
            )

        fetched = transcript.fetch()

        return (
            serialise_fetched_transcript(fetched),
            str(getattr(transcript, "language_code", "en")),
            bool(getattr(transcript, "is_generated", False)),
        )

    # Compatibility with older youtube-transcript-api releases.
    if hasattr(YouTubeTranscriptApi, "list_transcripts"):
        transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)

        transcript = None

        try:
            transcript = transcript_list.find_manually_created_transcript(
                PREFERRED_LANGUAGES
            )
        except Exception:
            pass

        if transcript is None:
            try:
                transcript = transcript_list.find_generated_transcript(
                    PREFERRED_LANGUAGES
                )
            except Exception:
                pass

        if transcript is None:
            transcript = transcript_list.find_transcript(
                PREFERRED_LANGUAGES
            )

        fetched = transcript.fetch()

        return (
            serialise_fetched_transcript(fetched),
            str(getattr(transcript, "language_code", "en")),
            bool(getattr(transcript, "is_generated", False)),
        )

    # Final fallback for much older releases.
    raw_data = YouTubeTranscriptApi.get_transcript(
        video_id,
        languages=PREFERRED_LANGUAGES,
    )

    return serialise_fetched_transcript(raw_data), "en", True


# ============================================================
# RETRY HANDLING
# ============================================================

def classify_error(error: Exception) -> str:
    error_name = type(error).__name__
    error_text = str(error).lower()

    combined = f"{error_name} {error_text}"

    classifications = [
        ("IpBlocked", ["ipblocked", "ip blocked"]),
        ("RequestBlocked", ["requestblocked", "request blocked"]),
        (
            "TranscriptsDisabled",
            [
                "transcriptsdisabled",
                "transcripts are disabled",
                "subtitles are disabled",
            ],
        ),
        (
            "NoTranscriptFound",
            [
                "notranscriptfound",
                "no transcript",
                "could not find a transcript",
            ],
        ),
        (
            "VideoUnavailable",
            [
                "videounavailable",
                "video unavailable",
            ],
        ),
        (
            "AgeRestricted",
            [
                "agereestricted",
                "agerestricted",
                "age restricted",
            ],
        ),
    ]

    for classification, patterns in classifications:
        if any(pattern in combined for pattern in patterns):
            return classification

    return error_name or "UnknownError"


def publication_age_minutes(item: dict[str, Any], now: datetime) -> float | None:
    published = parse_datetime(item.get("published"))

    if published is None:
        return None

    return max(0.0, (now - published).total_seconds() / 60)


def next_retry_time(
    published: datetime | None,
    attempts: int,
    now: datetime,
) -> datetime:
    stage_index = min(max(attempts - 1, 0), len(RETRY_AFTER_MINUTES) - 1)
    retry_minutes = RETRY_AFTER_MINUTES[stage_index]

    if published is not None:
        scheduled = published + timedelta(minutes=retry_minutes)

        if scheduled > now:
            return scheduled

    # If the scheduled stage has already passed, give the block time
    # to clear instead of hammering YouTube immediately.
    return now + timedelta(minutes=retry_minutes)


def should_attempt_video(
    item: dict[str, Any],
    retry_record: dict[str, Any] | None,
    transcript_exists: bool,
    force: bool,
    now: datetime,
) -> tuple[bool, str]:
    if force:
        return True, "forced"

    if transcript_exists:
        return False, "already archived"

    if not retry_record:
        return True, "first attempt"

    status = retry_record.get("status")

    if status == "success":
        return False, "already successful"

    if status == "permanent_failure":
        return False, "permanent failure"

    next_retry = parse_datetime(retry_record.get("next_retry"))

    if next_retry and now < next_retry:
        return False, f"retry due {iso_datetime(next_retry)}"

    return True, "retry due"


def update_retry_failure(
    retries: dict[str, Any],
    item: dict[str, Any],
    error: Exception,
    now: datetime,
) -> dict[str, Any]:
    video_id = item["video_id"]
    previous = retries.get(video_id, {})

    attempts = int(previous.get("attempts", 0)) + 1
    published = parse_datetime(item.get("published"))

    age_hours: float | None = None

    if published is not None:
        age_hours = max(0.0, (now - published).total_seconds() / 3600)

    reason = classify_error(error)

    final_reasons = {
        "VideoUnavailable",
        "AgeRestricted",
    }

    old_enough = (
        age_hours is not None
        and age_hours >= FINAL_FAILURE_AFTER_HOURS
    )

    exhausted_stages = attempts >= len(RETRY_AFTER_MINUTES)

    permanent = reason in final_reasons or (
        old_enough and exhausted_stages
    )

    record = {
        "video_id": video_id,
        "title": item.get("title"),
        "source": item.get("source"),
        "published": item.get("published"),
        "url": item.get("url"),
        "status": "permanent_failure" if permanent else "pending",
        "reason": reason,
        "attempts": attempts,
        "first_attempt": previous.get("first_attempt") or iso_datetime(now),
        "last_attempt": iso_datetime(now),
        "next_retry": (
            None
            if permanent
            else iso_datetime(next_retry_time(published, attempts, now))
        ),
        "age_hours_at_attempt": (
            round(age_hours, 2)
            if age_hours is not None
            else None
        ),
        "error": str(error),
    }

    retries[video_id] = record
    return record


def update_retry_success(
    retries: dict[str, Any],
    item: dict[str, Any],
    now: datetime,
) -> None:
    video_id = item["video_id"]
    previous = retries.get(video_id, {})

    retries[video_id] = {
        "video_id": video_id,
        "title": item.get("title"),
        "source": item.get("source"),
        "published": item.get("published"),
        "url": item.get("url"),
        "status": "success",
        "reason": None,
        "attempts": int(previous.get("attempts", 0)) + 1,
        "first_attempt": previous.get("first_attempt") or iso_datetime(now),
        "last_attempt": iso_datetime(now),
        "completed": iso_datetime(now),
        "next_retry": None,
        "error": None,
    }


# ============================================================
# MENTION ANALYSIS
# ============================================================

def normalise_for_matching(text: str) -> str:
    text = text.replace("’", "'")
    text = text.replace("‘", "'")
    text = text.replace("“", '"')
    text = text.replace("”", '"')
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def alias_pattern(alias: str) -> re.Pattern[str]:
    alias = normalise_for_matching(alias)

    escaped = re.escape(alias)

    # Permit flexible spaces where auto-captions have split words.
    escaped = escaped.replace(r"\ ", r"\s+")

    return re.compile(
        rf"(?<![A-Za-z0-9]){escaped}(?![A-Za-z0-9])",
        flags=re.IGNORECASE,
    )


COMPILED_ENTITY_PATTERNS: dict[str, list[tuple[str, re.Pattern[str]]]] = {
    entity: [
        (alias, alias_pattern(alias))
        for alias in aliases
    ]
    for entity, aliases in ENTITY_ALIASES.items()
}


def find_segment_mentions(text: str) -> Counter[str]:
    cleaned = normalise_for_matching(text)
    counts: Counter[str] = Counter()

    for entity, aliases in COMPILED_ENTITY_PATTERNS.items():
        entity_matches: list[tuple[int, int, str]] = []

        # Longer aliases are checked first to avoid counting
        # "Eric" inside "Eric July" more than once.
        sorted_aliases = sorted(
            aliases,
            key=lambda pair: len(pair[0]),
            reverse=True,
        )

        occupied_ranges: list[tuple[int, int]] = []

        for alias, pattern in sorted_aliases:
            for match in pattern.finditer(cleaned):
                start, end = match.span()

                overlaps = any(
                    start < occupied_end and end > occupied_start
                    for occupied_start, occupied_end in occupied_ranges
                )

                if overlaps:
                    continue

                occupied_ranges.append((start, end))
                entity_matches.append((start, end, alias))

        counts[entity] = len(entity_matches)

    return Counter(
        {
            entity: count
            for entity, count in counts.items()
            if count > 0
        }
    )


def make_receipt(
    segment: dict[str, Any],
    entity: str,
) -> dict[str, Any]:
    start = float(segment.get("start", 0) or 0)
    duration = float(segment.get("duration", 0) or 0)
    text = str(segment.get("text", "")).strip()

    return {
        "entity": entity,
        "start": round(start, 3),
        "end": round(start + duration, 3),
        "timestamp": seconds_to_timestamp(start),
        "text": text,
    }


def seconds_to_timestamp(seconds: float) -> str:
    total_seconds = max(0, int(seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)

    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    return f"{minutes:02d}:{seconds:02d}"


def analyse_mentions(
    segments: list[dict[str, Any]],
) -> tuple[dict[str, int], dict[str, list[dict[str, Any]]]]:
    totals: Counter[str] = Counter()
    receipts: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for segment in segments:
        text = str(segment.get("text", "")).strip()

        if not text:
            continue

        segment_counts = find_segment_mentions(text)

        for entity, count in segment_counts.items():
            totals[entity] += count

            if len(receipts[entity]) < MAX_RECEIPTS_PER_NAME_PER_VIDEO:
                receipts[entity].append(
                    make_receipt(segment, entity)
                )

    sorted_totals = dict(
        sorted(
            totals.items(),
            key=lambda item: (-item[1], item[0].lower()),
        )
    )

    sorted_receipts = {
        entity: receipts[entity]
        for entity in sorted_totals
    }

    return sorted_totals, sorted_receipts


# ============================================================
# TRANSCRIPT FILE HANDLING
# ============================================================

def transcript_output_path(
    item: dict[str, Any],
) -> Path:
    published = parse_datetime(item.get("published"))

    if published:
        folder = TRANSCRIPTS_DIR / published.strftime("%Y-%m")
    else:
        folder = TRANSCRIPTS_DIR / "undated"

    title = safe_filename(
        str(item.get("title") or item["video_id"])
    )

    filename = f"{title} [{item['video_id']}].json"

    return folder / filename


def find_existing_transcript(video_id: str) -> Path | None:
    if not TRANSCRIPTS_DIR.exists():
        return None

    exact_matches = list(
        TRANSCRIPTS_DIR.rglob(f"*[{video_id}].json")
    )

    if exact_matches:
        return exact_matches[0]

    old_style = TRANSCRIPTS_DIR / f"{video_id}.json"

    if old_style.exists():
        return old_style

    for candidate in TRANSCRIPTS_DIR.rglob("*.json"):
        try:
            data = load_json(candidate, {})
        except Exception:
            continue

        if isinstance(data, dict) and data.get("video_id") == video_id:
            return candidate

    return None


def extract_segments_from_file(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [
            segment
            for segment in data
            if isinstance(segment, dict)
        ]

    if not isinstance(data, dict):
        return []

    for key in ("segments", "transcript", "captions"):
        value = data.get(key)

        if isinstance(value, list):
            return [
                segment
                for segment in value
                if isinstance(segment, dict)
            ]

    return []


def build_transcript_document(
    item: dict[str, Any],
    segments: list[dict[str, Any]],
    language: str,
    is_generated: bool,
    downloaded_at: datetime,
) -> dict[str, Any]:
    mention_counts, mention_receipts = analyse_mentions(segments)

    return {
        "video_id": item["video_id"],
        "title": item.get("title"),
        "source": item.get("source"),
        "platform": item.get("platform", "YouTube"),
        "published": item.get("published"),
        "url": item.get("url"),
        "language": language,
        "is_generated": is_generated,
        "downloaded_at": iso_datetime(downloaded_at),
        "segment_count": len(segments),
        "mention_counts": mention_counts,
        "mention_receipts": mention_receipts,
        "segments": segments,
    }


def enrich_existing_document(
    path: Path,
    item: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    data = load_json(path, {})

    segments = extract_segments_from_file(data)

    if not segments:
        return None

    if isinstance(data, list):
        data = {
            "video_id": item.get("video_id") if item else None,
            "title": item.get("title") if item else path.stem,
            "source": item.get("source") if item else None,
            "published": item.get("published") if item else None,
            "url": item.get("url") if item else None,
            "language": "en",
            "is_generated": None,
            "segment_count": len(segments),
            "segments": segments,
        }

    if item:
        data.setdefault("video_id", item.get("video_id"))
        data.setdefault("title", item.get("title"))
        data.setdefault("source", item.get("source"))
        data.setdefault("published", item.get("published"))
        data.setdefault("url", item.get("url"))

    mention_counts, mention_receipts = analyse_mentions(segments)

    data["segment_count"] = len(segments)
    data["mention_counts"] = mention_counts
    data["mention_receipts"] = mention_receipts
    data["segments"] = segments

    save_json(path, data)
    return data


# ============================================================
# MANIFEST AND GLOBAL MENTION INDEX
# ============================================================

def build_manifest_entry(
    transcript_path: Path,
    document: dict[str, Any],
) -> dict[str, Any]:
    relative_path = relative_posix_path(transcript_path)

    return {
        "video_id": document.get("video_id"),
        "title": document.get("title"),
        "source": document.get("source"),
        "published": document.get("published"),
        "url": document.get("url"),
        "language": document.get("language"),
        "is_generated": document.get("is_generated"),
        "segment_count": document.get("segment_count", 0),
        "transcript_file": relative_path,
        "raw_url": raw_github_url(relative_path),
        "mention_counts": document.get("mention_counts", {}),
    }


def scan_all_transcripts(
    radar_items_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    manifest_entries: list[dict[str, Any]] = []

    if not TRANSCRIPTS_DIR.exists():
        return manifest_entries

    for path in sorted(TRANSCRIPTS_DIR.rglob("*.json")):
        existing_data = load_json(path, {})
        existing_video_id = (
            existing_data.get("video_id")
            if isinstance(existing_data, dict)
            else None
        )

        if not existing_video_id:
            match = re.search(r"\[([A-Za-z0-9_-]{11})\]\.json$", path.name)

            if match:
                existing_video_id = match.group(1)
            elif re.fullmatch(r"[A-Za-z0-9_-]{11}\.json", path.name):
                existing_video_id = path.stem

        item = (
            radar_items_by_id.get(existing_video_id)
            if existing_video_id
            else None
        )

        document = enrich_existing_document(path, item)

        if document is None:
            print(f"WARNING - no transcript segments in {path}")
            continue

        manifest_entries.append(
            build_manifest_entry(path, document)
        )

    manifest_entries.sort(
        key=lambda entry: (
            entry.get("published") or "",
            entry.get("title") or "",
        ),
        reverse=True,
    )

    return manifest_entries


def build_global_mention_index(
    manifest_entries: list[dict[str, Any]],
) -> dict[str, Any]:
    global_totals: Counter[str] = Counter()
    by_source: dict[str, Counter[str]] = defaultdict(Counter)
    by_video: list[dict[str, Any]] = []
    entity_videos: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for entry in manifest_entries:
        source = str(entry.get("source") or "Unknown")
        mention_counts = entry.get("mention_counts", {})

        if not isinstance(mention_counts, dict):
            continue

        video_record = {
            "video_id": entry.get("video_id"),
            "title": entry.get("title"),
            "source": source,
            "published": entry.get("published"),
            "url": entry.get("url"),
            "transcript_file": entry.get("transcript_file"),
            "mentions": mention_counts,
        }

        by_video.append(video_record)

        for entity, raw_count in mention_counts.items():
            try:
                count = int(raw_count)
            except (TypeError, ValueError):
                continue

            if count <= 0:
                continue

            global_totals[entity] += count
            by_source[source][entity] += count

            entity_videos[entity].append(
                {
                    "video_id": entry.get("video_id"),
                    "title": entry.get("title"),
                    "source": source,
                    "published": entry.get("published"),
                    "url": entry.get("url"),
                    "count": count,
                    "transcript_file": entry.get("transcript_file"),
                }
            )

    sorted_global_totals = dict(
        sorted(
            global_totals.items(),
            key=lambda item: (-item[1], item[0].lower()),
        )
    )

    sorted_by_source: dict[str, dict[str, int]] = {}

    for source, counts in sorted(by_source.items()):
        sorted_by_source[source] = dict(
            sorted(
                counts.items(),
                key=lambda item: (-item[1], item[0].lower()),
            )
        )

    entities: dict[str, Any] = {}

    for entity in sorted_global_totals:
        videos = sorted(
            entity_videos[entity],
            key=lambda record: (
                record.get("published") or "",
                record.get("count") or 0,
            ),
            reverse=True,
        )

        sources = Counter()

        for video in videos:
            sources[video["source"]] += int(video["count"])

        entities[entity] = {
            "total_mentions": sorted_global_totals[entity],
            "video_count": len(videos),
            "source_count": len(sources),
            "mentioned_by": dict(
                sorted(
                    sources.items(),
                    key=lambda item: (-item[1], item[0].lower()),
                )
            ),
            "videos": videos,
        }

    return {
        "generated_at": iso_datetime(utc_now()),
        "transcript_count": len(manifest_entries),
        "entity_count": len(sorted_global_totals),
        "total_mentions": sum(sorted_global_totals.values()),
        "global_totals": sorted_global_totals,
        "by_source": sorted_by_source,
        "entities": entities,
        "videos": by_video,
    }


# ============================================================
# MAIN PROCESS
# ============================================================

def process_transcripts(
    force: bool = False,
    rescan_only: bool = False,
) -> None:
    if not RADAR_FILE.exists():
        raise FileNotFoundError(
            f"Could not find {RADAR_FILE.name} in {ROOT}"
        )

    radar_data = load_json(RADAR_FILE, [])
    youtube_items = find_youtube_items(radar_data)

    items_by_id = {
        item["video_id"]: item
        for item in youtube_items
    }

    retries = load_json(RETRY_FILE, {})

    if not isinstance(retries, dict):
        retries = {}

    TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)

    successful_downloads = 0
    failed_downloads = 0
    skipped_downloads = 0

    print(f"Found {len(youtube_items)} YouTube items.")

    if not rescan_only:
        for position, item in enumerate(youtube_items, start=1):
            video_id = item["video_id"]
            title = str(item.get("title") or video_id)

            existing_path = find_existing_transcript(video_id)

            should_attempt, explanation = should_attempt_video(
                item=item,
                retry_record=retries.get(video_id),
                transcript_exists=existing_path is not None,
                force=force,
                now=utc_now(),
            )

            if not should_attempt:
                skipped_downloads += 1
                print(
                    f"[{position}/{len(youtube_items)}] "
                    f"SKIPPED - {explanation}: {title}"
                )
                continue

            if existing_path is not None and force:
                print(
                    f"[{position}/{len(youtube_items)}] "
                    f"FORCED REDOWNLOAD: {title}"
                )
            else:
                print(
                    f"[{position}/{len(youtube_items)}] "
                    f"FETCHING: {title}"
                )

            wait_seconds = random.uniform(
                MINIMUM_WAIT_SECONDS,
                MAXIMUM_WAIT_SECONDS,
            )

            print(f"Waiting {wait_seconds:.1f} seconds before request...")
            time.sleep(wait_seconds)

            attempt_time = utc_now()

            try:
                segments, language, is_generated = fetch_transcript(video_id)

                if not segments:
                    raise RuntimeError("Transcript returned no segments.")

                output_path = transcript_output_path(item)

                document = build_transcript_document(
                    item=item,
                    segments=segments,
                    language=language,
                    is_generated=is_generated,
                    downloaded_at=attempt_time,
                )

                save_json(output_path, document)
                update_retry_success(retries, item, attempt_time)
                save_json(RETRY_FILE, retries)

                successful_downloads += 1

                print(
                    f"SAVED - {len(segments)} segments, "
                    f"{sum(document['mention_counts'].values())} mentions: "
                    f"{relative_posix_path(output_path)}"
                )

            except KeyboardInterrupt:
                save_json(RETRY_FILE, retries)
                print("\nStopped by user. Retry state was saved.")
                raise

            except Exception as error:
                failed_downloads += 1

                record = update_retry_failure(
                    retries=retries,
                    item=item,
                    error=error,
                    now=attempt_time,
                )

                save_json(RETRY_FILE, retries)

                print(f"FAILED: {title}")
                print(f"Reason: {record['reason']}")
                print(f"Status: {record['status']}")

                if record.get("next_retry"):
                    print(f"Next retry: {record['next_retry']}")

                print(str(error))
                print()

    print("Rescanning every archived transcript for mentions...")

    manifest_entries = scan_all_transcripts(items_by_id)
    save_json(MANIFEST_FILE, manifest_entries)

    mention_index = build_global_mention_index(manifest_entries)
    save_json(MENTION_INDEX_FILE, mention_index)

    print()
    print("DONE")
    print(f"Successful downloads: {successful_downloads}")
    print(f"Failed downloads:     {failed_downloads}")
    print(f"Skipped downloads:    {skipped_downloads}")
    print(f"Archived transcripts: {len(manifest_entries)}")
    print(f"Known entities found: {mention_index['entity_count']}")
    print(f"Total mentions found: {mention_index['total_mentions']}")
    print(f"Manifest:              {MANIFEST_FILE.name}")
    print(f"Retry queue:           {RETRY_FILE.name}")
    print(f"Mention index:         {MENTION_INDEX_FILE.name}")

    print()
    print("TOP MENTIONS")

    for rank, (entity, count) in enumerate(
        list(mention_index["global_totals"].items())[:20],
        start=1,
    ):
        print(f"{rank:>2}. {entity:<28} {count:>6}")


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Download Drama Radar transcripts, retry recent failures, "
            "and build transcript mention indexes."
        )
    )

    parser.add_argument(
        "--force",
        action="store_true",
        help="Redownload transcripts even when they are already archived.",
    )

    parser.add_argument(
        "--rescan-only",
        action="store_true",
        help=(
            "Do not contact YouTube. Recalculate mentions in all existing "
            "transcript files and rebuild the indexes."
        ),
    )

    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()

    try:
        process_transcripts(
            force=arguments.force,
            rescan_only=arguments.rescan_only,
        )
        return 0

    except KeyboardInterrupt:
        return 130

    except Exception as error:
        print(f"FATAL ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
