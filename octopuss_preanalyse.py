from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any

from radar_common import load_json, save_json, path_for, now_iso


ASSERTION_VERBS = re.compile(
    r"\b(said|says|claimed|claims|alleged|alleges|denied|denies|"
    r"confirmed|confirms|admitted|admits|accused|accuses|reported|reports)\b",
    re.I,
)

FIRST_PERSON_ASSERTION = re.compile(
    r"\b(I know|I saw|I heard|I was told|I can confirm|I deny|"
    r"I claim|I allege|I believe|I think)\b",
    re.I,
)

QUOTE_SIGNIFICANCE = re.compile(
    r"\b(I admit|I deny|I lied|I was wrong|I can confirm|"
    r"threaten|sue|lawsuit|legal action|strike|fired|cancelled|"
    r"all hands on deck|never again|no longer speak)\b",
    re.I,
)

POSITIVE_WORDS = {
    "support", "supported", "defend", "defended", "praise", "praised",
    "promote", "promoted", "friend", "ally", "respect", "love",
    "excellent", "great", "amazing", "successful", "helped",
}

NEGATIVE_WORDS = {
    "liar", "lying", "fraud", "scam", "grift", "grifter", "idiot",
    "stupid", "coward", "hate", "hates", "attack", "attacked",
    "harass", "harassment", "dox", "doxxing", "strike", "fired",
    "cancelled", "threat", "threatened", "pathetic", "terrible",
}

RELATIONSHIP_PATTERNS = {
    "defended": re.compile(r"\b(defend|defended|stood up for|supported)\b", re.I),
    "criticised": re.compile(r"\b(criticised|criticized|attacked|called out|mocked)\b", re.I),
    "promoted": re.compile(r"\b(promoted|back this|buy this|launch|campaign)\b", re.I),
    "collaborated": re.compile(r"\b(worked with|collaborated|co-host|guest on|appeared on)\b", re.I),
    "legal_conflict": re.compile(r"\b(sue|lawsuit|legal action|cease and desist|copyright strike)\b", re.I),
    "separation": re.compile(r"\b(no longer speak|fell out|split|left the show|kicked off|fired)\b", re.I),
}

CLIP_MARKERS = re.compile(
    r"\b(play(?:ing)? this clip|watch this|here'?s the video|let'?s watch|"
    r"roll the clip|screen share)\b",
    re.I,
)


def slugify(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-") or "unknown"


def tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


def sentence_complete(sentence: str) -> bool:
    words = sentence.split()
    if not 7 <= len(words) <= 70:
        return False
    if sentence.endswith((",", ":", ";", "—", "-")):
        return False
    if len(set(words)) < 5:
        return False
    return True


def sentiment(text: str) -> dict[str, Any]:
    words = tokenize(text)
    pos = sum(word in POSITIVE_WORDS for word in words)
    neg = sum(word in NEGATIVE_WORDS for word in words)
    total = pos + neg

    if not total:
        return {"label": "unclear", "score": 0.0, "positive_hits": 0, "negative_hits": 0}

    score = (pos - neg) / total
    label = "mixed"
    if score >= 0.35:
        label = "positive"
    elif score <= -0.35:
        label = "negative"

    return {
        "label": label,
        "score": round(score, 3),
        "positive_hits": pos,
        "negative_hits": neg,
    }


def excerpt(text: str, needle: str, radius: int = 180) -> str:
    lower = text.lower()
    index = lower.find(needle.lower())
    if index < 0:
        return re.sub(r"\s+", " ", text[: radius * 2]).strip()

    start = max(0, index - radius)
    end = min(len(text), index + len(needle) + radius)
    return re.sub(r"\s+", " ", text[start:end]).strip()


def load_entities() -> dict[str, str]:
    aliases_path = path_for("octopuss_entities")
    data = load_json(aliases_path, {"entities": []})
    lookup: dict[str, str] = {}

    for row in data.get("entities", []):
        canonical = str(row.get("name") or "").strip()
        if not canonical:
            continue

        lookup[canonical.lower()] = canonical
        for alias in row.get("aliases", []):
            alias = str(alias).strip()
            if alias:
                lookup[alias.lower()] = canonical

    return lookup


def canonical_name(name: str, lookup: dict[str, str]) -> str | None:
    return lookup.get(name.strip().lower())


def story_catalog() -> list[dict[str, str]]:
    root = path_for("octopuss_stories")
    rows = []

    for path in root.glob("*.json"):
        data = load_json(path, {})
        title = str(data.get("title") or data.get("story_id") or path.stem)
        rows.append(
            {
                "story_id": str(data.get("story_id") or path.stem),
                "title": title,
            }
        )

    return rows


def story_matches(text: str, catalog: list[dict[str, str]]) -> list[dict[str, Any]]:
    words = set(tokenize(text))
    matches = []

    for story in catalog:
        story_words = set(tokenize(story["title"]))
        if not story_words:
            continue

        overlap = len(words & story_words)
        coverage = overlap / len(story_words)

        if overlap >= 2 and coverage >= 0.4:
            matches.append(
                {
                    "story_id": story["story_id"],
                    "title": story["title"],
                    "score": round(coverage, 3),
                }
            )

    matches.sort(key=lambda row: row["score"], reverse=True)
    return matches[:5]


def subjects(packet: dict[str, Any], lookup: dict[str, str]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}

    for block in packet.get("blocks", []):
        counts = block.get("entity_counts") or {}

        for raw_name, count in counts.items():
            canonical = canonical_name(raw_name, lookup)
            if not canonical:
                continue

            row = grouped.setdefault(
                canonical,
                {
                    "entity": canonical,
                    "mentions": 0,
                    "blocks": [],
                    "receipts": [],
                },
            )
            row["mentions"] += int(count or 0)
            row["blocks"].append(block)

            if len(row["receipts"]) < 5:
                row["receipts"].append(
                    {
                        "timestamp": block.get("timestamp_start"),
                        "excerpt": excerpt(str(block.get("text") or ""), raw_name),
                    }
                )

    results = []

    for row in grouped.values():
        combined = " ".join(str(block.get("text") or "") for block in row["blocks"])
        block_count = len(row["blocks"])

        importance = "passing"
        if row["mentions"] >= 8 or block_count >= 3:
            importance = "major"
        elif row["mentions"] >= 3 or block_count >= 2:
            importance = "secondary"

        results.append(
            {
                "entity": row["entity"],
                "mentions": row["mentions"],
                "block_count": block_count,
                "first_timestamp": row["blocks"][0].get("timestamp_start"),
                "last_timestamp": row["blocks"][-1].get("timestamp_end"),
                "importance_guess": importance,
                "sentiment": sentiment(combined),
                "receipts": row["receipts"],
            }
        )

    results.sort(
        key=lambda row: (row["importance_guess"] == "major", row["mentions"]),
        reverse=True,
    )
    return results


def participant_candidates(
    packet: dict[str, Any],
    subject_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    source = str(packet.get("source") or "").strip()
    full_text = " ".join(str(block.get("text") or "") for block in packet.get("blocks", []))
    candidates = []

    if source:
        candidates.append(
            {
                "entity": source,
                "role_guess": "host_or_channel_identity",
                "confidence": 0.75,
                "signals": ["packet source/channel identity"],
            }
        )

    for row in subject_rows:
        name = row["entity"]
        direct_address = len(
            re.findall(rf"\b{re.escape(name)}\s*[,!?]", full_text, flags=re.I)
        )
        confidence = 0.15
        signals = []

        if direct_address >= 2:
            confidence += min(0.35, direct_address * 0.08)
            signals.append(f"directly addressed {direct_address} times")

        if row["block_count"] >= 4:
            confidence += min(0.25, row["block_count"] * 0.03)
            signals.append(f"mentioned across {row['block_count']} blocks")

        if re.search(
            rf"\b(welcome|joining us|we have|we've got)\b.{0,80}\b{re.escape(name)}\b",
            full_text,
            flags=re.I,
        ):
            confidence += 0.3
            signals.append("introduction/welcome wording detected")

        if CLIP_MARKERS.search(full_text) and row["block_count"] <= 2:
            confidence -= 0.1
            signals.append("possible clip subject")

        if confidence >= 0.55:
            candidates.append(
                {
                    "entity": name,
                    "role_guess": "likely_participant",
                    "confidence": round(min(confidence, 0.95), 3),
                    "signals": signals,
                }
            )

    return candidates[:15]


def claim_candidates(
    packet: dict[str, Any],
    tracked_names: set[str],
) -> list[dict[str, Any]]:
    candidates = []
    seen = set()

    for block in packet.get("blocks", []):
        text = str(block.get("text") or "")
        sentences = re.split(r"(?<=[.!?])\s+|>>", text)

        for sentence in sentences:
            sentence = re.sub(r"\s+", " ", sentence).strip()

            if not sentence_complete(sentence):
                continue

            lower = sentence.lower()
            relevant_entities = [
                name for name in tracked_names
                if name.lower() in lower
            ]

            if not relevant_entities:
                continue

            if not (ASSERTION_VERBS.search(sentence) or FIRST_PERSON_ASSERTION.search(sentence)):
                continue

            if sentence.count(" uh ") + sentence.count(" um ") > 4:
                continue

            key = slugify(sentence[:180])
            if key in seen:
                continue
            seen.add(key)

            status = "candidate_claim"
            if re.search(r"\b(I heard|I was told|apparently|reportedly)\b", sentence, re.I):
                status = "hearsay_candidate"
            elif re.search(r"\b(I think|I believe|maybe|probably)\b", sentence, re.I):
                status = "opinion_or_inference"

            candidates.append(
                {
                    "claimant": "unknown speaker",
                    "subjects": relevant_entities[:3],
                    "claim": sentence,
                    "timestamp": block.get("timestamp_start"),
                    "status": status,
                    "confidence": 0.55 if status == "candidate_claim" else 0.42,
                    "receipt": sentence,
                }
            )

    candidates.sort(
        key=lambda row: (
            row["confidence"],
            len(row["subjects"]),
            len(row["claim"]),
        ),
        reverse=True,
    )
    return candidates[:10]


def quote_candidates(
    packet: dict[str, Any],
    tracked_names: set[str],
) -> list[dict[str, Any]]:
    candidates = []
    seen = set()

    for block in packet.get("blocks", []):
        for sentence in re.split(r"(?<=[.!?])\s+|>>", str(block.get("text") or "")):
            sentence = re.sub(r"\s+", " ", sentence).strip()

            if not sentence_complete(sentence):
                continue

            if not QUOTE_SIGNIFICANCE.search(sentence):
                continue

            lower = sentence.lower()
            relevant = [name for name in tracked_names if name.lower() in lower]
            score = 1 + min(3, len(relevant))

            key = slugify(sentence[:180])
            if key in seen:
                continue
            seen.add(key)

            candidates.append(
                {
                    "timestamp": block.get("timestamp_start"),
                    "text": sentence,
                    "subjects": relevant[:3],
                    "significance_score": score,
                }
            )

    candidates.sort(key=lambda row: row["significance_score"], reverse=True)
    return candidates[:10]


def relationship_signals(
    packet: dict[str, Any],
    tracked_names: set[str],
) -> list[dict[str, Any]]:
    rows = []
    seen = set()

    for block in packet.get("blocks", []):
        text = str(block.get("text") or "")
        lower = text.lower()
        present = [name for name in tracked_names if name.lower() in lower]

        if len(present) < 2:
            continue

        for signal_type, pattern in RELATIONSHIP_PATTERNS.items():
            if not pattern.search(text):
                continue

            for index, left in enumerate(present):
                for right in present[index + 1:]:
                    key = (left, right, signal_type, block.get("timestamp_start"))
                    if key in seen:
                        continue
                    seen.add(key)

                    rows.append(
                        {
                            "left": left,
                            "right": right,
                            "signal_type": signal_type,
                            "timestamp": block.get("timestamp_start"),
                            "confidence": 0.48,
                            "receipt": excerpt(text, left),
                            "warning": "candidate relationship evidence; requires review",
                        }
                    )

    return rows[:20]


def analyse(packet: dict[str, Any], lookup: dict[str, str], catalog: list[dict[str, str]]) -> dict[str, Any]:
    subject_rows = subjects(packet, lookup)
    tracked_names = {row["entity"] for row in subject_rows}
    full_text = " ".join(str(block.get("text") or "") for block in packet.get("blocks", []))

    chapters = []
    for block in packet.get("blocks", []):
        names = [
            name for name in tracked_names
            if name.lower() in str(block.get("text") or "").lower()
        ]
        chapters.append(
            {
                "start": block.get("timestamp_start"),
                "end": block.get("timestamp_end"),
                "title": " / ".join(names[:3]) if names else "General discussion",
                "entities": names[:10],
                "possible_clip": bool(CLIP_MARKERS.search(str(block.get("text") or ""))),
                "summary_seed": re.sub(r"\s+", " ", str(block.get("text") or "")[:350]).strip(),
            }
        )

    return {
        "schema_version": "octopuss-preanalysis-v0.2",
        "generated_at": now_iso(),
        "video_id": packet.get("video_id"),
        "title": packet.get("title"),
        "source": packet.get("source"),
        "published": packet.get("published"),
        "packet_hash": packet.get("transcript_sha256"),
        "coverage": packet.get("coverage"),
        "participant_candidates": participant_candidates(packet, subject_rows),
        "subjects": subject_rows,
        "chapters": chapters,
        "claim_candidates": claim_candidates(packet, tracked_names),
        "quote_candidates": quote_candidates(packet, tracked_names),
        "relationship_signals": relationship_signals(packet, tracked_names),
        "story_matches": story_matches(full_text, catalog),
        "review_priority": round(
            min(
                100,
                len(subject_rows) * 3
                + len(claim_candidates(packet, tracked_names)) * 5
                + len(relationship_signals(packet, tracked_names)) * 4
                + len(story_matches(full_text, catalog)) * 8,
            ),
            1,
        ),
        "uncertainty_flags": [
            "speaker attribution may be unknown",
            "clip voices may be mistaken for live participants",
            "sentiment lexicon cannot reliably detect sarcasm",
            "claims and relationship signals remain candidates",
        ],
    }


def markdown(data: dict[str, Any]) -> str:
    lines = [
        f"# OCTOPUSS Pre-analysis: {data.get('title')}",
        "",
        f"- **Video ID:** `{data.get('video_id')}`",
        f"- **Source:** {data.get('source') or 'Unknown'}",
        f"- **Review priority:** {data.get('review_priority', 0)}/100",
        "",
        "## Participant candidates",
        "",
    ]

    for row in data.get("participant_candidates", []):
        lines.append(
            f"- **{row['entity']}** — {row['role_guess']} "
            f"({float(row['confidence']):.0%})"
        )

    lines.extend(["", "## Subjects", ""])
    for row in data.get("subjects", []):
        lines.append(
            f"- **{row['entity']}** — {row['importance_guess']}; "
            f"{row['mentions']} mentions; "
            f"{row['sentiment']['label']}"
        )

    lines.extend(["", "## Strong claim candidates", ""])
    if data.get("claim_candidates"):
        for row in data["claim_candidates"]:
            lines.append(
                f"- **{row['timestamp']}** — {row['claim']} "
                f"_[{row['status']}; {float(row['confidence']):.0%}]_"
            )
    else:
        lines.append("_None passed the strong filter._")

    lines.extend(["", "## Quote candidates", ""])
    if data.get("quote_candidates"):
        for row in data["quote_candidates"]:
            lines.append(
                f"- **{row['timestamp']}** — {row['text']}"
            )
    else:
        lines.append("_None passed the strong filter._")

    lines.extend(["", "## Relationship candidates", ""])
    if data.get("relationship_signals"):
        for row in data["relationship_signals"]:
            lines.append(
                f"- **{row['left']} ↔ {row['right']}** — "
                f"{row['signal_type']} at {row['timestamp']}"
            )
    else:
        lines.append("_None passed the filter._")

    lines.extend(["", "## Story matches", ""])
    for row in data.get("story_matches", []):
        lines.append(f"- **{row['title']}** — {row['score']:.3f}")

    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    packet_root = path_for("octopuss_packets")
    output_root = path_for("octopuss_preanalysis")
    output_root.mkdir(parents=True, exist_ok=True)

    lookup = load_entities()
    catalog = story_catalog()
    index = load_json(packet_root / "index.json", {"packets": []})

    built = 0
    unchanged = 0
    rows = []

    for item in index.get("packets", []):
        video_id = str(item.get("video_id") or "").strip()
        if not video_id:
            continue

        packet = load_json(packet_root / f"{video_id}.json", {})
        json_path = output_root / f"{video_id}.json"
        md_path = output_root / f"{video_id}.md"
        previous = load_json(json_path, {})

        if (
            previous.get("schema_version") == "octopuss-preanalysis-v0.2"
            and previous.get("packet_hash") == packet.get("transcript_sha256")
            and md_path.exists()
        ):
            data = previous
            unchanged += 1
        else:
            data = analyse(packet, lookup, catalog)
            save_json(json_path, data)
            md_path.write_text(markdown(data), encoding="utf-8")
            built += 1

        rows.append(
            {
                "video_id": video_id,
                "title": data.get("title"),
                "source": data.get("source"),
                "review_priority": data.get("review_priority", 0),
                "packet_hash": data.get("packet_hash"),
            }
        )

    rows.sort(key=lambda row: row["review_priority"], reverse=True)

    save_json(
        output_root / "index.json",
        {
            "schema_version": "octopuss-preanalysis-index-v0.2",
            "generated_at": now_iso(),
            "count": len(rows),
            "items": rows,
        },
    )

    print(f"OCTOPUSS compact pre-analysis files: {len(rows)}")
    print(f"Built or refreshed:                 {built}")
    print(f"Unchanged:                          {unchanged}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
