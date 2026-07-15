from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from radar_common import load_json, save_json, path_for, now_iso


POSITIVE_WORDS = {
    "good", "great", "excellent", "amazing", "love", "like", "support",
    "defend", "defended", "friend", "ally", "smart", "funny", "best",
    "congratulate", "congratulations", "appreciate", "promote", "help",
    "helped", "respect", "based", "win", "winner", "successful",
}

NEGATIVE_WORDS = {
    "bad", "hate", "hates", "liar", "lying", "lies", "fraud", "stupid",
    "idiot", "moron", "loser", "awful", "terrible", "evil", "coward",
    "fake", "attack", "attacked", "harass", "harassment", "dox", "doxxing",
    "strike", "struck", "cancel", "cancelled", "fired", "scam", "grift",
    "grifter", "threat", "threatened", "wrong", "pathetic", "disgusting",
}

INTRO_PATTERNS = [
    re.compile(r"\bwelcome(?:d)?\s+(?:back\s+)?(?P<name>[A-Z][A-Za-z0-9' -]{1,40})", re.I),
    re.compile(r"\bwe(?:'ve| have)\s+got\s+(?P<name>[A-Z][A-Za-z0-9' -]{1,40})", re.I),
    re.compile(r"\bjoining\s+us\s+(?:is|today is)\s+(?P<name>[A-Z][A-Za-z0-9' -]{1,40})", re.I),
    re.compile(r"\bthanks\s+for\s+(?:coming|joining|being here)\b", re.I),
]

CLAIM_PATTERNS = [
    re.compile(r"\b(?P<claimant>[A-Z][A-Za-z0-9' .-]{1,40})\s+(?:said|says|claimed|claims|alleged|alleges|denied|denies|confirmed|confirms)\s+(?P<claim>.+)", re.I),
    re.compile(r"\bI\s+(?:think|believe|know|heard|was told|can confirm|deny|claim|allege)\s+(?P<claim>.+)", re.I),
]

RELATIONSHIP_PATTERNS = {
    "defended": re.compile(r"\b(defend|defended|stood up for|support|supported)\b", re.I),
    "criticised": re.compile(r"\b(criticise|criticized|attacked|called out|mocked|made fun of|hates?)\b", re.I),
    "promoted": re.compile(r"\b(promote|promoted|back this|buy this|launch|campaign)\b", re.I),
    "collaborated": re.compile(r"\b(worked with|collaborated|co-host|cohost|guest on|appeared on)\b", re.I),
    "legal_conflict": re.compile(r"\b(sue|lawsuit|legal action|cease and desist|strike|copyright)\b", re.I),
    "friendship_claim": re.compile(r"\b(friend|friends|ally|allies)\b", re.I),
    "separation": re.compile(r"\b(no longer speak|fell out|split|left the show|kicked off|fired)\b", re.I),
}

CLIP_MARKERS = re.compile(
    r"\b(play(?:ing)? this clip|watch this|here'?s the video|let'?s watch|"
    r"he says in this clip|she says in this clip|screen share|roll the clip)\b",
    re.I,
)

MOVE_ON_MARKERS = re.compile(
    r"\b(next story|moving on|let'?s move on|speaking of|anyway|"
    r"before we get into|now let'?s talk about)\b",
    re.I,
)


def slugify(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-") or "unknown"


def tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


def sentiment_for(text: str) -> dict[str, Any]:
    words = tokenize(text)
    pos = sum(1 for word in words if word in POSITIVE_WORDS)
    neg = sum(1 for word in words if word in NEGATIVE_WORDS)
    total = pos + neg

    if total == 0:
        label = "unclear"
        score = 0.0
    else:
        score = (pos - neg) / total
        if score >= 0.35:
            label = "positive"
        elif score <= -0.35:
            label = "negative"
        else:
            label = "mixed"

    return {
        "label": label,
        "score": round(score, 3),
        "positive_hits": pos,
        "negative_hits": neg,
    }


def nearby_excerpt(text: str, needle: str, radius: int = 220) -> str:
    lower = text.lower()
    index = lower.find(needle.lower())
    if index < 0:
        return text[: radius * 2].strip()

    start = max(0, index - radius)
    end = min(len(text), index + len(needle) + radius)
    excerpt = text[start:end].strip()
    return re.sub(r"\s+", " ", excerpt)


def story_catalog(stories_root: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for path in stories_root.glob("*.json"):
        data = load_json(path, {})
        title = str(data.get("title") or data.get("story_id") or path.stem)
        rows.append(
            {
                "story_id": str(data.get("story_id") or path.stem),
                "title": title,
                "tokens": " ".join(tokenize(title)),
            }
        )
    return rows


def jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def story_matches(text: str, catalog: list[dict[str, str]]) -> list[dict[str, Any]]:
    text_tokens = set(tokenize(text))
    matches: list[dict[str, Any]] = []

    for row in catalog:
        title_tokens = set(tokenize(row["title"]))
        score = jaccard(text_tokens, title_tokens)
        if score >= 0.08:
            matches.append(
                {
                    "story_id": row["story_id"],
                    "title": row["title"],
                    "score": round(score, 3),
                }
            )

    matches.sort(key=lambda item: item["score"], reverse=True)
    return matches[:5]


def block_chapter(block: dict[str, Any]) -> dict[str, Any]:
    entities = list((block.get("entity_counts") or {}).keys())
    title = "General discussion"
    if entities:
        title = " / ".join(entities[:3])
    if CLIP_MARKERS.search(block.get("text") or ""):
        title = f"Clip/reaction: {title}"

    return {
        "start": block.get("timestamp_start"),
        "end": block.get("timestamp_end"),
        "title": title,
        "entities": entities,
        "summary_seed": re.sub(r"\s+", " ", (block.get("text") or "")[:420]).strip(),
        "clip_likelihood": bool(CLIP_MARKERS.search(block.get("text") or "")),
        "transition_markers": len(MOVE_ON_MARKERS.findall(block.get("text") or "")),
    }


def participant_candidates(packet: dict[str, Any]) -> list[dict[str, Any]]:
    source = str(packet.get("source") or "").strip()
    blocks = packet.get("blocks", [])
    full_text = " ".join(str(block.get("text") or "") for block in blocks)
    entity_counts = packet.get("global_mention_counts") or {}

    candidates: list[dict[str, Any]] = []

    if source:
        candidates.append(
            {
                "entity": source,
                "role_guess": "host_or_channel_identity",
                "confidence": 0.72,
                "signals": ["packet source/channel identity"],
            }
        )

    for entity, count in sorted(
        entity_counts.items(),
        key=lambda item: item[1],
        reverse=True,
    ):
        if count < 2:
            continue

        direct_address = len(
            re.findall(
                rf"\b{re.escape(entity)}\s*[,!?]",
                full_text,
                flags=re.I,
            )
        )
        spread = sum(
            1
            for block in blocks
            if entity in (block.get("entity_counts") or {})
        )
        introduced = any(
            entity.lower() in match.group(0).lower()
            for pattern in INTRO_PATTERNS
            for match in pattern.finditer(full_text)
        )

        score = 0.15
        signals = []

        if direct_address:
            score += min(0.35, direct_address * 0.08)
            signals.append(f"directly addressed {direct_address} times")

        if spread >= 3:
            score += min(0.3, spread * 0.04)
            signals.append(f"appears in {spread} distant transcript blocks")

        if introduced:
            score += 0.25
            signals.append("introduction/welcome language detected")

        if CLIP_MARKERS.search(full_text) and spread <= 2:
            score -= 0.1
            signals.append("possible clip voice rather than live participant")

        if score >= 0.4:
            candidates.append(
                {
                    "entity": entity,
                    "role_guess": "likely_participant",
                    "confidence": round(min(score, 0.95), 3),
                    "signals": signals,
                }
            )

    return candidates


def subject_records(packet: dict[str, Any]) -> list[dict[str, Any]]:
    blocks = packet.get("blocks", [])
    counts = packet.get("global_mention_counts") or {}
    records: list[dict[str, Any]] = []

    for entity, total_mentions in sorted(
        counts.items(),
        key=lambda item: item[1],
        reverse=True,
    ):
        entity_blocks = [
            block
            for block in blocks
            if entity in (block.get("entity_counts") or {})
        ]
        if not entity_blocks:
            continue

        combined = " ".join(str(block.get("text") or "") for block in entity_blocks)
        sentiment = sentiment_for(combined)
        receipts = []

        for block in entity_blocks[:6]:
            receipts.append(
                {
                    "timestamp": block.get("timestamp_start"),
                    "excerpt": nearby_excerpt(block.get("text") or "", entity),
                }
            )

        span_seconds = (
            float(entity_blocks[-1].get("actual_end") or 0)
            - float(entity_blocks[0].get("actual_start") or 0)
        )
        importance = "passing"
        if total_mentions >= 8 or len(entity_blocks) >= 3:
            importance = "major"
        elif total_mentions >= 3 or len(entity_blocks) >= 2:
            importance = "secondary"

        records.append(
            {
                "entity": entity,
                "mentions": total_mentions,
                "block_count": len(entity_blocks),
                "first_timestamp": entity_blocks[0].get("timestamp_start"),
                "last_timestamp": entity_blocks[-1].get("timestamp_end"),
                "discussion_span_seconds": round(max(0, span_seconds), 1),
                "importance_guess": importance,
                "sentiment": sentiment,
                "receipts": receipts,
                "appearance_status": "uncertain",
            }
        )

    return records


def claim_candidates(packet: dict[str, Any]) -> list[dict[str, Any]]:
    claims: list[dict[str, Any]] = []
    seen: set[str] = set()

    for block in packet.get("blocks", []):
        text = str(block.get("text") or "")
        sentences = re.split(r"(?<=[.!?])\s+|>>", text)

        for sentence in sentences:
            sentence = re.sub(r"\s+", " ", sentence).strip()
            if len(sentence) < 30 or len(sentence) > 500:
                continue

            for pattern in CLAIM_PATTERNS:
                match = pattern.search(sentence)
                if not match:
                    continue

                claim = match.groupdict().get("claim") or sentence
                claimant = match.groupdict().get("claimant") or "unknown speaker"
                key = slugify(f"{claimant}-{claim[:120]}")
                if key in seen:
                    continue
                seen.add(key)

                status = "candidate_claim"
                lowered = sentence.lower()
                if "i was told" in lowered or "i heard" in lowered:
                    status = "hearsay_candidate"
                elif "i think" in lowered or "i believe" in lowered:
                    status = "opinion_or_inference"

                claims.append(
                    {
                        "claimant": claimant.strip(),
                        "claim": claim.strip(),
                        "timestamp": block.get("timestamp_start"),
                        "status": status,
                        "receipt": sentence,
                    }
                )
                break

        if len(claims) >= 40:
            break

    return claims


def quote_candidates(packet: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []

    for block in packet.get("blocks", []):
        sentences = re.split(r"(?<=[.!?])\s+|>>", str(block.get("text") or ""))

        for sentence in sentences:
            sentence = re.sub(r"\s+", " ", sentence).strip()
            if not 35 <= len(sentence) <= 220:
                continue

            significance = 0
            lowered = sentence.lower()

            if any(word in lowered for word in ["i admit", "i deny", "i lied", "i was wrong"]):
                significance += 3
            if any(word in lowered for word in ["threat", "sue", "strike", "fired", "cancelled"]):
                significance += 2
            if any(word in lowered for word in ["all hands", "never", "always", "the truth is"]):
                significance += 1

            if significance:
                candidates.append(
                    {
                        "timestamp": block.get("timestamp_start"),
                        "text": sentence,
                        "significance_score": significance,
                    }
                )

    candidates.sort(key=lambda item: item["significance_score"], reverse=True)
    return candidates[:20]


def relationship_signals(packet: dict[str, Any], subjects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    entities = [row["entity"] for row in subjects[:15]]

    for block in packet.get("blocks", []):
        text = str(block.get("text") or "")
        present = [
            entity
            for entity in entities
            if entity in (block.get("entity_counts") or {})
        ]

        if len(present) < 2:
            continue

        for signal_type, pattern in RELATIONSHIP_PATTERNS.items():
            if not pattern.search(text):
                continue

            for index, left in enumerate(present):
                for right in present[index + 1:]:
                    signals.append(
                        {
                            "left": left,
                            "right": right,
                            "signal_type": signal_type,
                            "timestamp": block.get("timestamp_start"),
                            "confidence": 0.45,
                            "receipt": nearby_excerpt(text, left),
                            "warning": "co-mention plus language signal; requires interpretation",
                        }
                    )

    return signals[:50]


def analyse_packet(packet: dict[str, Any], catalog: list[dict[str, str]]) -> dict[str, Any]:
    chapters = [block_chapter(block) for block in packet.get("blocks", [])]
    subjects = subject_records(packet)
    participants = participant_candidates(packet)
    claims = claim_candidates(packet)
    quotes = quote_candidates(packet)
    relationships = relationship_signals(packet, subjects)

    full_text = " ".join(str(block.get("text") or "") for block in packet.get("blocks", []))
    matches = story_matches(full_text, catalog)

    return {
        "schema_version": "octopuss-preanalysis-v0.1",
        "generated_at": now_iso(),
        "video_id": packet.get("video_id"),
        "title": packet.get("title"),
        "source": packet.get("source"),
        "published": packet.get("published"),
        "packet_hash": packet.get("transcript_sha256"),
        "coverage": packet.get("coverage"),
        "participant_candidates": participants,
        "subjects": subjects,
        "chapters": chapters,
        "claim_candidates": claims,
        "quote_candidates": quotes,
        "relationship_signals": relationships,
        "story_matches": matches,
        "uncertainty_flags": [
            "speaker identity may be unknown",
            "clip voices may be mistaken for live participants",
            "sentiment lexicon does not reliably detect sarcasm",
            "relationship signals are candidates, not declarations",
            "claims are not verified facts",
        ],
    }


def markdown(data: dict[str, Any]) -> str:
    lines = [
        f"# OCTOPUSS Pre-analysis: {data.get('title')}",
        "",
        f"- **Video ID:** `{data.get('video_id')}`",
        f"- **Source:** {data.get('source') or 'Unknown'}",
        f"- **Published:** {data.get('published') or 'Unknown'}",
        f"- **Generated:** {data.get('generated_at')}",
        "",
        "## Likely participants",
        "",
    ]

    if data["participant_candidates"]:
        for row in data["participant_candidates"]:
            signals = "; ".join(row.get("signals", [])) or "no signals listed"
            lines.append(
                f"- **{row['entity']}** — {row['role_guess']}; "
                f"confidence {row['confidence']:.0%}; {signals}"
            )
    else:
        lines.append("_No strong participant candidates._")

    lines.extend(["", "## Subjects and sentiment receipts", ""])
    for row in data["subjects"]:
        lines.extend(
            [
                f"### {row['entity']}",
                "",
                f"- Importance: **{row['importance_guess']}**",
                f"- Mentions: **{row['mentions']}** across **{row['block_count']}** blocks",
                f"- Span: {row['first_timestamp']}–{row['last_timestamp']}",
                f"- Sentiment guess: **{row['sentiment']['label']}** "
                f"({row['sentiment']['score']:+.3f})",
                "",
            ]
        )
        for receipt in row.get("receipts", [])[:3]:
            lines.append(f"- **{receipt['timestamp']}:** {receipt['excerpt']}")
        lines.append("")

    lines.extend(["## Topic map", ""])
    for row in data["chapters"]:
        clip = " — possible clip/reaction" if row["clip_likelihood"] else ""
        lines.append(
            f"- **{row['start']}–{row['end']}** — {row['title']}{clip}"
        )

    lines.extend(["", "## Claim candidates", ""])
    if data["claim_candidates"]:
        for row in data["claim_candidates"]:
            lines.append(
                f"- **{row['timestamp']} — {row['claimant']}:** "
                f"{row['claim']} _[{row['status']}]_"
            )
    else:
        lines.append("_No claim-shaped sentences detected._")

    lines.extend(["", "## Story matches", ""])
    if data["story_matches"]:
        for row in data["story_matches"]:
            lines.append(
                f"- **{row['title']}** — similarity {row['score']:.3f}"
            )
    else:
        lines.append("_No existing story match crossed the threshold._")

    lines.extend(["", "## Relationship signals", ""])
    if data["relationship_signals"]:
        for row in data["relationship_signals"]:
            lines.append(
                f"- **{row['left']} ↔ {row['right']}** — "
                f"{row['signal_type']} at {row['timestamp']} "
                f"(candidate confidence {row['confidence']:.0%})"
            )
    else:
        lines.append("_No relationship-language candidates detected._")

    lines.extend(["", "## Uncertainty flags", ""])
    for item in data["uncertainty_flags"]:
        lines.append(f"- {item}")

    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    packet_root = path_for("octopuss_packets")
    preanalysis_root = path_for("octopuss_preanalysis")
    stories_root = path_for("octopuss_stories")
    preanalysis_root.mkdir(parents=True, exist_ok=True)

    catalog = story_catalog(stories_root)
    index = load_json(packet_root / "index.json", {"packets": []})

    built = 0
    unchanged = 0
    failed = 0
    rows = []

    for packet_row in index.get("packets", []):
        video_id = str(packet_row.get("video_id") or "").strip()
        if not video_id:
            continue

        packet_path = packet_root / f"{video_id}.json"
        output_json = preanalysis_root / f"{video_id}.json"
        output_md = preanalysis_root / f"{video_id}.md"

        try:
            packet = load_json(packet_path, {})
            previous = load_json(output_json, {})

            if (
                previous.get("packet_hash") == packet.get("transcript_sha256")
                and output_md.exists()
            ):
                data = previous
                unchanged += 1
            else:
                data = analyse_packet(packet, catalog)
                save_json(output_json, data)
                output_md.write_text(markdown(data), encoding="utf-8")
                built += 1

            rows.append(
                {
                    "video_id": video_id,
                    "title": data.get("title"),
                    "source": data.get("source"),
                    "packet_hash": data.get("packet_hash"),
                    "json_path": str(output_json).replace("\\", "/"),
                    "markdown_path": str(output_md).replace("\\", "/"),
                }
            )

        except Exception as exc:
            failed += 1
            print(f"PREANALYSIS WARNING: {video_id}: {exc}")

    save_json(
        preanalysis_root / "index.json",
        {
            "schema_version": "octopuss-preanalysis-index-v0.1",
            "generated_at": now_iso(),
            "count": len(rows),
            "items": rows,
        },
    )

    print(f"OCTOPUSS pre-analysis files: {len(rows)}")
    print(f"Built or refreshed:         {built}")
    print(f"Unchanged:                  {unchanged}")
    print(f"Failed:                     {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
