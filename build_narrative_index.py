#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote

EXTRACTOR_VERSION = "4.0.0"
DEFAULT_REPO = "commi3mark/RTF-drama-radar"
DEFAULT_BRANCH = "main"

TRANSCRIPT_ROOT = Path("transcripts")
CONFIG_DIR = Path("config")
DATA_DIR = Path("data")
STATE_DIR = Path("state")
CACHE_DIR = Path("cache/context-bundles")

OUTPUTS = {
    "entities": DATA_DIR / "entities.json",
    "occurrences": DATA_DIR / "entity-occurrences.json",
    "passages": DATA_DIR / "passage-index.json",
    "bundles": DATA_DIR / "context-bundles.json",
    "narratives": DATA_DIR / "narrative-units.json",
    "risk_signals": DATA_DIR / "risk-signals.json",
    "risk_profiles": DATA_DIR / "commi3-risk-profiles.json",
    "manifest": Path("transcript-manifest.json"),
    "state": STATE_DIR / "processing-state.json",
}

CLAIM_TERMS = [
    "said", "says", "told", "claims", "claimed", "alleged", "alleges",
    "accused", "denied", "admitted", "reported", "reports", "wrote",
    "posted", "tweeted", "replied", "threatened", "warned", "according to",
    "lied", "lying", "true", "false", "because", "responded", "response",
]

DEVELOPMENT_PATTERNS = {
    "new_claim": [r"\b(?:claims?|alleges?|says?|said|told|reported)\b"],
    "response": [r"\b(?:responded|response|replied|answered|addressed)\b"],
    "denial": [r"\b(?:denied|denies|not true|never happened|didn't happen)\b"],
    "escalation": [r"\b(?:threatened|reported|flagged|doxx|doxed|strike|ban|lawsuit|police)\b"],
    "campaign_update": [r"\b(?:indiegogo|kickstarter|funded|backers?|stretch goal|closing)\b"],
    "appearance": [r"\b(?:joined|appeared|guest|came on|streamed with)\b"],
}

UNCERTAINTY_PATTERNS = [
    r"\b(?:maybe|possibly|apparently|allegedly|supposedly|I think|I guess|not sure|unclear)\b",
    r"\b(?:someone said|people are saying|I heard|rumor|rumour)\b",
]

RISK_RULES = {
    "direct_hostility": (1, [r"\b(?:hate|despise|can't stand)\b", r"\b(?:idiot|moron|fraud|liar|loser|scumbag)\b"]),
    "deplatforming": (2, [r"\b(?:report|mass report|flag|strike|ban|deplatform)\b"]),
    "privacy_signal": (3, [r"\b(?:address|home|house|workplace|phone number|real name|dox|doxx)\b"]),
    "threat_language": (4, [r"\b(?:hurt|attack|kill|shoot|stab|beat|smash|visit his house|come to your house)\b"]),
    "mobilisation": (2, [r"\b(?:everyone should|you all should|tell your followers|go after|dogpile)\b"]),
}

STOPWORDS = {
    "the","and","that","this","with","from","have","what","when","where","which",
    "there","their","they","them","then","than","into","about","because","just",
    "like","really","right","yeah","okay","well","would","could","should","said",
    "says","saying","told","thing","things","people","person","video","stream",
    "know","think","going","want","look","here","were","been","being","your",
    "you're","you","our","his","her","she","him","he","its","it's","for","but",
    "not","was","are","is","of","to","in","on","at","as","it","a","an","i",
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
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def norm(text: str) -> str:
    text = text.replace("\u2018", "'").replace("\u2019", "'")
    return re.sub(r"\s+", " ", text).strip()


def timestamp_text(seconds: float) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def timestamp_url(url: str | None, seconds: float) -> str | None:
    if not url:
        return None
    if "youtube.com" in url or "youtu.be" in url:
        sep = "&" if "?" in url else "?"
        return f"{url}{sep}t={max(0, int(seconds))}s"
    return url


def github_raw_url(repo: str, branch: str, rel: str) -> str:
    encoded = "/".join(quote(part, safe="") for part in rel.split("/"))
    return f"https://raw.githubusercontent.com/{repo}/refs/heads/{branch}/{encoded}"


def iter_transcripts(root: Path) -> Iterable[Path]:
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
    out = {}
    for entity in entities:
        rules = []
        for alias in entity.get("aliases", []):
            value = alias["value"] if isinstance(alias, dict) else str(alias)
            strength = alias.get("strength", "strong") if isinstance(alias, dict) else "strong"
            exclusions = alias.get("exclusions", []) if isinstance(alias, dict) else []
            rules.append({
                "value": value,
                "strength": strength,
                "pattern": re.compile(rf"(?<!\w){re.escape(value)}(?!\w)", re.I),
                "exclusions": [re.compile(x, re.I) for x in exclusions],
            })
        out[entity["entity_id"]] = rules
    return out


def detect_entities(text: str, alias_rules: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    found = []
    for entity_id, rules in alias_rules.items():
        for rule in rules:
            if not rule["pattern"].search(text):
                continue
            if any(ex.search(text) for ex in rule["exclusions"]):
                continue
            found.append({
                "entity_id": entity_id,
                "matched_alias": rule["value"],
                "alias_strength": rule["strength"],
                "confidence": 1.0 if rule["strength"] == "strong" else 0.68,
            })
            break
    return found


def sentence_score(text: str, entity_count: int) -> float:
    low = text.lower()
    claim_hits = sum(1 for term in CLAIM_TERMS if term in low)
    punctuation_bonus = 1 if any(ch in text for ch in ".?!") else 0
    length_bonus = min(2.0, len(text.split()) / 20)
    return claim_hits * 2 + entity_count * 1.5 + punctuation_bonus + length_bonus


def topic_terms(text: str, aliases: set[str]) -> list[str]:
    words = re.findall(r"[A-Za-z][A-Za-z0-9'-]{2,}", text.lower())
    counts = Counter(
        word for word in words
        if word not in STOPWORDS and word not in aliases
    )
    return [word for word, _ in counts.most_common(8)]


def extract_sentences(text: str) -> list[str]:
    pieces = re.split(r"(?<=[.!?])\s+|\s+(?=>>)", text)
    return [norm(piece) for piece in pieces if len(norm(piece).split()) >= 4]


def choose_summary_sentences(text: str, entity_names: list[str], limit: int = 3) -> list[str]:
    sentences = extract_sentences(text)
    scored = []
    for index, sentence in enumerate(sentences):
        entity_hits = sum(1 for name in entity_names if name.lower() in sentence.lower())
        score = sentence_score(sentence, entity_hits)
        if re.search(r"\b(?:subscribe|superchat|super chat|like and subscribe)\b", sentence, re.I):
            score -= 5
        scored.append((score, index, sentence))
    selected = sorted(scored, reverse=True)[:limit]
    selected.sort(key=lambda item: item[1])
    return [item[2] for item in selected if item[0] > 0]


def classify_development(text: str) -> list[str]:
    labels = []
    for label, patterns in DEVELOPMENT_PATTERNS.items():
        if any(re.search(pattern, text, re.I) for pattern in patterns):
            labels.append(label)
    return labels or ["discussion"]


def uncertainty_flags(text: str) -> list[str]:
    flags = []
    if any(re.search(pattern, text, re.I) for pattern in UNCERTAINTY_PATTERNS):
        flags.append("contains_uncertain_language")
    if re.search(r"\b(?:accused|alleged|claimed|reported)\b", text, re.I):
        flags.append("contains_reported_claim")
    if re.search(r"\b(?:chat says|superchat|comment says|writes)\b", text, re.I):
        flags.append("may_include_chat_readout")
    return flags


def build_occurrences(transcript: dict[str, Any], path: Path, alias_rules: dict[str, list[dict[str, Any]]]) -> tuple[list[dict[str, Any]], dict[int, list[dict[str, Any]]]]:
    occurrences = []
    segment_entities = {}
    for i, seg in enumerate(transcript.get("segments") or []):
        if not isinstance(seg, dict):
            continue
        text = norm(str(seg.get("text") or ""))
        entities = detect_entities(text, alias_rules)
        segment_entities[i] = entities
        for item in entities:
            start = float(seg.get("start") or 0)
            occurrences.append({
                "occurrence_id": f"occurrence:{transcript.get('video_id')}:{i}:{item['entity_id']}",
                "entity_id": item["entity_id"],
                "matched_alias": item["matched_alias"],
                "alias_strength": item["alias_strength"],
                "confidence": item["confidence"],
                "video_id": transcript.get("video_id"),
                "title": transcript.get("title"),
                "source": transcript.get("source"),
                "published": transcript.get("published"),
                "segment_index": i,
                "start": start,
                "timestamp": timestamp_text(start),
                "quote": text,
                "receipt_url": timestamp_url(transcript.get("url"), start),
                "transcript_file": path.as_posix(),
            })
    return occurrences, segment_entities


def candidate_passages(transcript: dict[str, Any], path: Path, segment_entities: dict[int, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    segments = transcript.get("segments") or []
    passages = []
    used = set()

    for i, seg in enumerate(segments):
        if not isinstance(seg, dict):
            continue
        start = float(seg.get("start") or 0)
        indexes = [
            j for j, candidate in enumerate(segments)
            if isinstance(candidate, dict) and abs(float(candidate.get("start") or 0) - start) <= 20
        ]
        entities = {}
        for j in indexes:
            for item in segment_entities.get(j, []):
                entities[item["entity_id"]] = item
        if not entities:
            continue

        full_context = norm(" ".join(str(segments[j].get("text") or "") for j in indexes))
        claim_hits = sum(1 for term in CLAIM_TERMS if term in full_context.lower())
        if len(entities) < 2 and claim_hits == 0:
            continue

        key = (int(start // 15), tuple(sorted(entities)))
        if key in used:
            continue
        used.add(key)

        passages.append({
            "passage_id": f"passage:{transcript.get('video_id')}:{int(start)}",
            "trigger_segment_index": i,
            "trigger_start": start,
            "involved_entity_ids": sorted(entities),
            "entity_matches": list(entities.values()),
            "claim_term_count": claim_hits,
            "claim_excerpt": norm(str(seg.get("text") or "")),
            "full_context": full_context,
            "source": {
                "source_type": "youtube",
                "video_id": transcript.get("video_id"),
                "title": transcript.get("title"),
                "channel": transcript.get("source"),
                "published_at": transcript.get("published"),
                "timestamp_seconds": start,
                "timestamp_display": timestamp_text(start),
                "url": transcript.get("url"),
                "receipt_url": timestamp_url(transcript.get("url"), start),
                "transcript_file": path.as_posix(),
            },
            "confidence": round(min(0.98, 0.5 + 0.08 * claim_hits + 0.08 * len(entities)), 3),
        })
    return passages


def bundle_passages(
    transcript: dict[str, Any],
    path: Path,
    passages: list[dict[str, Any]],
    entities_by_id: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    segments = transcript.get("segments") or []
    bundles = []
    narratives = []
    used_ranges = []

    for passage in sorted(passages, key=lambda p: p["trigger_start"]):
        trigger = passage["trigger_start"]

        # 75 seconds before and 105 seconds after: broad enough for narrative,
        # still small enough to inspect quickly.
        start_time = max(0.0, trigger - 75)
        end_time = trigger + 105

        # Merge heavily overlapping bundles with the same people.
        duplicate = False
        for prior_start, prior_end, prior_entities in used_ranges:
            overlap = max(0.0, min(end_time, prior_end) - max(start_time, prior_start))
            span = min(end_time - start_time, prior_end - prior_start)
            if span > 0 and overlap / span >= 0.7 and set(prior_entities) == set(passage["involved_entity_ids"]):
                duplicate = True
                break
        if duplicate:
            continue

        indexes = [
            i for i, seg in enumerate(segments)
            if isinstance(seg, dict)
            and start_time <= float(seg.get("start") or 0) <= end_time
        ]
        if not indexes:
            continue

        text = norm(" ".join(str(segments[i].get("text") or "") for i in indexes))
        involved = passage["involved_entity_ids"]
        entity_names = [entities_by_id[eid]["canonical_name"] for eid in involved if eid in entities_by_id]
        aliases = {
            (alias["value"] if isinstance(alias, dict) else str(alias)).lower()
            for eid in involved
            for alias in entities_by_id.get(eid, {}).get("aliases", [])
        }

        summary_sentences = choose_summary_sentences(text, entity_names, limit=3)
        if not summary_sentences:
            summary_sentences = [passage["claim_excerpt"]]

        topics = topic_terms(text, aliases)
        developments = classify_development(text)
        uncertainties = uncertainty_flags(text)

        bundle_id = f"bundle:{transcript.get('video_id')}:{int(trigger)}"
        bundle = {
            "context_bundle_id": bundle_id,
            "video_id": transcript.get("video_id"),
            "title": transcript.get("title"),
            "channel": transcript.get("source"),
            "published_at": transcript.get("published"),
            "start_seconds": start_time,
            "end_seconds": end_time,
            "start_display": timestamp_text(start_time),
            "end_display": timestamp_text(end_time),
            "receipt_url": timestamp_url(transcript.get("url"), start_time),
            "trigger_receipt_url": passage["source"]["receipt_url"],
            "trigger_excerpt": passage["claim_excerpt"],
            "involved_entity_ids": involved,
            "involved_entity_names": entity_names,
            "topic_terms": topics,
            "development_types": developments,
            "uncertainty_flags": uncertainties,
            "context_text": text,
            "source_passage_ids": [passage["passage_id"]],
            "confidence": passage["confidence"],
        }
        bundles.append(bundle)

        narrative_summary = " ".join(summary_sentences)
        narrative = {
            "narrative_unit_id": f"narrative:{transcript.get('video_id')}:{int(trigger)}",
            "context_bundle_id": bundle_id,
            "headline": make_headline(entity_names, topics, developments),
            "summary": narrative_summary,
            "what_happened": narrative_summary,
            "people_involved": entity_names,
            "topic_terms": topics,
            "development_types": developments,
            "new_development": infer_new_development(developments, summary_sentences),
            "uncertainties": human_uncertainties(uncertainties),
            "evidence": {
                "video_title": transcript.get("title"),
                "channel": transcript.get("source"),
                "published_at": transcript.get("published"),
                "timestamp": timestamp_text(trigger),
                "receipt_url": passage["source"]["receipt_url"],
                "context_receipt_url": timestamp_url(transcript.get("url"), start_time),
                "trigger_excerpt": passage["claim_excerpt"],
            },
            "confidence": passage["confidence"],
            "review_status": "automatic_extract",
        }
        narratives.append(narrative)
        used_ranges.append((start_time, end_time, involved))

    return bundles, narratives


def make_headline(names: list[str], topics: list[str], developments: list[str]) -> str:
    people = " and ".join(names[:3]) if names else "Tracked figures"
    topic = " / ".join(topics[:3]) if topics else "ongoing discussion"
    development = developments[0].replace("_", " ")
    return f"{people}: {development} around {topic}"


def infer_new_development(developments: list[str], sentences: list[str]) -> str:
    if "denial" in developments:
        return "The passage contains a denial or rejection of an earlier claim."
    if "response" in developments:
        return "The passage contains a response to earlier discussion."
    if "escalation" in developments:
        return "The passage contains language suggesting escalation."
    if "campaign_update" in developments:
        return "The passage contains a campaign-related update."
    if "new_claim" in developments:
        return "The passage introduces or repeats a claim."
    return sentences[0] if sentences else "The passage continues the discussion."


def human_uncertainties(flags: list[str]) -> list[str]:
    out = []
    if "contains_uncertain_language" in flags:
        out.append("The passage includes uncertain or speculative language.")
    if "contains_reported_claim" in flags:
        out.append("A claim is reported, but the passage alone does not establish that it is true.")
    if "may_include_chat_readout" in flags:
        out.append("Part of the passage may be a chat or comment readout rather than the host's own claim.")
    return out


def build_risk(narratives: list[dict[str, Any]], target: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    signals = []
    by_actor = defaultdict(list)

    for narrative in narratives:
        bundle_people = narrative.get("people_involved", [])
        # Entity IDs are retrieved from the bundle later through the narrative id mapping,
        # so risk attribution stays conservative.
        text = f"{narrative.get('summary','')} {narrative.get('evidence',{}).get('trigger_excerpt','')}"
        for signal_type, (severity, patterns) in RISK_RULES.items():
            if not any(re.search(pattern, text, re.I) for pattern in patterns):
                continue
            signals.append({
                "risk_signal_id": f"risk-signal:{narrative['narrative_unit_id']}:{signal_type}",
                "target_entity_id": target,
                "actor_entity_id": None,
                "candidate_actor_names": bundle_people,
                "signal_type": signal_type,
                "severity": severity,
                "narrative_unit_id": narrative["narrative_unit_id"],
                "exact_excerpt": narrative.get("evidence", {}).get("trigger_excerpt"),
                "summary": narrative.get("summary"),
                "receipt_url": narrative.get("evidence", {}).get("receipt_url"),
                "confidence": narrative.get("confidence", 0.5),
                "review_status": "automatic_needs_review",
            })

    # Profiles are intentionally empty until an actor can be assigned confidently.
    return signals, []


def main() -> int:
    parser = argparse.ArgumentParser(description="Build narrative-ready Drama Radar context bundles.")
    parser.add_argument("--transcripts", default=str(TRANSCRIPT_ROOT))
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--branch", default=DEFAULT_BRANCH)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    for directory in [DATA_DIR, STATE_DIR, CACHE_DIR]:
        directory.mkdir(parents=True, exist_ok=True)

    entities = load_entities()
    entities_by_id = {e["entity_id"]: e for e in entities}
    alias_rules = compile_aliases(entities)

    previous_state = load_json(OUTPUTS["state"], {})
    previous_files = previous_state.get("files", {})

    all_occurrences = []
    all_passages = []
    all_bundles = []
    all_narratives = []
    manifest = []
    state_files = {}

    changed = unchanged = failed = 0
    paths = list(iter_transcripts(Path(args.transcripts)))

    for path in paths:
        rel = path.as_posix()
        digest = file_hash(path)
        prior = previous_files.get(rel, {})
        cache_file = CACHE_DIR / f"{digest}-{EXTRACTOR_VERSION}.json"

        use_cache = (
            not args.force
            and prior.get("sha256") == digest
            and prior.get("extractor_version") == EXTRACTOR_VERSION
            and cache_file.exists()
        )

        try:
            transcript = load_json(path, {})
            if not isinstance(transcript, dict):
                raise ValueError("Transcript root must be an object")

            if use_cache:
                cached = load_json(cache_file, {})
                occurrences = cached.get("occurrences", [])
                passages = cached.get("passages", [])
                bundles = cached.get("bundles", [])
                narratives = cached.get("narratives", [])
                unchanged += 1
            else:
                occurrences, segment_entities = build_occurrences(transcript, path, alias_rules)
                passages = candidate_passages(transcript, path, segment_entities)
                bundles, narratives = bundle_passages(transcript, path, passages, entities_by_id)
                write_json(cache_file, {
                    "occurrences": occurrences,
                    "passages": passages,
                    "bundles": bundles,
                    "narratives": narratives,
                })
                changed += 1

            all_occurrences.extend(occurrences)
            all_passages.extend(passages)
            all_bundles.extend(bundles)
            all_narratives.extend(narratives)

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
                "entity_occurrence_count": len(occurrences),
                "passage_count": len(passages),
                "context_bundle_count": len(bundles),
                "narrative_unit_count": len(narratives),
                "processed_at": now_iso(),
            })

            state_files[rel] = {
                "sha256": digest,
                "extractor_version": EXTRACTOR_VERSION,
                "cache_file": cache_file.as_posix(),
                "processed_at": now_iso(),
                "status": "ok",
            }

        except Exception as error:
            failed += 1
            state_files[rel] = {
                "sha256": digest,
                "extractor_version": EXTRACTOR_VERSION,
                "processed_at": now_iso(),
                "status": "failed",
                "error": f"{type(error).__name__}: {error}",
            }
            print(f"FAILED: {rel}: {error}", file=sys.stderr)

    all_occurrences.sort(key=lambda x: (x.get("published") or "", x.get("start") or 0), reverse=True)
    all_passages.sort(key=lambda x: (x["source"].get("published_at") or "", x["source"].get("timestamp_seconds") or 0), reverse=True)
    all_bundles.sort(key=lambda x: (x.get("published_at") or "", x.get("start_seconds") or 0), reverse=True)
    all_narratives.sort(key=lambda x: (x["evidence"].get("published_at") or "", x["evidence"].get("timestamp") or ""), reverse=True)

    risk_signals, risk_profiles = build_risk(all_narratives, "person:commi3-mark")

    write_json(OUTPUTS["entities"], {"generated_at": now_iso(), "schema_version": 4, "entities": entities})
    write_json(OUTPUTS["occurrences"], {"generated_at": now_iso(), "schema_version": 4, "total": len(all_occurrences), "occurrences": all_occurrences})
    write_json(OUTPUTS["passages"], {"generated_at": now_iso(), "schema_version": 4, "total": len(all_passages), "passages": all_passages})
    write_json(OUTPUTS["bundles"], {"generated_at": now_iso(), "schema_version": 4, "total": len(all_bundles), "context_bundles": all_bundles})
    write_json(OUTPUTS["narratives"], {"generated_at": now_iso(), "schema_version": 4, "total": len(all_narratives), "narrative_units": all_narratives})
    write_json(OUTPUTS["risk_signals"], {"generated_at": now_iso(), "schema_version": 4, "risk_signals": risk_signals})
    write_json(OUTPUTS["risk_profiles"], {"generated_at": now_iso(), "schema_version": 4, "risk_profiles": risk_profiles})
    write_json(OUTPUTS["manifest"], sorted(manifest, key=lambda x: x.get("published") or "", reverse=True))
    write_json(OUTPUTS["state"], {
        "generated_at": now_iso(),
        "schema_version": 4,
        "extractor_version": EXTRACTOR_VERSION,
        "file_count": len(paths),
        "changed_files": changed,
        "unchanged_files": unchanged,
        "failed_files": failed,
        "files": state_files,
    })

    print("Drama Radar v4 narrative build complete.")
    print(f"Transcript files: {len(paths)}")
    print(f"Changed/new: {changed}")
    print(f"Unchanged from cache: {unchanged}")
    print(f"Failed: {failed}")
    print(f"Entity occurrences: {len(all_occurrences)}")
    print(f"Searchable passages: {len(all_passages)}")
    print(f"Context bundles: {len(all_bundles)}")
    print(f"Narrative units: {len(all_narratives)}")
    print(f"Risk signals about Commi3 Mark: {len(risk_signals)}")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
