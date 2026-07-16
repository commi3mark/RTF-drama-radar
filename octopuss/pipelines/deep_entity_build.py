from __future__ import annotations

import hashlib
import json
import math
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
OCT = ROOT / "octopuss"
INTEL = OCT / "intelligence"
ENTITY_ROOT = INTEL / "entities"
PEOPLE_ROOT = ENTITY_ROOT / "people"
EVIDENCE_ROOT = INTEL / "evidence" / "entities"
REPORT_ROOT = INTEL / "reports" / "entities"
CONFIG = OCT / "config" / "entity-seeds.json"
NOW = datetime.now(timezone.utc).isoformat()

URL_RE = re.compile(r"https?://[^\s<>\]\[\)\(\"']+", re.I)
EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
HANDLE_RE = re.compile(r"(?<![\w@])@[A-Za-z0-9_]{2,32}\b")
CLAIM_RE = re.compile(r"\b(?:said|says|claimed|claims|accused|alleged|argued|insisted|denied|admitted|called|told|believes?|thinks?)\b", re.I)
APPEARANCE_RE = re.compile(r"\b(?:joined by|with special guest|guest|welcome|appears?|panel|interview|speaks? with|talks? with)\b", re.I)
COMIC_RE = re.compile(r"\b(?:comic|comics|graphic novel|campaign|indiegogo|fundraiser|backers?|fulfilled|fulfilment|publisher|writer|artist|issue|volume|book|character)\b", re.I)
BEHAVIOUR_TERMS = {
    "criticism": re.compile(r"\b(?:criticis|attack|mock|ridicul|roast|call(?:ed)? out|expos|slam|drag)\w*\b", re.I),
    "praise": re.compile(r"\b(?:prais|support|recommend|love|respect|credit|great|excellent)\w*\b", re.I),
    "conflict": re.compile(r"\b(?:fight|feud|war|beef|dispute|argument|lawsuit|strike|threaten|enemy|rival)\w*\b", re.I),
    "evidence_language": re.compile(r"\b(?:receipt|evidence|proof|screenshot|clip|timestamp|source|document)\w*\b", re.I),
    "certainty": re.compile(r"\b(?:definitely|obviously|clearly|absolutely|certainly|without question)\b", re.I),
    "uncertainty": re.compile(r"\b(?:maybe|perhaps|probably|possibly|I think|I guess|apparently|allegedly)\b", re.I),
}
COMMUNITY_TERMS = ["ComicsGate", "Comics Gate", "Rippaverse", "Fandom Menace", "CG", "RTF", "Russian Troll Factory"]
TOPIC_TERMS = [
    "ComicsGate", "Rippaverse", "crowdfunding", "comic", "campaign", "YouTube", "Twitter", "X", "Kick",
    "lawsuit", "copyright", "strike", "drama", "FNT", "Friday Night Tights", "Russian Troll Factory",
    "Drama Radar", "ComicsGate Kings", "Back Me Bro", "fulfilment", "customers", "audience", "channel"
]
STOP_TERMS = {"the", "and", "that", "this", "with", "from", "have", "they", "you", "your", "about", "just", "like", "what", "when", "where", "which", "there", "their", "then", "than", "because", "would", "could", "should", "really", "going", "know", "right", "yeah", "okay", "thing", "things", "people"}


def load(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")


def norm(value: str) -> str:
    return re.sub(r"[^a-z0-9@]+", " ", str(value).casefold().replace("’", "'")).strip()


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", norm(value)).strip("-")[:90] or "unknown"


def evidence_id(entity_id: str, video_id: str, start: float, kind: str) -> str:
    raw = f"{entity_id}|{video_id}|{start:.3f}|{kind}"
    return hashlib.sha1(raw.encode("utf-8", "ignore")).hexdigest()[:20]


def timestamp_url(url: str | None, seconds: float) -> str | None:
    if not url:
        return None
    return f"{url}{'&' if '?' in url else '?'}t={int(max(0, seconds))}s"


def unique_dicts(rows: Iterable[dict], keys: tuple[str, ...]) -> list[dict]:
    out, seen = [], set()
    for row in rows:
        marker = tuple(str(row.get(k, "")) for k in keys)
        if marker in seen:
            continue
        seen.add(marker)
        out.append(row)
    return out


def words(text: str) -> list[str]:
    return [w for w in re.findall(r"[A-Za-z][A-Za-z'-]{2,}", text.casefold()) if w not in STOP_TERMS]


def title_series_key(title: str) -> str:
    title = re.sub(r"\([^)]*\d{4}[^)]*\)", "", title)
    title = re.sub(r"\b(?:episode|ep|part|show|stream|live)\s*[#:_-]?\s*\d+\b", "", title, flags=re.I)
    title = re.sub(r"[#|:_-]+\s*\d+\b", "", title)
    title = re.sub(r"\b(?:january|february|march|april|may|june|july|august|september|october|november|december)\s+\d{1,2}(?:st|nd|rd|th)?(?:,?\s+\d{4})?\b", "", title, flags=re.I)
    title = re.sub(r"\s+", " ", title).strip(" -–—:|#")
    return title[:140]


def context_block(segments: list[dict], index: int, radius: int = 6) -> tuple[float, float, str]:
    lo, hi = max(0, index - radius), min(len(segments), index + radius + 1)
    chosen = segments[lo:hi]
    start = float(chosen[0].get("start", 0) or 0) if chosen else 0.0
    end = 0.0
    if chosen:
        last = chosen[-1]
        end = float(last.get("start", 0) or 0) + float(last.get("duration", 0) or 0)
    return start, end, " ".join(str(x.get("text", "")) for x in chosen).strip()


def build_alias_patterns(registry: list[dict], seeds: dict) -> tuple[dict[str, re.Pattern], dict[str, dict]]:
    seed_by_id = {p["entity_id"]: p for p in seeds.get("people", [])}
    patterns: dict[str, re.Pattern] = {}
    data: dict[str, dict] = {}
    for row in registry:
        eid = row["entity_id"]
        seed = seed_by_id.get(eid, {})
        aliases = []
        for value in [row.get("canonical_name"), *row.get("aliases", []), *seed.get("aliases", []), *seed.get("accounts", [])]:
            if value and norm(value) not in {norm(x) for x in aliases}:
                aliases.append(value)
        safe = [re.escape(norm(a)) for a in sorted(aliases, key=lambda x: len(norm(x)), reverse=True) if len(norm(a)) >= 3]
        if safe:
            patterns[eid] = re.compile(r"(?<!\w)(?:" + "|".join(safe) + r")(?!\w)", re.I)
        data[eid] = {**row, "aliases": aliases, "seed": seed}
    return patterns, data


def stage(label: str, number: int, total: int, started: float) -> None:
    elapsed = time.time() - started
    print(f"\n[{number:02d}/{total:02d}] {label}  ({elapsed:.1f}s elapsed)", flush=True)


def main() -> int:
    overall_started = time.time()
    print("=" * 76)
    print("OCTOPUSS — DEEP ENTITY BUILD")
    print("=" * 76)

    registry_doc = load(ENTITY_ROOT / "entity-registry.json", {})
    registry = registry_doc.get("entities", [])
    if not registry:
        print("No resolved entities found. Run ENTITY SCAN first.")
        return 2
    seeds = load(CONFIG, {"people": []})
    patterns, entities = build_alias_patterns(registry, seeds)
    index_doc = load(ROOT / "transcripts" / "transcript-index.json", {})
    metas = index_doc.get("transcripts", []) if isinstance(index_doc, dict) else []
    radar = load(ROOT / "drama-radar.json", [])
    radar = radar if isinstance(radar, list) else []

    total_stages = 20
    stage("Intelligence preparation", 1, total_stages, overall_started)
    corpus: list[dict] = []
    missing: list[str] = []
    for meta in metas:
        path = ROOT / str(meta.get("path", ""))
        doc = load(path, None)
        if not isinstance(doc, dict):
            missing.append(str(meta.get("path", "")))
            continue
        segments = doc.get("segments", []) if isinstance(doc.get("segments"), list) else []
        corpus.append({
            "video_id": str(doc.get("video_id") or meta.get("video_id") or ""),
            "title": str(doc.get("title") or meta.get("title") or ""),
            "source": str(doc.get("source") or meta.get("source") or "Unknown"),
            "published": doc.get("published") or meta.get("published"),
            "url": doc.get("url") or f"https://www.youtube.com/watch?v={doc.get('video_id') or meta.get('video_id') or ''}",
            "path": str(meta.get("path") or ""),
            "segments": segments,
            "full_text": " ".join(str(x.get("text", "")) for x in segments),
        })
    print(f"Opened {len(corpus)} transcripts; {len(missing)} missing.")

    stage("Identity and alias evidence", 2, total_stages, overall_started)
    evidence_by_entity: dict[str, list[dict]] = defaultdict(list)
    alias_observations: dict[str, Counter] = defaultdict(Counter)
    video_entities: dict[str, set[str]] = defaultdict(set)
    for doc in corpus:
        normalized_segments = [norm(str(s.get("text", ""))) for s in doc["segments"]]
        normalized_title = norm(doc["title"])
        normalized_source = norm(doc["source"])
        for eid, pattern in patterns.items():
            aliases = entities[eid]["aliases"]
            # Source/title hits are separate evidence.
            for location, text, normalized in [("source", doc["source"], normalized_source), ("title", doc["title"], normalized_title)]:
                hits = list(pattern.finditer(normalized))
                if hits:
                    video_entities[doc["video_id"]].add(eid)
                    for hit in hits:
                        matched = hit.group(0)
                        alias_observations[eid][matched] += 1
                        ev_id = evidence_id(eid, doc["video_id"], 0.0, location + matched)
                        evidence_by_entity[eid].append({
                            "evidence_id": ev_id, "evidence_type": f"{location}_identity_match", "entity_id": eid,
                            "video_id": doc["video_id"], "title": doc["title"], "source": doc["source"],
                            "published": doc["published"], "transcript_path": doc["path"], "timestamp_seconds": 0.0,
                            "timestamp_url": doc["url"], "matched_text": matched, "context": text,
                            "confidence": 0.98, "status": "confirmed_alias_match"
                        })
            for i, normalized in enumerate(normalized_segments):
                hits = list(pattern.finditer(normalized))
                if not hits:
                    continue
                video_entities[doc["video_id"]].add(eid)
                start, end, context = context_block(doc["segments"], i, radius=6)
                for hit in hits:
                    matched = hit.group(0)
                    alias_observations[eid][matched] += 1
                    ev_id = evidence_id(eid, doc["video_id"], start, "transcript" + matched)
                    evidence_by_entity[eid].append({
                        "evidence_id": ev_id, "evidence_type": "transcript_identity_match", "entity_id": eid,
                        "video_id": doc["video_id"], "title": doc["title"], "source": doc["source"],
                        "published": doc["published"], "transcript_path": doc["path"], "timestamp_seconds": round(start, 3),
                        "end_seconds": round(end, 3), "timestamp_url": timestamp_url(doc["url"], start),
                        "matched_text": matched, "context": context, "confidence": 0.96,
                        "status": "confirmed_alias_match"
                    })
    for eid in list(evidence_by_entity):
        evidence_by_entity[eid] = unique_dicts(evidence_by_entity[eid], ("evidence_id",))
    print(f"Built {sum(len(v) for v in evidence_by_entity.values())} identity evidence objects.")

    stage("Channels, websites, socials and emails", 3, total_stages, overall_started)
    owned_radar: dict[str, list[dict]] = defaultdict(list)
    links: dict[str, dict[str, dict]] = defaultdict(dict)
    socials: dict[str, dict[str, dict]] = defaultdict(dict)
    emails: dict[str, dict[str, dict]] = defaultdict(dict)
    channels: dict[str, Counter] = defaultdict(Counter)
    canonical_by_norm = {norm(v["canonical_name"]): eid for eid, v in entities.items()}
    for item in radar:
        src = str(item.get("source") or "")
        src_eid = canonical_by_norm.get(norm(src))
        if not src_eid:
            for eid, pat in patterns.items():
                if pat.fullmatch(norm(src)):
                    src_eid = eid
                    break
        if not src_eid:
            continue
        owned_radar[src_eid].append(item)
        channels[src_eid][src] += 1
        desc = str(item.get("description") or "")
        evidence_key = str(item.get("id") or item.get("url") or item.get("title"))
        for url in URL_RE.findall(desc):
            url = url.rstrip(".,;!?)")
            host = urlparse(url).netloc.casefold().removeprefix("www.")
            obj = {"url": url, "domain": host, "status": "candidate_owned_or_promoted", "observation_count": 0, "evidence_ids": []}
            current = links[src_eid].setdefault(url.casefold(), obj)
            current["observation_count"] += 1
            current["evidence_ids"].append(evidence_key)
        for handle in HANDLE_RE.findall(desc):
            current = socials[src_eid].setdefault(handle.casefold(), {"handle": handle, "platform": "unknown", "status": "candidate", "observation_count": 0, "evidence_ids": []})
            current["observation_count"] += 1; current["evidence_ids"].append(evidence_key)
        for email in EMAIL_RE.findall(desc):
            current = emails[src_eid].setdefault(email.casefold(), {"address": email, "visibility": "public_description", "status": "candidate", "observation_count": 0, "evidence_ids": []})
            current["observation_count"] += 1; current["evidence_ids"].append(evidence_key)

    stage("Recurring shows and formats", 4, total_stages, overall_started)
    show_counts: dict[str, Counter] = defaultdict(Counter)
    schedules: dict[str, Counter] = defaultdict(Counter)
    for eid, items in owned_radar.items():
        for item in items:
            key = title_series_key(str(item.get("title") or ""))
            if key:
                show_counts[eid][key] += 1
            published = str(item.get("published") or "")
            try:
                dt = datetime.fromisoformat(published)
                schedules[eid][f"weekday_{dt.strftime('%A')}"] += 1
                schedules[eid][f"hour_{dt.hour:02d}_utc"] += 1
            except Exception:
                pass

    stage("Projects and project relationships", 5, total_stages, overall_started)
    project_rows: dict[str, list[dict]] = defaultdict(list)
    for eid, info in entities.items():
        seed_projects = info.get("seed", {}).get("projects", [])
        contexts = " ".join(x["context"] for x in evidence_by_entity.get(eid, []))
        for project in seed_projects:
            count = len(re.findall(r"(?<!\w)" + re.escape(norm(project)) + r"(?!\w)", norm(contexts), flags=re.I))
            radar_count = sum(1 for x in radar if norm(project) in norm(str(x.get("title", "")) + " " + str(x.get("description", ""))))
            project_rows[eid].append({"name": project, "relationship": "known_project_cue", "status": "confirmed_relationship", "is_alias": False, "transcript_observations": count, "radar_observations": radar_count})

    stage("Associates and co-appearances", 6, total_stages, overall_started)
    co_counts: dict[str, Counter] = defaultdict(Counter)
    co_videos: dict[tuple[str, str], set[str]] = defaultdict(set)
    for video_id, eids in video_entities.items():
        for left in eids:
            for right in eids:
                if left == right:
                    continue
                co_counts[left][right] += 1
                co_videos[(left, right)].add(video_id)
    # Wider local-context co-mentions.
    for eid, rows in evidence_by_entity.items():
        for row in rows:
            ctx = norm(row.get("context", ""))
            for other, pat in patterns.items():
                if other != eid and pat.search(ctx):
                    co_counts[eid][other] += 2
                    co_videos[(eid, other)].add(row["video_id"])

    stage("Guest appearances", 7, total_stages, overall_started)
    appearances: dict[str, list[dict]] = defaultdict(list)
    for eid, rows in evidence_by_entity.items():
        own_names = {norm(entities[eid]["canonical_name"]), *(norm(x) for x in entities[eid]["aliases"])}
        for row in rows:
            source_owned = norm(row["source"]) in own_names
            title_hit = row["evidence_type"] == "title_identity_match"
            intro_hit = bool(APPEARANCE_RE.search(row.get("context", "")))
            if not source_owned and (title_hit or intro_hit):
                appearances[eid].append({
                    "appearance_id": "appearance-" + row["evidence_id"], "video_id": row["video_id"],
                    "host_channel": row["source"], "title": row["title"], "published": row["published"],
                    "timestamp_seconds": row["timestamp_seconds"], "timestamp_url": row["timestamp_url"],
                    "role": "guest_or_subject", "status": "probable" if intro_hit else "candidate",
                    "evidence_ids": [row["evidence_id"]]
                })

    stage("Relationship history", 8, total_stages, overall_started)
    relationships: dict[str, list[dict]] = defaultdict(list)
    for eid, counter in co_counts.items():
        for other, count in counter.most_common(50):
            if count < 2:
                continue
            relationships[eid].append({
                "entity_id": other, "name": entities.get(other, {}).get("canonical_name", other),
                "relationship_type": "co_discussed_or_co_appeared", "interaction_score": count,
                "shared_video_count": len(co_videos[(eid, other)]), "status": "candidate",
                "first_seen": None, "last_seen": None
            })

    stage("Comic and campaign history", 9, total_stages, overall_started)
    comic_history: dict[str, list[dict]] = defaultdict(list)
    for eid, rows in evidence_by_entity.items():
        for row in rows:
            if COMIC_RE.search(row["context"] + " " + row["title"]):
                comic_history[eid].append({
                    "history_id": "comic-" + row["evidence_id"], "published": row["published"],
                    "summary": row["context"], "source": row["source"], "video_id": row["video_id"],
                    "timestamp_url": row["timestamp_url"], "status": "candidate", "evidence_ids": [row["evidence_id"]]
                })

    stage("Behavioural fingerprint", 10, total_stages, overall_started)
    behaviour: dict[str, dict] = {}
    for eid, rows in evidence_by_entity.items():
        joined = " ".join(row["context"] for row in rows)
        metrics = {name: len(pattern.findall(joined)) for name, pattern in BEHAVIOUR_TERMS.items()}
        topic_counts = Counter({term: len(re.findall(r"(?<!\w)" + re.escape(term) + r"(?!\w)", joined, flags=re.I)) for term in TOPIC_TERMS})
        behaviour[eid] = {
            "status": "provisional_corpus_derived", "observed_context_count": len(rows),
            "language_signals": metrics,
            "typical_topics": [{"term": t, "count": c} for t, c in topic_counts.most_common(15) if c],
            "notes": ["Counts describe language around mentions, not necessarily words spoken by the entity unless speaker attribution is available."]
        }

    stage("Catchphrases and recurring terminology", 11, total_stages, overall_started)
    terminology: dict[str, list[dict]] = defaultdict(list)
    for eid, rows in evidence_by_entity.items():
        ngrams = Counter()
        for row in rows:
            toks = words(row["context"])
            for size in (2, 3, 4):
                for i in range(max(0, len(toks) - size + 1)):
                    phrase = " ".join(toks[i:i+size])
                    if len(phrase) >= 8:
                        ngrams[phrase] += 1
        terminology[eid] = [{"phrase": p, "observation_count": c, "status": "candidate"} for p, c in ngrams.most_common(40) if c >= 2]

    stage("Quote bank", 12, total_stages, overall_started)
    quotes: dict[str, list[dict]] = defaultdict(list)
    for eid, rows in evidence_by_entity.items():
        ranked = sorted(rows, key=lambda r: (len(r["context"]), r.get("confidence", 0)), reverse=True)
        for row in ranked[:100]:
            quotes[eid].append({
                "quote_id": "quote-" + row["evidence_id"], "text": row["context"], "source": row["source"],
                "video_id": row["video_id"], "published": row["published"], "timestamp_seconds": row["timestamp_seconds"],
                "timestamp_url": row["timestamp_url"], "status": "context_quote_candidate",
                "speaker_attribution": "unknown", "evidence_ids": [row["evidence_id"]]
            })

    stage("Claims and allegations", 13, total_stages, overall_started)
    claims: dict[str, list[dict]] = defaultdict(list)
    for eid, rows in evidence_by_entity.items():
        for row in rows:
            if CLAIM_RE.search(row["context"]):
                claims[eid].append({
                    "claim_id": "claim-" + row["evidence_id"], "text": row["context"],
                    "claimant": row["source"], "subject_entity_id": eid, "published": row["published"],
                    "status": "unverified_candidate", "timestamp_url": row["timestamp_url"],
                    "evidence_ids": [row["evidence_id"]]
                })

    stage("Communities and affiliations", 14, total_stages, overall_started)
    communities: dict[str, list[dict]] = defaultdict(list)
    for eid, rows in evidence_by_entity.items():
        joined = " ".join(r["context"] + " " + r["title"] for r in rows)
        for term in COMMUNITY_TERMS:
            count = len(re.findall(r"(?<!\w)" + re.escape(term) + r"(?!\w)", joined, flags=re.I))
            if count:
                communities[eid].append({"name": term, "observation_count": count, "relationship": "discussed_near_entity", "status": "candidate"})

    stage("Story and narrative participation", 15, total_stages, overall_started)
    story_rows: dict[str, list[dict]] = defaultdict(list)
    for eid, rows in evidence_by_entity.items():
        title_counts = Counter(title_series_key(r["title"]) for r in rows if r.get("title"))
        topic_counts = Counter()
        for row in rows:
            for term in TOPIC_TERMS:
                if re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", row["context"] + " " + row["title"], flags=re.I):
                    topic_counts[term] += 1
        for name, count in topic_counts.most_common(15):
            story_rows[eid].append({"story_or_narrative": name, "observation_count": count, "status": "topic_cluster_candidate"})
        for title, count in title_counts.most_common(10):
            if title and count >= 2:
                story_rows[eid].append({"story_or_narrative": title, "observation_count": count, "status": "recurring_title_cluster"})

    stage("Narrative language and positioning", 16, total_stages, overall_started)
    narrative: dict[str, dict] = {}
    for eid, rows in evidence_by_entity.items():
        joined = " ".join(r["context"] for r in rows)
        narrative[eid] = {
            "positive_language_count": len(BEHAVIOUR_TERMS["praise"].findall(joined)),
            "negative_language_count": len(BEHAVIOUR_TERMS["criticism"].findall(joined)),
            "conflict_language_count": len(BEHAVIOUR_TERMS["conflict"].findall(joined)),
            "status": "rough_context_signal_not_sentiment_classification"
        }

    stage("Influence and power assessment", 17, total_stages, overall_started)
    influence: dict[str, dict] = {}
    for eid in entities:
        ev = evidence_by_entity.get(eid, [])
        sources = {r["source"] for r in ev}
        videos = {r["video_id"] for r in ev}
        activity = owned_radar.get(eid, [])
        associates = relationships.get(eid, [])
        appearances_n = len(appearances.get(eid, []))
        reach = min(100, len(sources) * 9 + len(videos) * 1.2)
        output = min(100, len(activity) * 2.5)
        network = min(100, sum(x["shared_video_count"] for x in associates) * 4 + len(associates) * 3)
        visibility = min(100, len(ev) * 1.5 + appearances_n * 6)
        score = round((reach * .30) + (output * .25) + (network * .25) + (visibility * .20))
        influence[eid] = {
            "power_level": score, "band": "high" if score >= 70 else "medium" if score >= 35 else "low",
            "components": {"reach": round(reach), "content_output": round(output), "network": round(network), "visibility": round(visibility)},
            "status": "provisional_explainable_corpus_assessment"
        }

    stage("Threat assessment", 18, total_stages, overall_started)
    threat: dict[str, dict] = {}
    for eid, power in influence.items():
        conflict = behaviour.get(eid, {}).get("language_signals", {}).get("conflict", 0)
        evidence_language = behaviour.get(eid, {}).get("language_signals", {}).get("evidence_language", 0)
        editorial = min(100, power["power_level"] * .7 + conflict * 2 + evidence_language)
        commercial = min(100, power["components"]["reach"] * .5 + power["components"]["network"] * .3)
        legal = min(100, len([c for c in claims.get(eid, []) if re.search(r"lawsuit|legal|court|lawyer", c["text"], re.I)]) * 10)
        overall = round(editorial * .65 + commercial * .25 + legal * .10)
        threat[eid] = {
            "overall_score": overall, "overall": "high" if overall >= 70 else "medium" if overall >= 35 else "low",
            "targets": {"editorial_or_reputational": round(editorial), "commercial": round(commercial), "legal": round(legal)},
            "definition": "Potential capacity to affect stories, reputation, audience attention or commercial outcomes; not physical danger.",
            "status": "provisional_explainable_nonphysical_assessment"
        }

    stage("Quality control and evidence validation", 19, total_stages, overall_started)
    qc = {"entities": {}, "warnings": [], "missing_transcripts": missing}
    for eid, info in entities.items():
        ev = evidence_by_entity.get(eid, [])
        qc["entities"][eid] = {
            "evidence_count": len(ev), "unique_videos": len({x["video_id"] for x in ev}),
            "unique_sources": len({x["source"] for x in ev}), "has_profile": (PEOPLE_ROOT / eid / "profile.json").exists(),
            "detail_sections_populated": sum(bool(x) for x in [links.get(eid), socials.get(eid), emails.get(eid), show_counts.get(eid), project_rows.get(eid), relationships.get(eid), appearances.get(eid), comic_history.get(eid), terminology.get(eid), claims.get(eid), communities.get(eid), story_rows.get(eid)])
        }
        if not ev:
            qc["warnings"].append(f"{info['canonical_name']}: no corpus identity evidence found")

    stage("Rebuild detailed profiles and reports", 20, total_stages, overall_started)
    summary_rows = []
    for eid, info in entities.items():
        folder = PEOPLE_ROOT / eid
        folder.mkdir(parents=True, exist_ok=True)
        ev = evidence_by_entity.get(eid, [])
        sources = sorted({x["source"] for x in ev})
        videos = sorted({x["video_id"] for x in ev})
        dates = sorted(str(x.get("published")) for x in ev if x.get("published"))
        old_profile = load(folder / "profile.json", {})
        profile = {
            **old_profile,
            "schema_version": "octopuss-deep-entity-profile-v1",
            "entity_id": eid,
            "canonical_name": info["canonical_name"],
            "entity_type": "person_or_persona",
            "aliases": info["aliases"],
            "nationality": old_profile.get("nationality"),
            "pronunciation": old_profile.get("pronunciation"),
            "description": old_profile.get("description"),
            "status": info.get("status", old_profile.get("status", "probable_person")),
            "first_seen": dates[0] if dates else old_profile.get("first_seen"),
            "last_seen": dates[-1] if dates else old_profile.get("last_seen"),
            "mention_evidence_count": len(ev),
            "mention_video_count": len(videos),
            "independent_source_count": len(sources),
            "mentioning_sources": sources,
            "profile_completeness": qc["entities"][eid]["detail_sections_populated"],
            "last_deep_build": NOW,
            "manual_notes": old_profile.get("manual_notes", []),
        }
        save(folder / "profile.json", profile)
        save(folder / "aliases.json", {"entity_id": eid, "updated_at": NOW, "aliases": [{"value": a, "status": "confirmed_or_seeded", "observations": alias_observations[eid][norm(a)]} for a in info["aliases"]]})
        save(folder / "channels.json", {"entity_id": eid, "updated_at": NOW, "channels": [{"name": n, "observation_count": c, "status": "confirmed_source"} for n, c in channels[eid].most_common()]})
        save(folder / "websites.json", {"entity_id": eid, "updated_at": NOW, "websites": sorted(links[eid].values(), key=lambda x: x["observation_count"], reverse=True)})
        save(folder / "socials.json", {"entity_id": eid, "updated_at": NOW, "social_accounts": sorted(socials[eid].values(), key=lambda x: x["observation_count"], reverse=True)})
        save(folder / "emails.json", {"entity_id": eid, "updated_at": NOW, "emails": sorted(emails[eid].values(), key=lambda x: x["observation_count"], reverse=True)})
        save(folder / "shows.json", {"entity_id": eid, "updated_at": NOW, "recurring_shows": [{"name": n, "observation_count": c, "status": "probable" if c >= 3 else "candidate"} for n, c in show_counts[eid].most_common(30) if n], "observed_schedule": dict(schedules[eid].most_common(20))})
        save(folder / "projects.json", {"entity_id": eid, "updated_at": NOW, "projects": project_rows[eid]})
        save(folder / "associates.json", {"entity_id": eid, "updated_at": NOW, "associates": relationships[eid]})
        save(folder / "appearances.json", {"entity_id": eid, "updated_at": NOW, "appearances": unique_dicts(appearances[eid], ("appearance_id",))})
        save(folder / "relationships.json", {"entity_id": eid, "updated_at": NOW, "relationships": relationships[eid]})
        save(folder / "comics.json", {"entity_id": eid, "updated_at": NOW, "comic_and_campaign_history": unique_dicts(comic_history[eid], ("history_id",))})
        save(folder / "behaviour.json", {"entity_id": eid, "updated_at": NOW, **behaviour.get(eid, {"status": "no_evidence"})})
        save(folder / "terminology.json", {"entity_id": eid, "updated_at": NOW, "recurring_terminology": terminology[eid]})
        save(folder / "quotes.json", {"entity_id": eid, "updated_at": NOW, "quotes": quotes[eid]})
        save(folder / "claims.json", {"entity_id": eid, "updated_at": NOW, "claims": unique_dicts(claims[eid], ("claim_id",))})
        save(folder / "communities.json", {"entity_id": eid, "updated_at": NOW, "communities": communities[eid]})
        save(folder / "stories.json", {"entity_id": eid, "updated_at": NOW, "stories_and_narratives": story_rows[eid], "narrative_signals": narrative.get(eid, {})})
        save(folder / "influence.json", {"entity_id": eid, "updated_at": NOW, **influence[eid]})
        save(folder / "threat.json", {"entity_id": eid, "updated_at": NOW, **threat[eid]})
        save(folder / "mentions.json", {"entity_id": eid, "updated_at": NOW, "count": len(ev), "mentions": ev})
        save(folder / "evidence.json", {"entity_id": eid, "updated_at": NOW, "count": len(ev), "evidence": ev})
        timeline = []
        for row in ev:
            timeline.append({"date": row.get("published"), "kind": "mention", "summary": f"{row['source']}: {row['context'][:240]}", "evidence_id": row["evidence_id"], "timestamp_url": row["timestamp_url"]})
        for item in owned_radar[eid]:
            timeline.append({"date": item.get("published"), "kind": "own_activity", "summary": item.get("title"), "url": item.get("url")})
        timeline.sort(key=lambda x: str(x.get("date") or ""), reverse=True)
        save(folder / "timeline.json", {"entity_id": eid, "updated_at": NOW, "events": timeline[:500]})
        save(folder / "activity.json", {"entity_id": eid, "updated_at": NOW, "activity": owned_radar[eid]})
        save(folder / "quality-control.json", {"entity_id": eid, "updated_at": NOW, **qc["entities"][eid]})
        history = load(folder / "history.json", {}).get("runs", [])
        history.append({"run_at": NOW, "mode": "deep_entity_build", "evidence_count": len(ev), "detail_sections_populated": qc["entities"][eid]["detail_sections_populated"]})
        save(folder / "history.json", {"entity_id": eid, "runs": history[-100:]})
        save(EVIDENCE_ROOT / f"{eid}.json", {"entity_id": eid, "updated_at": NOW, "evidence": ev})

        report_lines = [
            f"OCTOPUSS DEEP ENTITY REPORT — {info['canonical_name']}", "=" * 72, "",
            f"Identity evidence: {len(ev)}", f"Videos: {len(videos)}", f"Independent sources: {len(sources)}",
            f"Profile completeness sections: {qc['entities'][eid]['detail_sections_populated']}",
            f"Power level: {influence[eid]['power_level']} ({influence[eid]['band']})",
            f"Threat level: {threat[eid]['overall_score']} ({threat[eid]['overall']})", "",
            "TOP ASSOCIATES",
        ]
        report_lines += [f"- {x['name']}: score {x['interaction_score']} across {x['shared_video_count']} videos" for x in relationships[eid][:15]] or ["- None yet"]
        report_lines += ["", "SHOWS"] + ([f"- {n}: {c} observations" for n, c in show_counts[eid].most_common(10)] or ["- None yet"])
        report_lines += ["", "PROJECTS"] + ([f"- {x['name']}: {x['transcript_observations']} transcript / {x['radar_observations']} Radar observations" for x in project_rows[eid]] or ["- None yet"])
        report_lines += ["", "APPEARANCE CANDIDATES"] + ([f"- {x['host_channel']} — {x['title']} ({x['status']})" for x in appearances[eid][:15]] or ["- None yet"])
        report_lines += ["", "RECENT EVIDENCE"] + ([f"- {x['source']} @ {int(x['timestamp_seconds'])}s: {x['context'][:300]}" for x in ev[:15]] or ["- None"])
        report_path = REPORT_ROOT / f"{eid}.txt"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text("\n".join(report_lines), encoding="utf-8")
        summary_rows.append({"entity_id": eid, "canonical_name": info["canonical_name"], "evidence_count": len(ev), "sources": len(sources), "videos": len(videos), "completeness": qc["entities"][eid]["detail_sections_populated"], "power_level": influence[eid]["power_level"], "threat_level": threat[eid]["overall_score"]})

    save(INTEL / "deep-entity-build-summary.json", {
        "schema_version": "octopuss-deep-entity-build-v1", "updated_at": NOW,
        "duration_seconds": round(time.time() - overall_started, 2), "transcripts_opened": len(corpus),
        "missing_transcripts": missing, "entity_count": len(entities), "evidence_objects": sum(len(v) for v in evidence_by_entity.values()),
        "entities": sorted(summary_rows, key=lambda x: (x["completeness"], x["evidence_count"]), reverse=True),
        "quality_control": qc
    })

    duration = time.time() - overall_started
    print("\n" + "=" * 76)
    print("DEEP ENTITY BUILD COMPLETE")
    print(f"Entities rebuilt: {len(entities)}")
    print(f"Evidence objects: {sum(len(v) for v in evidence_by_entity.values())}")
    print(f"Elapsed: {duration:.1f} seconds")
    print(f"Summary: {INTEL / 'deep-entity-build-summary.json'}")
    print("=" * 76)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
