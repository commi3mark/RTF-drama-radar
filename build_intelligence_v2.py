#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote

EXTRACTOR_VERSION = "2.0.0"
DEFAULT_REPO = "commi3mark/RTF-drama-radar"
DEFAULT_BRANCH = "main"

TRANSCRIPT_ROOT = Path("transcripts")
CONFIG_DIR = Path("config")
DATA_DIR = Path("data")
STATE_DIR = Path("state")
CACHE_DIR = Path("cache/transcripts")

FILES = {
    "entities": DATA_DIR / "entities.json",
    "evidence": DATA_DIR / "evidence-index.json",
    "raw_mentions": DATA_DIR / "raw-mention-index.json",
    "mentions": DATA_DIR / "mention-index.json",
    "claims": DATA_DIR / "claims.json",
    "claim_participants": DATA_DIR / "claim-participant-index.json",
    "events": DATA_DIR / "events.json",
    "stories": DATA_DIR / "stories.json",
    "relationships": DATA_DIR / "relationships.json",
    "campaigns": DATA_DIR / "campaigns.json",
    "risk_signals": DATA_DIR / "risk-signals.json",
    "risk_profiles": DATA_DIR / "commi3-risk-profiles.json",
    "report_index": DATA_DIR / "report-index.json",
    "manifest": Path("transcript-manifest.json"),
    "state": STATE_DIR / "processing-state.json",
}

CLAIM_VERBS = (
    r"said|says|told|claimed|claims|alleged|alleges|stated|states|"
    r"argued|argues|insisted|insists|reported|reports|wrote|posted|"
    r"tweeted|replied|accused|denied|admitted|threatened|warned"
)

TO_RECIPIENT = r"(?:to|that|at)\s+"

RISK_PATTERNS = {
    "direct_hostility": [
        r"\b(?:hate|despise|can't stand)\b",
        r"\b(?:idiot|moron|fraud|liar|loser|scumbag)\b",
    ],
    "deplatforming": [
        r"\b(?:report|mass report|flag|strike|ban|deplatform)\b",
    ],
    "privacy_signal": [
        r"\b(?:address|home|house|workplace|phone number|real name|dox|doxx)\b",
    ],
    "threat_language": [
        r"\b(?:hurt|attack|kill|shoot|stab|beat|smash|visit his house|come to your house)\b",
    ],
    "mobilisation": [
        r"\b(?:everyone should|you all should|tell your followers|go after|dogpile)\b",
    ],
}

CAMPAIGN_PLATFORMS = [
    "indiegogo", "kickstarter", "fund my comic", "fundmycomic",
    "backerkit", "zoop"
]
CAMPAIGN_ACTIONS = [
    "back", "backing", "launch", "launched", "closing", "close",
    "funding", "funded", "stretch goal", "preorder", "pre-order",
    "signup", "sign-up", "mailing list", "fulfilled", "fulfilment", "fulfillment"
]

STORY_KEYWORDS = {
    "fencegate": ["fencegate", "fence gate"],
    "false-flag": ["false flag", "false-flag"],
    "copyright-strike": ["copyright strike", "copyright claim", "dmca"],
    "doxxing": ["dox", "doxxing", "doxed", "doxxed"],
    "swatting": ["swat", "swatting"],
    "platform-ban": ["suspended", "unsuspended", "banned", "terminated", "channel strike"],
    "campaign-drama": ["indiegogo", "kickstarter", "refund", "backer", "fulfillment", "fulfilment"],
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def norm(text: str) -> str:
    text = text.replace("\u2018", "'").replace("\u2019", "'")
    return re.sub(r"\s+", " ", text).strip()


def ts_text(seconds: float) -> str:
    s = max(0, int(seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{sec:02d}" if h else f"{m:02d}:{sec:02d}"


def timestamp_url(url: str | None, seconds: float) -> str | None:
    if not url:
        return None
    if "youtube.com" in url or "youtu.be" in url:
        sep = "&" if "?" in url else "?"
        return f"{url}{sep}t={max(0, int(seconds))}s"
    return url


def github_raw_url(repo: str, branch: str, relative_path: str) -> str:
    encoded = "/".join(quote(part, safe="") for part in relative_path.split("/"))
    return f"https://raw.githubusercontent.com/{repo}/refs/heads/{branch}/{encoded}"


def iter_json(root: Path) -> Iterable[Path]:
    if not root.exists():
        return []
    return sorted(p for p in root.rglob("*.json") if p.is_file())


def load_entities() -> list[dict[str, Any]]:
    payload = load_json(CONFIG_DIR / "entities.json", {})
    entities = payload.get("entities", [])
    if not entities:
        raise RuntimeError("config/entities.json contains no entities")
    return entities


def compile_aliases(entities: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for entity in entities:
        compiled = []
        for alias in entity.get("aliases", []):
            value = alias["value"] if isinstance(alias, dict) else str(alias)
            strength = alias.get("strength", "strong") if isinstance(alias, dict) else "strong"
            exclusions = alias.get("exclusions", []) if isinstance(alias, dict) else []
            compiled.append({
                "value": value,
                "strength": strength,
                "exclusions": [re.compile(x, re.I) for x in exclusions],
                "pattern": re.compile(rf"(?<!\w){re.escape(value)}(?!\w)", re.I),
            })
        result[entity["entity_id"]] = compiled
    return result


@dataclass
class RawMention:
    entity_id: str
    alias: str
    alias_strength: str
    video_id: str
    title: str
    source: str | None
    published: str | None
    url: str | None
    transcript_file: str
    segment_index: int
    start: float
    text: str
    before: str
    after: str
    confidence: float


def detect_mention_class(text: str, before: str, after: str, entity_name: str) -> str:
    joined = f"{before} {text} {after}".lower()
    if re.search(r"\b(?:i am|i'm|this is)\s+" + re.escape(entity_name.lower()), joined):
        return "self_reference"
    if re.search(r"\b(?:welcome|you're watching|my channel|subscribe)\b", joined):
        return "host_intro"
    if re.search(r"\b(?:superchat|super chat|chat says|comment says|writes)\b", joined):
        return "chat_readout"
    if re.search(r"\b(?:clip|video says|he says|she says|they say)\b", joined):
        return "quoted_clip"
    if len(text.split()) <= 3:
        return "incidental"
    return "discussion"


def extract_raw_mentions(
    transcript: dict[str, Any],
    path: Path,
    entities_by_id: dict[str, dict[str, Any]],
    aliases: dict[str, list[dict[str, Any]]],
) -> list[RawMention]:
    segments = transcript.get("segments") or []
    output: list[RawMention] = []
    for i, seg in enumerate(segments):
        if not isinstance(seg, dict):
            continue
        text = norm(str(seg.get("text") or ""))
        if not text:
            continue
        before = norm(str(segments[i-1].get("text") or "")) if i > 0 and isinstance(segments[i-1], dict) else ""
        after = norm(str(segments[i+1].get("text") or "")) if i + 1 < len(segments) and isinstance(segments[i+1], dict) else ""
        context = f"{before} {text} {after}"
        for entity_id, patterns in aliases.items():
            for item in patterns:
                if not item["pattern"].search(text):
                    continue
                if any(ex.search(context) for ex in item["exclusions"]):
                    continue
                confidence = 1.0 if item["strength"] == "strong" else 0.68
                output.append(RawMention(
                    entity_id=entity_id,
                    alias=item["value"],
                    alias_strength=item["strength"],
                    video_id=str(transcript.get("video_id") or ""),
                    title=str(transcript.get("title") or path.stem),
                    source=transcript.get("source"),
                    published=transcript.get("published"),
                    url=transcript.get("url"),
                    transcript_file=path.as_posix(),
                    segment_index=i,
                    start=float(seg.get("start") or 0),
                    text=text,
                    before=before,
                    after=after,
                    confidence=confidence,
                ))
                break
    return output


def dedupe_mentions(raw_mentions: list[RawMention], entities_by_id: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[RawMention]] = defaultdict(list)
    for m in raw_mentions:
        grouped[(m.video_id, m.entity_id)].append(m)

    cleaned = []
    for (_, entity_id), mentions in grouped.items():
        mentions.sort(key=lambda x: x.start)
        cluster: list[RawMention] = []
        for m in mentions:
            if cluster and m.start - cluster[-1].start > 15:
                cleaned.append(make_clean_mention(cluster, entities_by_id[entity_id]))
                cluster = []
            cluster.append(m)
        if cluster:
            cleaned.append(make_clean_mention(cluster, entities_by_id[entity_id]))
    return sorted(cleaned, key=lambda x: (x.get("published") or "", x["video_id"], x["start"]), reverse=True)


def make_clean_mention(cluster: list[RawMention], entity: dict[str, Any]) -> dict[str, Any]:
    best = max(cluster, key=lambda m: (m.confidence, len(m.text)))
    mention_class = detect_mention_class(best.text, best.before, best.after, entity["canonical_name"])
    return {
        "mention_id": f"mention:{best.video_id}:{int(best.start)}:{best.entity_id}",
        "entity_id": best.entity_id,
        "canonical_name": entity["canonical_name"],
        "matched_aliases": sorted({m.alias for m in cluster}),
        "mention_class": mention_class,
        "video_id": best.video_id,
        "title": best.title,
        "source": best.source,
        "published": best.published,
        "url": best.url,
        "transcript_file": best.transcript_file,
        "segment_indexes": [m.segment_index for m in cluster],
        "start": best.start,
        "timestamp": ts_text(best.start),
        "quote": best.text,
        "context_before": best.before,
        "context_after": best.after,
        "receipt_url": timestamp_url(best.url, best.start),
        "duplicate_fragment_count": max(0, len(cluster) - 1),
        "confidence": max(m.confidence for m in cluster),
    }


def detect_claims(
    transcript: dict[str, Any],
    path: Path,
    cleaned_mentions: list[dict[str, Any]],
    entities_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    segments = transcript.get("segments") or []
    mentions_by_segment: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for m in cleaned_mentions:
        for idx in m.get("segment_indexes", []):
            mentions_by_segment[idx].append(m)

    claims = []
    for i, seg in enumerate(segments):
        if not isinstance(seg, dict):
            continue
        text = norm(str(seg.get("text") or ""))
        if not re.search(rf"\b(?:{CLAIM_VERBS})\b", text, re.I):
            continue

        window_indexes = [j for j in range(max(0, i-2), min(len(segments), i+3))]
        participant_mentions = []
        for j in window_indexes:
            participant_mentions.extend(mentions_by_segment.get(j, []))
        participants = []
        seen = set()
        for m in participant_mentions:
            if m["entity_id"] not in seen:
                seen.add(m["entity_id"])
                participants.append(m["entity_id"])
        if not participants:
            continue

        reporting_speaker = infer_reporting_speaker(transcript, participants, entities_by_id)
        attributed_speaker = None
        recipient = None
        subject_ids = list(participants)

        # Simple role inference from entity order around reporting verbs.
        entity_positions = []
        for entity_id in participants:
            name = entities_by_id[entity_id]["canonical_name"]
            aliases = [a["value"] if isinstance(a, dict) else str(a) for a in entities_by_id[entity_id].get("aliases", [])]
            for alias in aliases:
                match = re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", text, re.I)
                if match:
                    entity_positions.append((match.start(), entity_id))
                    break
        entity_positions.sort()
        verb_match = re.search(rf"\b(?:{CLAIM_VERBS})\b", text, re.I)
        if verb_match:
            before_entities = [eid for pos, eid in entity_positions if pos < verb_match.start()]
            after_entities = [eid for pos, eid in entity_positions if pos > verb_match.end()]
            if before_entities:
                attributed_speaker = before_entities[-1]
            elif reporting_speaker in participants:
                attributed_speaker = reporting_speaker
            if after_entities:
                recipient = after_entities[0]

        start = float(seg.get("start") or 0)
        evidence_id = f"evidence:youtube:{transcript.get('video_id')}:{int(start)}"
        directness = "direct"
        chain = []
        if reporting_speaker:
            chain.append({
                "level": 1,
                "speaker_entity_id": reporting_speaker,
                "verb": "said",
                "recipient_entity_ids": [],
                "content_type": "direct_statement",
            })
        if attributed_speaker and attributed_speaker != reporting_speaker:
            directness = "second_hand"
            chain.append({
                "level": 2,
                "speaker_entity_id": attributed_speaker,
                "verb": infer_claim_verb(text),
                "recipient_entity_ids": [recipient] if recipient else [],
                "content_type": "reported_statement",
            })

        claim_id = f"claim:{transcript.get('video_id')}:{int(start)}:{len(claims)+1}"
        claims.append({
            "claim_id": claim_id,
            "claim_text": text,
            "claim_status": "reported_claim" if directness != "direct" else "direct_statement",
            "reporting_speaker_entity_id": reporting_speaker,
            "attributed_speaker_entity_id": attributed_speaker,
            "recipient_entity_ids": [recipient] if recipient else [],
            "subject_entity_ids": subject_ids,
            "participant_entity_ids": sorted(set(participants + ([reporting_speaker] if reporting_speaker else []) + ([attributed_speaker] if attributed_speaker else []) + ([recipient] if recipient else []))),
            "attribution_chain": chain,
            "source_type": "youtube",
            "source_title": transcript.get("title"),
            "source_entity_name": transcript.get("source"),
            "published_at": transcript.get("published"),
            "video_id": transcript.get("video_id"),
            "timestamp_seconds": start,
            "timestamp_display": ts_text(start),
            "exact_quote": text,
            "context_before": norm(str(segments[i-1].get("text") or "")) if i > 0 and isinstance(segments[i-1], dict) else "",
            "context_after": norm(str(segments[i+1].get("text") or "")) if i+1 < len(segments) and isinstance(segments[i+1], dict) else "",
            "receipt_url": timestamp_url(transcript.get("url"), start),
            "evidence_ids": [evidence_id],
            "directness": directness,
            "confidence": 0.82 if directness == "second_hand" else 0.9,
        })
    return claims


def infer_reporting_speaker(transcript: dict[str, Any], participants: list[str], entities_by_id: dict[str, dict[str, Any]]) -> str | None:
    source = norm(str(transcript.get("source") or "")).lower()
    for entity_id, entity in entities_by_id.items():
        names = [entity["canonical_name"]] + [
            a["value"] if isinstance(a, dict) else str(a) for a in entity.get("aliases", [])
        ]
        if any(norm(name).lower() == source for name in names):
            return entity_id
    return participants[0] if len(participants) == 1 else None


def infer_claim_verb(text: str) -> str:
    m = re.search(rf"\b({CLAIM_VERBS})\b", text, re.I)
    return m.group(1).lower() if m else "said"


def build_participant_index(claims: list[dict[str, Any]]) -> dict[str, Any]:
    index: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for c in claims:
        roles = defaultdict(list)
        if c.get("reporting_speaker_entity_id"):
            roles[c["reporting_speaker_entity_id"]].append("reporting_speaker")
        if c.get("attributed_speaker_entity_id"):
            roles[c["attributed_speaker_entity_id"]].append("attributed_speaker")
        for eid in c.get("recipient_entity_ids", []):
            roles[eid].append("recipient")
        for eid in c.get("subject_entity_ids", []):
            roles[eid].append("subject")
        for eid, role_list in roles.items():
            index[eid].append({
                "claim_id": c["claim_id"],
                "roles": sorted(set(role_list)),
                "receipt_url": c.get("receipt_url"),
                "published_at": c.get("published_at"),
            })
    return {
        "generated_at": now_iso(),
        "schema_version": 2,
        "participants": dict(index),
    }


def build_evidence(transcript: dict[str, Any], path: Path, raw_mentions: list[RawMention]) -> list[dict[str, Any]]:
    evidence = []
    seen = set()
    for m in raw_mentions:
        eid = f"evidence:youtube:{m.video_id}:{int(m.start)}"
        if eid in seen:
            continue
        seen.add(eid)
        evidence.append({
            "evidence_id": eid,
            "evidence_type": "transcript_quote",
            "platform": "youtube",
            "source_title": m.title,
            "source_entity_name": m.source,
            "published_at": m.published,
            "captured_at": now_iso(),
            "url": m.url,
            "receipt_url": timestamp_url(m.url, m.start),
            "transcript_file": path.as_posix(),
            "timestamp_seconds": m.start,
            "timestamp_display": ts_text(m.start),
            "content": m.text,
            "context_before": m.before,
            "context_after": m.after,
            "confidence": m.confidence,
            "status": "captured",
        })
    return evidence


def detect_campaigns(transcript: dict[str, Any], path: Path) -> list[dict[str, Any]]:
    output = []
    for i, seg in enumerate(transcript.get("segments") or []):
        if not isinstance(seg, dict):
            continue
        text = norm(str(seg.get("text") or ""))
        low = text.lower()
        platforms = [p for p in CAMPAIGN_PLATFORMS if p in low]
        actions = [a for a in CAMPAIGN_ACTIONS if a in low]
        if not platforms or not actions:
            continue
        start = float(seg.get("start") or 0)
        output.append({
            "campaign_signal_id": f"campaign-signal:{transcript.get('video_id')}:{int(start)}",
            "platforms": platforms,
            "actions": actions,
            "quote": text,
            "published_at": transcript.get("published"),
            "source_title": transcript.get("title"),
            "receipt_url": timestamp_url(transcript.get("url"), start),
            "transcript_file": path.as_posix(),
            "status": "candidate",
            "confidence": 0.72,
        })
    return output


def detect_story_tags(text: str) -> list[str]:
    low = text.lower()
    return [sid for sid, terms in STORY_KEYWORDS.items() if any(t in low for t in terms)]


def build_events(claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    events = []
    for c in claims:
        events.append({
            "event_id": "event:" + c["claim_id"].split("claim:", 1)[1],
            "event_type": "statement",
            "title": c["claim_text"][:120],
            "summary": c["claim_text"],
            "occurred_from": c.get("published_at"),
            "participant_entity_ids": c.get("participant_entity_ids", []),
            "primary_actor_entity_id": c.get("reporting_speaker_entity_id"),
            "claim_ids": [c["claim_id"]],
            "evidence_ids": c.get("evidence_ids", []),
            "importance": 0.5,
            "novelty": 0.5,
            "confidence": c.get("confidence", 0.8),
            "status": "candidate",
        })
    return events


def build_relationships(claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    pairs = defaultdict(lambda: {"claim_ids": [], "roles": Counter()})
    for c in claims:
        ids = sorted(set(c.get("participant_entity_ids", [])))
        for i in range(len(ids)):
            for j in range(i+1, len(ids)):
                key = (ids[i], ids[j])
                pairs[key]["claim_ids"].append(c["claim_id"])
                pairs[key]["roles"]["co_claim"] += 1
    out = []
    for (a, b), data in pairs.items():
        out.append({
            "relationship_id": f"relationship:{a}:{b}",
            "entity_a_id": a,
            "entity_b_id": b,
            "relationship_type": "unclear",
            "status": "candidate",
            "strength": min(1.0, len(data["claim_ids"]) / 10),
            "confidence": 0.45,
            "supporting_claim_ids": data["claim_ids"],
            "note": "Co-participation creates a candidate only; this is not a confirmed relationship.",
        })
    return sorted(out, key=lambda x: -x["strength"])


def build_risk_signals(claims: list[dict[str, Any]], target_entity_id: str) -> list[dict[str, Any]]:
    signals = []
    for c in claims:
        if target_entity_id not in c.get("participant_entity_ids", []):
            continue
        text = c.get("claim_text", "")
        actor = c.get("attributed_speaker_entity_id") or c.get("reporting_speaker_entity_id")
        if not actor or actor == target_entity_id:
            continue
        for signal_type, patterns in RISK_PATTERNS.items():
            matched = [p for p in patterns if re.search(p, text, re.I)]
            if not matched:
                continue
            severity = {
                "direct_hostility": 1,
                "deplatforming": 2,
                "privacy_signal": 3,
                "threat_language": 4,
                "mobilisation": 2,
            }[signal_type]
            if c.get("directness") != "direct":
                severity = max(1, severity - 1)
            signals.append({
                "risk_signal_id": f"risk-signal:{c['claim_id']}:{signal_type}",
                "target_entity_id": target_entity_id,
                "actor_entity_id": actor,
                "signal_type": signal_type,
                "severity": severity,
                "claim_id": c["claim_id"],
                "evidence_ids": c.get("evidence_ids", []),
                "exact_quote": text,
                "directness": c.get("directness"),
                "receipt_url": c.get("receipt_url"),
                "confidence": c.get("confidence", 0.8),
                "review_status": "automatic_needs_review",
            })
    return signals


def build_risk_profiles(signals: list[dict[str, Any]], target_entity_id: str) -> list[dict[str, Any]]:
    grouped = defaultdict(list)
    for s in signals:
        grouped[s["actor_entity_id"]].append(s)
    out = []
    for actor, items in grouped.items():
        raw = sum(s["severity"] * 8 for s in items)
        score = min(100, raw)
        label = "minimal" if score < 20 else "watch" if score < 40 else "concerning" if score < 60 else "elevated" if score < 80 else "severe"
        out.append({
            "entity_id": actor,
            "target_entity_id": target_entity_id,
            "risk_label": label,
            "risk_score": score,
            "confidence": round(sum(s["confidence"] for s in items) / len(items), 3),
            "signal_count": len(items),
            "supporting_risk_signal_ids": [s["risk_signal_id"] for s in items],
            "last_updated_at": now_iso(),
            "review_status": "automatic_needs_review",
            "warning": "This is an interaction-risk sorting aid, not a finding that the person is dangerous.",
        })
    return sorted(out, key=lambda x: -x["risk_score"])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--transcripts", default=str(TRANSCRIPT_ROOT))
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--branch", default=DEFAULT_BRANCH)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    for d in [DATA_DIR, STATE_DIR, CACHE_DIR, CONFIG_DIR]:
        d.mkdir(parents=True, exist_ok=True)

    entities = load_entities()
    entities_by_id = {e["entity_id"]: e for e in entities}
    aliases = compile_aliases(entities)
    previous_state = load_json(FILES["state"], {})
    previous_files = previous_state.get("files", {})

    all_raw: list[RawMention] = []
    all_clean: list[dict[str, Any]] = []
    all_claims: list[dict[str, Any]] = []
    all_evidence: list[dict[str, Any]] = []
    all_campaigns: list[dict[str, Any]] = []
    manifest = []
    state_files = {}

    changed = unchanged = failed = 0
    transcript_paths = list(iter_json(Path(args.transcripts)))

    for path in transcript_paths:
        rel = path.as_posix()
        digest = sha256_file(path)
        prior = previous_files.get(rel, {})
        cache_file = CACHE_DIR / f"{digest}-{EXTRACTOR_VERSION}.json"
        use_cache = (
            not args.force
            and prior.get("sha256") == digest
            and prior.get("extractor_version") == EXTRACTOR_VERSION
            and cache_file.exists()
        )
        try:
            if use_cache:
                cache = load_json(cache_file, {})
                raw_dicts = cache.get("raw_mentions", [])
                raw_mentions = [RawMention(**x) for x in raw_dicts]
                clean_mentions = cache.get("clean_mentions", [])
                claims = cache.get("claims", [])
                evidence = cache.get("evidence", [])
                campaigns = cache.get("campaigns", [])
                unchanged += 1
                transcript = load_json(path, {})
            else:
                transcript = load_json(path, {})
                if not isinstance(transcript, dict):
                    raise ValueError("Transcript JSON root must be an object")
                raw_mentions = extract_raw_mentions(transcript, path, entities_by_id, aliases)
                clean_mentions = dedupe_mentions(raw_mentions, entities_by_id)
                claims = detect_claims(transcript, path, clean_mentions, entities_by_id)
                evidence = build_evidence(transcript, path, raw_mentions)
                campaigns = detect_campaigns(transcript, path)
                write_json(cache_file, {
                    "raw_mentions": [m.__dict__ for m in raw_mentions],
                    "clean_mentions": clean_mentions,
                    "claims": claims,
                    "evidence": evidence,
                    "campaigns": campaigns,
                })
                changed += 1

            all_raw.extend(raw_mentions)
            all_clean.extend(clean_mentions)
            all_claims.extend(claims)
            all_evidence.extend(evidence)
            all_campaigns.extend(campaigns)

            full_text = " ".join(str(s.get("text") or "") for s in transcript.get("segments") or [] if isinstance(s, dict))
            manifest.append({
                "video_id": transcript.get("video_id"),
                "title": transcript.get("title"),
                "source": transcript.get("source"),
                "published": transcript.get("published"),
                "url": transcript.get("url"),
                "segment_count": len(transcript.get("segments") or []),
                "transcript_file": rel,
                "raw_url": github_raw_url(args.repo, args.branch, rel),
                "sha256": digest,
                "raw_mention_count": len(raw_mentions),
                "clean_mention_count": len(clean_mentions),
                "claim_count": len(claims),
                "campaign_signal_count": len(campaigns),
                "story_tags": detect_story_tags(full_text),
                "processed_at": now_iso(),
            })
            state_files[rel] = {
                "sha256": digest,
                "extractor_version": EXTRACTOR_VERSION,
                "cache_file": cache_file.as_posix(),
                "processed_at": now_iso(),
                "status": "ok",
            }
        except Exception as e:
            failed += 1
            state_files[rel] = {
                "sha256": digest,
                "extractor_version": EXTRACTOR_VERSION,
                "processed_at": now_iso(),
                "status": "failed",
                "error": f"{type(e).__name__}: {e}",
            }
            print(f"FAILED: {rel}: {e}", file=sys.stderr)

    participant_index = build_participant_index(all_claims)
    events = build_events(all_claims)
    relationships = build_relationships(all_claims)
    risk_signals = build_risk_signals(all_claims, "person:commi3-mark")
    risk_profiles = build_risk_profiles(risk_signals, "person:commi3-mark")

    write_json(FILES["entities"], {"generated_at": now_iso(), "schema_version": 2, "entities": entities})
    write_json(FILES["evidence"], {"generated_at": now_iso(), "schema_version": 2, "evidence": all_evidence})
    write_json(FILES["raw_mentions"], {"generated_at": now_iso(), "schema_version": 2, "total": len(all_raw), "mentions": [m.__dict__ for m in all_raw]})
    write_json(FILES["mentions"], {"generated_at": now_iso(), "schema_version": 2, "total": len(all_clean), "mentions": all_clean})
    write_json(FILES["claims"], {"generated_at": now_iso(), "schema_version": 2, "total": len(all_claims), "claims": all_claims})
    write_json(FILES["claim_participants"], participant_index)
    write_json(FILES["events"], {"generated_at": now_iso(), "schema_version": 2, "events": events})
    write_json(FILES["stories"], {"generated_at": now_iso(), "schema_version": 2, "stories": []})
    write_json(FILES["relationships"], {"generated_at": now_iso(), "schema_version": 2, "relationships": relationships})
    write_json(FILES["campaigns"], {"generated_at": now_iso(), "schema_version": 2, "campaign_signals": all_campaigns})
    write_json(FILES["risk_signals"], {"generated_at": now_iso(), "schema_version": 2, "risk_signals": risk_signals})
    write_json(FILES["risk_profiles"], {"generated_at": now_iso(), "schema_version": 2, "risk_profiles": risk_profiles})
    write_json(FILES["report_index"], {"generated_at": now_iso(), "schema_version": 2, "reports": []})
    write_json(FILES["manifest"], sorted(manifest, key=lambda x: x.get("published") or "", reverse=True))
    write_json(FILES["state"], {
        "generated_at": now_iso(),
        "schema_version": 2,
        "extractor_version": EXTRACTOR_VERSION,
        "file_count": len(transcript_paths),
        "changed_files": changed,
        "unchanged_files": unchanged,
        "failed_files": failed,
        "files": state_files,
    })

    print("Drama Radar v2 build complete.")
    print(f"Transcript files: {len(transcript_paths)}")
    print(f"Changed/new: {changed}")
    print(f"Unchanged from cache: {unchanged}")
    print(f"Failed: {failed}")
    print(f"Raw mentions: {len(all_raw)}")
    print(f"Clean mentions: {len(all_clean)}")
    print(f"Claims: {len(all_claims)}")
    print(f"Campaign signals: {len(all_campaigns)}")
    print(f"Risk signals about Commi3 Mark: {len(risk_signals)}")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
