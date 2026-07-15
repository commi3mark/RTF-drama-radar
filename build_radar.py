#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from radar_core import load_json, write_json, file_hash, norm, now_iso, timestamp_text, timestamp_url, words

CONFIG = Path("config")
DATA = Path("data")
STATE = Path("state")
CACHE = Path("cache")
ARCHIVE = Path("archive")

SETTINGS = load_json(CONFIG / "settings.json", {})
ENTITIES_PAYLOAD = load_json(CONFIG / "entities.json", {})
ENTITIES = ENTITIES_PAYLOAD.get("entities", [])
ENTITY_BY_ID = {e["entity_id"]: e for e in ENTITIES}

CLAIM_TERMS = {
    "said","says","told","claims","claimed","alleged","accused","denied",
    "reported","wrote","posted","tweeted","replied","threatened","warned",
    "lied","lying","true","false","responded","response"
}

STOPWORDS = {
    "the","and","that","this","with","from","have","what","when","where","which",
    "there","their","they","them","then","than","into","about","because","just",
    "like","really","right","yeah","okay","well","would","could","should","said",
    "says","saying","told","thing","things","people","person","video","stream",
    "know","think","going","want","look","here","were","been","being","your",
    "you're","you","our","his","her","she","him","he","its","it's","for","but",
    "not","was","are","is","of","to","in","on","at","as","it","a","an","i"
}

RISK_RULES = {
    "direct_hostility": (1, [r"\b(?:hate|despise|can't stand)\b", r"\b(?:idiot|moron|fraud|liar|loser|scumbag)\b"]),
    "deplatforming": (2, [r"\b(?:report|mass report|flag|strike|ban|deplatform)\b"]),
    "privacy_signal": (3, [r"\b(?:address|home|house|workplace|phone number|real name|dox|doxx)\b"]),
    "threat_language": (4, [r"\b(?:hurt|attack|kill|shoot|stab|beat|smash|visit his house|come to your house)\b"]),
    "mobilisation": (2, [r"\b(?:everyone should|you all should|tell your followers|go after|dogpile)\b"]),
}

def compile_aliases() -> dict[str, list[dict[str, Any]]]:
    result = {}
    for entity in ENTITIES:
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
        result[entity["entity_id"]] = rules
    return result

ALIASES = compile_aliases()

def detect_entities(text: str) -> list[dict[str, Any]]:
    found = []
    for entity_id, rules in ALIASES.items():
        for rule in rules:
            if rule["pattern"].search(text) and not any(ex.search(text) for ex in rule["exclusions"]):
                found.append({
                    "entity_id": entity_id,
                    "matched_alias": rule["value"],
                    "strength": rule["strength"],
                    "confidence": 1.0 if rule["strength"] == "strong" else 0.68
                })
                break
    return found

def topic_terms(text: str, limit: int = 10) -> list[str]:
    counts = Counter(
        token for token in re.findall(r"[a-z][a-z0-9'-]{2,}", text.lower())
        if token not in STOPWORDS
    )
    return [token for token, _ in counts.most_common(limit)]

def extract_quotes(transcript: dict[str, Any], path: Path) -> tuple[list[dict], list[dict]]:
    segments = transcript.get("segments") or []
    occurrences = []
    quotes = []
    window_seconds = int(SETTINGS.get("quote_context_seconds", 30))

    for i, seg in enumerate(segments):
        if not isinstance(seg, dict):
            continue
        text = norm(str(seg.get("text") or ""))
        if not text:
            continue
        start = float(seg.get("start") or 0)
        matches = detect_entities(text)

        for match in matches:
            occurrences.append({
                "occurrence_id": f"occurrence:{transcript.get('video_id')}:{i}:{match['entity_id']}",
                "entity_id": match["entity_id"],
                "matched_alias": match["matched_alias"],
                "confidence": match["confidence"],
                "video_id": transcript.get("video_id"),
                "title": transcript.get("title"),
                "source": transcript.get("source"),
                "published": transcript.get("published"),
                "segment_index": i,
                "start": start,
                "timestamp": timestamp_text(start),
                "quote": text,
                "receipt_url": timestamp_url(transcript.get("url"), start),
                "transcript_file": path.as_posix()
            })

        if not matches and not any(term in text.lower() for term in CLAIM_TERMS):
            continue

        near_indexes = [
            j for j, candidate in enumerate(segments)
            if isinstance(candidate, dict)
            and abs(float(candidate.get("start") or 0) - start) <= window_seconds
        ]
        context_text = norm(" ".join(str(segments[j].get("text") or "") for j in near_indexes))
        nearby = {}
        for j in near_indexes:
            for entity in detect_entities(norm(str(segments[j].get("text") or ""))):
                nearby[entity["entity_id"]] = entity

        if not matches and len(nearby) == 0:
            continue

        quote_id = f"quote:{transcript.get('video_id')}:{i}"
        quotes.append({
            "quote_id": quote_id,
            "exact_text": text,
            "video_id": transcript.get("video_id"),
            "video_title": transcript.get("title"),
            "channel": transcript.get("source"),
            "published_at": transcript.get("published"),
            "timestamp_seconds": start,
            "timestamp_display": timestamp_text(start),
            "receipt_url": timestamp_url(transcript.get("url"), start),
            "transcript_file": path.as_posix(),
            "direct_entity_ids": sorted({m["entity_id"] for m in matches}),
            "nearby_entity_ids": sorted(nearby),
            "context_text": context_text,
            "context_before": norm(str(segments[i-1].get("text") or "")) if i > 0 and isinstance(segments[i-1], dict) else "",
            "context_after": norm(str(segments[i+1].get("text") or "")) if i+1 < len(segments) and isinstance(segments[i+1], dict) else "",
            "quote_type": "chat_or_quote_readout" if re.search(r"\b(?:chat|superchat|comment|says|writes)\b", text, re.I) else "transcript_line",
            "confidence": max([m["confidence"] for m in matches], default=0.65)
        })
    return occurrences, quotes

def build_context_bundles(transcript: dict[str, Any], quotes: list[dict]) -> tuple[list[dict], list[dict]]:
    segments = transcript.get("segments") or []
    before = int(SETTINGS.get("bundle_before_seconds", 75))
    after = int(SETTINGS.get("bundle_after_seconds", 105))
    bundles = []
    narratives = []
    seen = set()

    for quote in quotes:
        if not quote["nearby_entity_ids"]:
            continue
        trigger = float(quote["timestamp_seconds"])
        key = (int(trigger // 30), tuple(quote["nearby_entity_ids"]))
        if key in seen:
            continue
        seen.add(key)

        start = max(0, trigger - before)
        end = trigger + after
        indexes = [
            i for i, seg in enumerate(segments)
            if isinstance(seg, dict) and start <= float(seg.get("start") or 0) <= end
        ]
        text = norm(" ".join(str(segments[i].get("text") or "") for i in indexes))
        if not text:
            continue

        entity_ids = sorted({
            entity["entity_id"]
            for i in indexes
            for entity in detect_entities(norm(str(segments[i].get("text") or "")))
        })
        entity_names = [ENTITY_BY_ID[eid]["canonical_name"] for eid in entity_ids if eid in ENTITY_BY_ID]
        terms = topic_terms(text)

        bundle_id = f"bundle:{transcript.get('video_id')}:{int(trigger)}"
        bundle = {
            "context_bundle_id": bundle_id,
            "video_id": transcript.get("video_id"),
            "title": transcript.get("title"),
            "channel": transcript.get("source"),
            "published_at": transcript.get("published"),
            "start_seconds": start,
            "end_seconds": end,
            "start_display": timestamp_text(start),
            "end_display": timestamp_text(end),
            "receipt_url": timestamp_url(transcript.get("url"), start),
            "trigger_quote_id": quote["quote_id"],
            "trigger_receipt_url": quote["receipt_url"],
            "involved_entity_ids": entity_ids,
            "involved_entity_names": entity_names,
            "topic_terms": terms,
            "context_text": text,
            "supporting_quote_ids": [q["quote_id"] for q in quotes if start <= q["timestamp_seconds"] <= end],
            "confidence": quote["confidence"]
        }
        bundles.append(bundle)

        summary = make_extractive_summary(text, entity_names)
        narratives.append({
            "narrative_unit_id": f"narrative:{transcript.get('video_id')}:{int(trigger)}",
            "context_bundle_id": bundle_id,
            "headline": make_headline(entity_names, terms),
            "summary": summary,
            "people_involved": entity_names,
            "topic_terms": terms,
            "supporting_quote_ids": bundle["supporting_quote_ids"],
            "uncertainties": uncertainty_flags(text),
            "evidence": {
                "video_title": transcript.get("title"),
                "channel": transcript.get("source"),
                "published_at": transcript.get("published"),
                "timestamp": quote["timestamp_display"],
                "receipt_url": quote["receipt_url"],
                "context_receipt_url": bundle["receipt_url"]
            },
            "confidence": quote["confidence"],
            "review_status": "automatic_extract"
        })
    return bundles, narratives

def make_extractive_summary(text: str, entity_names: list[str]) -> str:
    sentences = [norm(x) for x in re.split(r"(?<=[.!?])\s+|\s+(?=>>)", text) if len(norm(x).split()) >= 5]
    scored = []
    for idx, sentence in enumerate(sentences):
        score = sum(2 for name in entity_names if name.lower() in sentence.lower())
        score += sum(1 for term in CLAIM_TERMS if term in sentence.lower())
        score += min(2, len(sentence.split()) / 25)
        if re.search(r"\b(?:subscribe|superchat|like and subscribe)\b", sentence, re.I):
            score -= 5
        scored.append((score, idx, sentence))
    selected = sorted(scored, reverse=True)[:3]
    selected.sort(key=lambda x: x[1])
    return " ".join(sentence for score, _, sentence in selected if score > 0)

def make_headline(names: list[str], terms: list[str]) -> str:
    people = " and ".join(names[:3]) if names else "Tracked discussion"
    topic = " / ".join(terms[:4]) if terms else "new discussion"
    return f"{people}: {topic}"

def uncertainty_flags(text: str) -> list[str]:
    out = []
    if re.search(r"\b(?:maybe|possibly|apparently|allegedly|supposedly|I think|I guess|not sure|unclear)\b", text, re.I):
        out.append("contains_uncertain_language")
    if re.search(r"\b(?:claimed|alleged|accused|reported)\b", text, re.I):
        out.append("contains_reported_claim")
    if re.search(r"\b(?:chat says|superchat|comment says|writes)\b", text, re.I):
        out.append("may_include_chat_readout")
    return out

def update_people_database(occurrences: list[dict], quotes: list[dict], narratives: list[dict]) -> dict:
    existing = load_json(DATA / "people.json", {"people": []})
    old = {p["entity_id"]: p for p in existing.get("people", [])}
    by_occ = defaultdict(list)
    by_quote = defaultdict(list)
    by_narr = defaultdict(list)

    for item in occurrences:
        by_occ[item["entity_id"]].append(item)
    for quote in quotes:
        for eid in set(quote["direct_entity_ids"] + quote["nearby_entity_ids"]):
            by_quote[eid].append(quote)
    for narr in narratives:
        bundle = narr["context_bundle_id"]
        for person_name in narr.get("people_involved", []):
            for eid, entity in ENTITY_BY_ID.items():
                if entity["canonical_name"] == person_name:
                    by_narr[eid].append(narr)

    people = []
    for entity in ENTITIES:
        eid = entity["entity_id"]
        prior = old.get(eid, {})
        occs = by_occ[eid]
        dates = sorted([o["published"] for o in occs if o.get("published")])
        people.append({
            "entity_id": eid,
            "canonical_name": entity["canonical_name"],
            "aliases": entity.get("aliases", []),
            "accounts": prior.get("accounts", entity.get("accounts", [])),
            "campaigns": prior.get("campaigns", []),
            "relationships": prior.get("relationships", []),
            "history": prior.get("history", []),
            "first_seen": min(filter(None, [prior.get("first_seen"), dates[0] if dates else None]), default=None),
            "last_seen": max(filter(None, [prior.get("last_seen"), dates[-1] if dates else None]), default=None),
            "mention_count": len(occs),
            "quote_ids": sorted({q["quote_id"] for q in by_quote[eid]}),
            "narrative_unit_ids": sorted({n["narrative_unit_id"] for n in by_narr[eid]}),
            "source_count": len({o["video_id"] for o in occs}),
            "updated_at": now_iso()
        })
    return {"generated_at": now_iso(), "schema_version": 5, "people": people}

def story_similarity(a_terms: set[str], a_people: set[str], b_terms: set[str], b_people: set[str]) -> float:
    term_union = a_terms | b_terms
    people_union = a_people | b_people
    term_score = len(a_terms & b_terms) / len(term_union) if term_union else 0
    people_score = len(a_people & b_people) / len(people_union) if people_union else 0
    return 0.65 * term_score + 0.35 * people_score

def update_story_memory(narratives: list[dict]) -> dict:
    existing = load_json(DATA / "stories.json", {"stories": []})
    stories = existing.get("stories", [])
    threshold = float(SETTINGS.get("story_similarity_threshold", 0.34))

    for narrative in narratives:
        n_terms = set(narrative.get("topic_terms", []))
        n_people = set(narrative.get("people_involved", []))
        best = None
        best_score = 0.0
        for story in stories:
            score = story_similarity(
                n_terms, n_people,
                set(story.get("topic_terms", [])),
                set(story.get("people", []))
            )
            if score > best_score:
                best_score = score
                best = story

        if best and best_score >= threshold:
            best["narrative_unit_ids"] = sorted(set(best.get("narrative_unit_ids", []) + [narrative["narrative_unit_id"]]))
            best["supporting_quote_ids"] = sorted(set(best.get("supporting_quote_ids", []) + narrative.get("supporting_quote_ids", [])))
            best["people"] = sorted(set(best.get("people", []) + narrative.get("people_involved", [])))
            best["topic_terms"] = list(dict.fromkeys(best.get("topic_terms", []) + narrative.get("topic_terms", [])))[:20]
            best["last_updated_at"] = narrative["evidence"].get("published_at") or now_iso()
            best["latest_summary"] = narrative.get("summary")
            best["momentum"] = best.get("momentum", 0) + 1
            best["status"] = "active"
        else:
            story_id = f"story:{len(stories)+1:05d}"
            stories.append({
                "story_id": story_id,
                "title": narrative.get("headline"),
                "status": "emerging",
                "first_seen_at": narrative["evidence"].get("published_at"),
                "last_updated_at": narrative["evidence"].get("published_at"),
                "people": narrative.get("people_involved", []),
                "topic_terms": narrative.get("topic_terms", []),
                "narrative_unit_ids": [narrative["narrative_unit_id"]],
                "supporting_quote_ids": narrative.get("supporting_quote_ids", []),
                "latest_summary": narrative.get("summary"),
                "momentum": 1,
                "confidence": narrative.get("confidence", 0.6)
            })

    stories.sort(key=lambda s: (s.get("last_updated_at") or "", s.get("momentum", 0)), reverse=True)
    return {"generated_at": now_iso(), "schema_version": 5, "stories": stories}

def build_relationships(people_payload: dict, stories_payload: dict) -> dict:
    pairs = defaultdict(lambda: {"story_ids": set(), "quote_ids": set(), "count": 0})
    for story in stories_payload.get("stories", []):
        people = story.get("people", [])
        for i in range(len(people)):
            for j in range(i+1, len(people)):
                key = tuple(sorted((people[i], people[j])))
                pairs[key]["story_ids"].add(story["story_id"])
                pairs[key]["quote_ids"].update(story.get("supporting_quote_ids", []))
                pairs[key]["count"] += 1

    relationships = []
    for (a, b), data in pairs.items():
        relationships.append({
            "relationship_id": f"relationship:{a}:{b}",
            "entity_a_name": a,
            "entity_b_name": b,
            "relationship_type": "association",
            "status": "candidate",
            "strength": min(1.0, data["count"] / 5),
            "supporting_story_ids": sorted(data["story_ids"]),
            "supporting_quote_ids": sorted(data["quote_ids"]),
            "warning": "Association only. Co-occurrence does not prove friendship, hostility, or collaboration."
        })
    return {"generated_at": now_iso(), "schema_version": 5, "relationships": relationships}

def build_risk(quotes: list[dict], target_id: str) -> tuple[dict, dict]:
    signals = []
    actor_signals = defaultdict(list)
    for quote in quotes:
        if target_id not in set(quote["direct_entity_ids"] + quote["nearby_entity_ids"]):
            continue
        text = quote["context_text"]
        candidates = [eid for eid in quote["nearby_entity_ids"] if eid != target_id]
        for signal_type, (severity, patterns) in RISK_RULES.items():
            if not any(re.search(p, text, re.I) for p in patterns):
                continue
            actor = candidates[0] if len(candidates) == 1 else None
            signal = {
                "risk_signal_id": f"risk-signal:{quote['quote_id']}:{signal_type}",
                "target_entity_id": target_id,
                "actor_entity_id": actor,
                "candidate_actor_entity_ids": candidates,
                "signal_type": signal_type,
                "severity": severity,
                "quote_id": quote["quote_id"],
                "exact_text": quote["exact_text"],
                "context_text": text,
                "receipt_url": quote["receipt_url"],
                "confidence": quote["confidence"],
                "review_status": "automatic_needs_review"
            }
            signals.append(signal)
            if actor:
                actor_signals[actor].append(signal)

    profiles = []
    for actor, items in actor_signals.items():
        score = min(100, sum(i["severity"] * 8 for i in items))
        label = "minimal" if score < 20 else "watch" if score < 40 else "concerning" if score < 60 else "elevated" if score < 80 else "severe"
        profiles.append({
            "entity_id": actor,
            "target_entity_id": target_id,
            "risk_label": label,
            "risk_score": score,
            "signal_count": len(items),
            "supporting_risk_signal_ids": [i["risk_signal_id"] for i in items],
            "review_status": "automatic_needs_review",
            "warning": "Interaction-risk sorting aid only, not a finding that the person is dangerous."
        })
    return (
        {"generated_at": now_iso(), "schema_version": 5, "risk_signals": signals},
        {"generated_at": now_iso(), "schema_version": 5, "risk_profiles": sorted(profiles, key=lambda p: -p["risk_score"])}
    )

def main() -> int:
    root = Path(SETTINGS.get("transcript_root", "transcripts"))
    state = load_json(STATE / "processing-state.json", {"files": {}})
    previous = state.get("files", {})
    all_occurrences = []
    all_quotes = []
    all_bundles = []
    all_narratives = []
    manifest = []
    files_state = {}
    changed = unchanged = failed = 0

    paths = sorted(p for p in root.rglob("*.json") if p.is_file())
    for path in paths:
        rel = path.as_posix()
        digest = file_hash(path)
        cache_file = CACHE / f"{digest}.json"
        use_cache = previous.get(rel, {}).get("sha256") == digest and cache_file.exists()
        try:
            transcript = load_json(path, {})
            if use_cache:
                cache = load_json(cache_file, {})
                occurrences = cache.get("occurrences", [])
                quotes = cache.get("quotes", [])
                bundles = cache.get("bundles", [])
                narratives = cache.get("narratives", [])
                unchanged += 1
            else:
                occurrences, quotes = extract_quotes(transcript, path)
                bundles, narratives = build_context_bundles(transcript, quotes)
                write_json(cache_file, {
                    "occurrences": occurrences,
                    "quotes": quotes,
                    "bundles": bundles,
                    "narratives": narratives
                })
                changed += 1

            all_occurrences.extend(occurrences)
            all_quotes.extend(quotes)
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
                "sha256": digest,
                "occurrence_count": len(occurrences),
                "quote_count": len(quotes),
                "bundle_count": len(bundles),
                "narrative_count": len(narratives),
                "processed_at": now_iso()
            })
            files_state[rel] = {"sha256": digest, "cache_file": cache_file.as_posix(), "status": "ok", "processed_at": now_iso()}
        except Exception as exc:
            failed += 1
            files_state[rel] = {"sha256": digest, "status": "failed", "error": f"{type(exc).__name__}: {exc}"}

    people_payload = update_people_database(all_occurrences, all_quotes, all_narratives)
    stories_payload = update_story_memory(all_narratives)
    relationships_payload = build_relationships(people_payload, stories_payload)
    risk_signals, risk_profiles = build_risk(all_quotes, SETTINGS.get("target_entity_id", "person:commi3-mark"))

    write_json(DATA / "entities.json", {"generated_at": now_iso(), "schema_version": 5, "entities": ENTITIES})
    write_json(DATA / "people.json", people_payload)
    write_json(DATA / "entity-occurrences.json", {"generated_at": now_iso(), "schema_version": 5, "occurrences": all_occurrences})
    write_json(DATA / "quote-index.json", {"generated_at": now_iso(), "schema_version": 5, "quotes": all_quotes})
    write_json(DATA / "evidence-index.json", {"generated_at": now_iso(), "schema_version": 5, "evidence": all_quotes})
    write_json(DATA / "context-bundles.json", {"generated_at": now_iso(), "schema_version": 5, "context_bundles": all_bundles})
    write_json(DATA / "narrative-units.json", {"generated_at": now_iso(), "schema_version": 5, "narrative_units": all_narratives})
    write_json(DATA / "stories.json", stories_payload)
    write_json(DATA / "relationships.json", relationships_payload)
    write_json(DATA / "campaigns.json", load_json(DATA / "campaigns.json", {"generated_at": now_iso(), "schema_version": 5, "campaigns": []}))
    write_json(DATA / "risk-signals.json", risk_signals)
    write_json(DATA / "commi3-risk-profiles.json", risk_profiles)
    write_json(Path("transcript-manifest.json"), sorted(manifest, key=lambda x: x.get("published") or "", reverse=True))
    write_json(STATE / "processing-state.json", {
        "generated_at": now_iso(),
        "schema_version": 5,
        "file_count": len(paths),
        "changed_files": changed,
        "unchanged_files": unchanged,
        "failed_files": failed,
        "files": files_state
    })

    print("Drama Radar v5 build complete.")
    print(f"Transcript files: {len(paths)}")
    print(f"Changed/new: {changed}")
    print(f"Unchanged from cache: {unchanged}")
    print(f"Failed: {failed}")
    print(f"Quotes: {len(all_quotes)}")
    print(f"Context bundles: {len(all_bundles)}")
    print(f"Narratives: {len(all_narratives)}")
    print(f"Stories: {len(stories_payload.get('stories', []))}")
    print(f"People: {len(people_payload.get('people', []))}")
    return 0 if failed == 0 else 2

if __name__ == "__main__":
    raise SystemExit(main())
