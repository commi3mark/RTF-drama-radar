from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
ENTITY_DIR = ROOT / "octopuss" / "entities" / "commi3-mark"
OUTPUT_DIR = ROOT / "octopuss" / "output"
SEED_PATH = ENTITY_DIR / "profile-seed.json"
RADAR_PATH = ROOT / "drama-radar.json"
INDEX_PATH = ROOT / "transcripts" / "transcript-index.json"
PROFILE_PATH = ENTITY_DIR / "profile.json"
MENTIONS_PATH = ENTITY_DIR / "mention-index.json"
CANDIDATES_PATH = ENTITY_DIR / "candidates.json"
EVIDENCE_PATH = ENTITY_DIR / "evidence.json"
HISTORY_PATH = ENTITY_DIR / "scan-history.json"
KNOWLEDGE_PATH = ENTITY_DIR / "trusted-knowledge.json"
REPORT_PATH = OUTPUT_DIR / "commi3-mark-report.txt"

MAX_PASSES = 6
CLUSTER_SECONDS = 20
URL_RE = re.compile(r"https?://[^\s<>\]\[\)\(\"']+", re.I)
EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
HANDLE_RE = re.compile(r"(?<!\w)@[A-Za-z0-9_]{2,30}\b")
COMIC_WORDS = re.compile(r"\b(comic|campaign|indiegogo|kickstarter|book|graphic novel|artist|writer|draw|issue|backer|fulfil)\w*\b", re.I)
APPEAR_WORDS = re.compile(r"\b(guest|joined by|welcome|on my channel|on the show|panel|stream with|appearance)\b", re.I)
ROLE_WORDS = re.compile(r"\b(host|creator|runs?|operates?|writer|artist|satirist|commentator|streamer|youtuber)\b", re.I)
NAMEISH_RE = re.compile(r"\b(?:[A-Z][A-Za-z0-9'_-]+(?:\s+[A-Z][A-Za-z0-9'_-]+){0,2}|[A-Z]{2,6})\b")
MARK_RE = re.compile(r"(?<!\w)(mark|commie|commi3|kami|kamie)(?!\w)", re.I)


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def stable_id(*parts: object) -> str:
    return hashlib.sha1("|".join(str(x) for x in parts).encode("utf-8")).hexdigest()[:20]


def normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text.casefold()).strip()


def youtube_time_url(video_id: str, seconds: float) -> str:
    return f"https://www.youtube.com/watch?v={video_id}&t={max(0, int(seconds))}s"


def context_for(segments: list[dict], index: int, radius: int = 5) -> tuple[str, float, float]:
    start = max(0, index - radius)
    end = min(len(segments), index + radius + 1)
    rows = segments[start:end]
    text = " ".join(str(row.get("text", "")).strip() for row in rows).strip()
    first = float(rows[0].get("start", 0) or 0) if rows else 0.0
    last = rows[-1] if rows else {}
    finish = float(last.get("start", 0) or 0) + float(last.get("duration", 0) or 0)
    return text, first, finish


def independent_videos(evidence_ids: list[str], evidence_by_id: dict[str, dict]) -> int:
    return len({evidence_by_id[e].get("video_id") for e in evidence_ids if e in evidence_by_id})


def independent_sources(evidence_ids: list[str], evidence_by_id: dict[str, dict]) -> int:
    return len({evidence_by_id[e].get("source") for e in evidence_ids if e in evidence_by_id and evidence_by_id[e].get("source")})


def source_record(kind: str, value: str, evidence_ids: list[str], status: str = "candidate", confidence: float = 0.5) -> dict:
    return {
        "type": kind,
        "value": value,
        "status": status,
        "confidence": round(confidence, 2),
        "evidence_ids": sorted(set(evidence_ids)),
    }


def compile_patterns(knowledge: dict) -> list[tuple[str, str, re.Pattern[str], float]]:
    rows = []
    for value in knowledge["direct_aliases"]:
        rows.append(("direct_alias", value, re.compile(r"(?<!\w)" + re.escape(value) + r"(?!\w)", re.I), 1.0))
    for value in knowledge["trusted_variants"]:
        rows.append(("trusted_variant", value, re.compile(r"(?<!\w)" + re.escape(value) + r"(?!\w)", re.I), 0.86))
    for value in knowledge["candidate_variants"]:
        rows.append(("candidate_variant", value, re.compile(r"(?<!\w)" + re.escape(value) + r"(?!\w)", re.I), 0.48))
    return rows


def scan_transcripts(transcripts: list[dict], knowledge: dict) -> dict:
    patterns = compile_patterns(knowledge)
    trusted_terms = sorted(set(knowledge["trusted_terms"]), key=len, reverse=True)
    trusted_associates = set(knowledge.get("trusted_associates", []))
    negative = {normalise(x) for x in knowledge.get("negative_matches", [])}

    evidence: list[dict] = []
    raw_mentions: list[dict] = []
    clue_evidence: dict[tuple[str, str], list[str]] = defaultdict(list)
    comic_evidence: list[str] = []
    appearance_evidence: list[str] = []
    missing_files: list[str] = []
    scanned = 0

    for meta in transcripts:
        path = ROOT / str(meta.get("path", ""))
        payload = load_json(path, None)
        if not isinstance(payload, dict):
            missing_files.append(str(meta.get("path", "")))
            continue
        scanned += 1
        segments = payload.get("segments", [])
        for i, segment in enumerate(segments):
            text = str(segment.get("text", ""))
            context, context_start, context_end = context_for(segments, i)
            norm_context = normalise(context)
            supporting_terms = [term for term in trusted_terms if normalise(term) in norm_context]
            supporting_associates = [name for name in trusted_associates if normalise(name) in norm_context]

            matches = [(kind, alias, base) for kind, alias, pattern, base in patterns if pattern.search(text)]
            # Later-pass indirect detection: weak name cue plus at least one strong trusted term,
            # or no name cue but two independently trusted identity cues in the same passage.
            if not matches:
                cue_count = len(set(supporting_terms)) + len(set(supporting_associates))
                if MARK_RE.search(context) and supporting_terms:
                    matches = [("contextual_reference", MARK_RE.search(context).group(0), 0.68)]
                elif cue_count >= 2 and any(normalise(x) in norm_context for x in ("his show", "your show", "his channel", "your channel", "the host")):
                    matches = [("indirect_reference", "profile cues", 0.62)]

            for match_type, alias, base_conf in matches:
                if any(bad in norm_context for bad in negative) and match_type in {"candidate_variant", "contextual_reference", "indirect_reference"}:
                    continue
                confidence = min(0.99, base_conf + min(0.18, 0.05 * len(set(supporting_terms)) + 0.03 * len(set(supporting_associates))))
                status = "confirmed" if match_type == "direct_alias" else "probable" if confidence >= 0.83 else "candidate"
                seconds = float(segment.get("start", 0) or 0)
                evidence_id = stable_id("mention", payload.get("video_id"), round(seconds, 1), alias, match_type)
                ev = {
                    "evidence_id": evidence_id,
                    "evidence_type": "transcript_passage",
                    "video_id": payload.get("video_id"),
                    "title": payload.get("title"),
                    "source": payload.get("source"),
                    "published": payload.get("published"),
                    "timestamp_seconds": seconds,
                    "timestamp_url": youtube_time_url(str(payload.get("video_id", "")), seconds),
                    "matched_text": text,
                    "context": context,
                    "context_start": context_start,
                    "context_end": context_end,
                    "pass_match_type": match_type,
                }
                evidence.append(ev)
                raw_mentions.append({
                    "mention_id": stable_id("commi3-mark", evidence_id),
                    "entity_id": "commi3-mark",
                    "match_type": match_type,
                    "matched_alias": alias,
                    "supporting_cues": sorted(set(supporting_terms + supporting_associates)),
                    "status": status,
                    "confidence": round(confidence, 2),
                    "evidence_id": evidence_id,
                    "video_id": payload.get("video_id"),
                    "source": payload.get("source"),
                    "title": payload.get("title"),
                    "published": payload.get("published"),
                    "timestamp_seconds": seconds,
                })

                if match_type in {"trusted_variant", "candidate_variant", "contextual_reference"} and alias.casefold() not in {"mark", "commie", "commi3"}:
                    clue_evidence[("transcription_variant", alias)].append(evidence_id)
                for term in supporting_terms:
                    clue_evidence[("associated_term", term)].append(evidence_id)
                for role in ROLE_WORDS.findall(context):
                    clue_evidence[("role_word", role.casefold())].append(evidence_id)
                for name in NAMEISH_RE.findall(context):
                    cleaned = name.strip(" .,:;!?\"'()[]")
                    n = normalise(cleaned)
                    if len(cleaned) > 2 and n not in {normalise(alias), "mark", "kami", "kamie", "youtube", "fbi"}:
                        clue_evidence[("associate", cleaned)].append(evidence_id)
                if COMIC_WORDS.search(context):
                    comic_evidence.append(evidence_id)
                if APPEAR_WORDS.search(context):
                    appearance_evidence.append(evidence_id)

    raw_mentions.sort(key=lambda r: (str(r.get("video_id")), float(r.get("timestamp_seconds", 0))))
    clustered: list[dict] = []
    for row in raw_mentions:
        if clustered and clustered[-1]["video_id"] == row["video_id"] and row["timestamp_seconds"] - clustered[-1]["last_timestamp_seconds"] <= CLUSTER_SECONDS:
            cluster = clustered[-1]
            cluster["last_timestamp_seconds"] = row["timestamp_seconds"]
            cluster["raw_hit_count"] += 1
            cluster["aliases"] = sorted(set(cluster["aliases"] + [row["matched_alias"]]))
            cluster["match_types"] = sorted(set(cluster["match_types"] + [row["match_type"]]))
            cluster["supporting_cues"] = sorted(set(cluster["supporting_cues"] + row["supporting_cues"]))
            cluster["evidence_ids"].append(row["evidence_id"])
            cluster["confidence"] = max(cluster["confidence"], row["confidence"])
            if row["status"] == "confirmed":
                cluster["status"] = "confirmed"
            elif row["status"] == "probable" and cluster["status"] == "candidate":
                cluster["status"] = "probable"
        else:
            clustered.append({
                "mention_cluster_id": stable_id("cluster", row["video_id"], int(row["timestamp_seconds"] // CLUSTER_SECONDS)),
                "entity_id": "commi3-mark",
                "video_id": row["video_id"],
                "source": row["source"],
                "title": row["title"],
                "published": row["published"],
                "first_timestamp_seconds": row["timestamp_seconds"],
                "last_timestamp_seconds": row["timestamp_seconds"],
                "timestamp_url": youtube_time_url(str(row["video_id"]), row["timestamp_seconds"]),
                "aliases": [row["matched_alias"]],
                "match_types": [row["match_type"]],
                "supporting_cues": row["supporting_cues"],
                "status": row["status"],
                "confidence": row["confidence"],
                "raw_hit_count": 1,
                "evidence_ids": [row["evidence_id"]],
            })

    return {
        "scanned": scanned,
        "missing_files": missing_files,
        "evidence": evidence,
        "clusters": clustered,
        "clue_evidence": clue_evidence,
        "comic_evidence": sorted(set(comic_evidence)),
        "appearance_evidence": sorted(set(appearance_evidence)),
    }


def promote_clues(knowledge: dict, scan: dict) -> tuple[dict, list[dict], list[dict]]:
    evidence_by_id = {e["evidence_id"]: e for e in scan["evidence"]}
    candidates = []
    promotions = []

    for (clue_type, value), ids in sorted(scan["clue_evidence"].items(), key=lambda x: (x[0][0], x[0][1].casefold())):
        ids = sorted(set(ids))
        videos = independent_videos(ids, evidence_by_id)
        sources = independent_sources(ids, evidence_by_id)
        status = "candidate"
        confidence = min(0.95, 0.35 + 0.12 * videos + 0.08 * sources)
        promote = False

        if clue_type == "transcription_variant":
            # Name variants need recurrence across videos, or two independent sources.
            promote = videos >= 3 or (videos >= 2 and sources >= 2)
        elif clue_type == "associate":
            # Associates can reinforce future references but never identify a person alone.
            promote = videos >= 3 and sources >= 2
        elif clue_type == "associated_term":
            promote = value in knowledge["trusted_terms"] or (videos >= 3 and sources >= 2)
        # Role words remain candidates; generic words are unsafe as detector knowledge.

        if promote:
            status = "promoted"
            if clue_type == "transcription_variant" and value not in knowledge["trusted_variants"]:
                knowledge["trusted_variants"].append(value)
                promotions.append({"type": clue_type, "value": value, "reason": f"{videos} videos / {sources} sources", "evidence_ids": ids})
            elif clue_type == "associate" and value not in knowledge["trusted_associates"]:
                knowledge["trusted_associates"].append(value)
                promotions.append({"type": clue_type, "value": value, "reason": f"{videos} videos / {sources} sources", "evidence_ids": ids})
            elif clue_type == "associated_term" and value not in knowledge["trusted_terms"]:
                knowledge["trusted_terms"].append(value)
                promotions.append({"type": clue_type, "value": value, "reason": f"{videos} videos / {sources} sources", "evidence_ids": ids})

        candidates.append({
            "clue_type": clue_type,
            "value": value,
            "status": status,
            "confidence": round(confidence, 2),
            "independent_videos": videos,
            "independent_sources": sources,
            "evidence_ids": ids,
        })

    for key in ("trusted_variants", "trusted_terms", "trusted_associates"):
        knowledge[key] = sorted(set(knowledge[key]), key=str.casefold)
    return knowledge, candidates, promotions


def collect_radar_profile(radar: list[dict]) -> dict:
    own = [item for item in radar if normalise(str(item.get("source", ""))) in {"commi3 mark", "commi3mark", "commie mark"}]
    urls: dict[str, list[str]] = defaultdict(list)
    emails: dict[str, list[str]] = defaultdict(list)
    handles: dict[str, list[str]] = defaultdict(list)
    for item in own:
        text = "\n".join(str(item.get(k, "")) for k in ("title", "description", "url"))
        rid = stable_id("radar", item.get("id"), item.get("url"))
        for url in URL_RE.findall(text):
            urls[url.rstrip(".,;:!?")].append(rid)
        for email in EMAIL_RE.findall(text):
            emails[email.casefold()].append(rid)
        for handle in HANDLE_RE.findall(text):
            handles[handle.casefold()].append(rid)

    social_domains = {
        "youtube.com": "YouTube", "youtu.be": "YouTube", "twitter.com": "X", "x.com": "X",
        "instagram.com": "Instagram", "facebook.com": "Facebook", "substack.com": "Substack",
        "rumble.com": "Rumble", "kick.com": "Kick", "discord.gg": "Discord", "discord.com": "Discord",
        "bsky.app": "Bluesky", "patreon.com": "Patreon", "ko-fi.com": "Ko-fi",
        "indiegogo.com": "Indiegogo", "kickstarter.com": "Kickstarter"
    }
    websites, socials = [], []
    for url, ids in sorted(urls.items()):
        host = urlparse(url).netloc.casefold().removeprefix("www.")
        platform = next((name for domain, name in social_domains.items() if host == domain or host.endswith("." + domain)), None)
        row = source_record("url", url, ids, "observed", 0.75)
        row["domain"] = host
        if platform:
            row["platform"] = platform
            socials.append(row)
        else:
            websites.append(row)

    show_examples: dict[str, set[str]] = defaultdict(set)
    for item in own:
        title = str(item.get("title", "")).strip()
        simplified = re.sub(r"[|:#\-–—].*$", "", title).strip()
        simplified = re.sub(r"\b(?:episode|ep\.?|#)\s*\d+.*$", "", simplified, flags=re.I).strip()
        if len(simplified) >= 4:
            show_examples[simplified].add(str(item.get("youtube_id") or item.get("url") or item.get("id", "")))
    shows = [{"name": name, "status": "candidate", "observed_episode_count": len(ids), "evidence_ids": sorted(ids)}
             for name, ids in sorted(show_examples.items(), key=lambda kv: (-len(kv[1]), kv[0].casefold())) if len(ids) >= 3]
    return {
        "own_items": own,
        "websites": websites,
        "socials": socials,
        "emails": [source_record("email", v, ids, "observed", 0.8) for v, ids in sorted(emails.items())],
        "handles": [source_record("handle", v, ids, "observed", 0.6) for v, ids in sorted(handles.items())],
        "shows": shows,
    }


def main() -> int:
    seed = load_json(SEED_PATH, {})
    radar = load_json(RADAR_PATH, [])
    index = load_json(INDEX_PATH, {"transcripts": []})
    transcripts = index.get("transcripts", []) if isinstance(index, dict) else []
    prior_history = load_json(HISTORY_PATH, {"runs": []})

    knowledge = {
        "direct_aliases": list(seed.get("direct_aliases", [])),
        "trusted_variants": list(seed.get("known_transcription_variants", [])),
        "candidate_variants": list(seed.get("possible_transcription_variants", [])),
        "trusted_terms": list(seed.get("known_terms", [])) + list(seed.get("known_projects", [])),
        "trusted_associates": [],
        "negative_matches": list(seed.get("negative_matches", [])),
    }
    # Preserve previously promoted knowledge across future runs.
    previous_knowledge = load_json(KNOWLEDGE_PATH, {})
    for key in ("trusted_variants", "trusted_terms", "trusted_associates"):
        knowledge[key] = sorted(set(knowledge[key] + list(previous_knowledge.get(key, []))), key=str.casefold)

    pass_history = []
    previous_cluster_ids: set[str] = set()
    final_scan = None
    final_candidates = []

    for pass_number in range(1, MAX_PASSES + 1):
        scan = scan_transcripts(transcripts, knowledge)
        cluster_ids = {r["mention_cluster_id"] for r in scan["clusters"]}
        new_clusters = sorted(cluster_ids - previous_cluster_ids)
        knowledge, candidates, promotions = promote_clues(knowledge, scan)
        pass_row = {
            "pass": pass_number,
            "mention_clusters": len(cluster_ids),
            "new_mentions_since_previous_pass": len(new_clusters),
            "new_mention_cluster_ids": new_clusters,
            "promotions": promotions,
            "trusted_variant_count": len(knowledge["trusted_variants"]),
            "trusted_term_count": len(knowledge["trusted_terms"]),
            "trusted_associate_count": len(knowledge["trusted_associates"]),
        }
        pass_history.append(pass_row)
        final_scan = scan
        final_candidates = candidates
        print(f"PASS {pass_number}: {len(cluster_ids)} mentions, {len(new_clusters)} new, {len(promotions)} knowledge promotions")
        if pass_number > 1 and not new_clusters and not promotions:
            pass_row["stopped_reason"] = "stable: no new mentions or trusted knowledge"
            break
        previous_cluster_ids = cluster_ids

    assert final_scan is not None
    radar_profile = collect_radar_profile(radar)
    clusters = final_scan["clusters"]
    evidence = final_scan["evidence"]
    mention_sources = Counter(r.get("source") for r in clusters)
    direct_count = sum(1 for r in clusters if r["status"] == "confirmed")
    probable_count = sum(1 for r in clusters if r["status"] == "probable")
    possible_count = sum(1 for r in clusters if r["status"] == "candidate")
    source_diversity = len({r.get("source") for r in clusters if r.get("source")})

    associates = [c for c in final_candidates if c["clue_type"] == "associate" and c["status"] in {"promoted", "candidate"}]
    recent_activity = len(radar_profile["own_items"])
    reach_score = min(20, source_diversity * 3)
    activity_score = min(20, recent_activity // 2)
    mention_score = min(25, direct_count * 4 + probable_count * 2)
    network_score = min(20, len([a for a in associates if a["status"] == "promoted"]) * 2)
    footprint_score = min(15, len(radar_profile["socials"]) + len(radar_profile["websites"]))
    power_score = min(100, reach_score + activity_score + mention_score + network_score + footprint_score)
    power_band = "low" if power_score < 25 else "developing" if power_score < 50 else "established" if power_score < 75 else "high"
    threat_components = {
        "editorial_visibility": min(25, mention_score),
        "network_amplification": min(25, network_score),
        "cross_platform_reach": min(20, footprint_score),
        "sustained_activity": min(20, activity_score),
        "evidence_capacity": min(10, 2 if not clusters else 6),
    }
    threat_score = sum(threat_components.values())
    threat_band = "low" if threat_score < 25 else "moderate" if threat_score < 50 else "significant" if threat_score < 75 else "high"

    profile = {
        "schema_version": "octopuss-entity-profile-v0.2-iterative",
        "generated_at": now_iso(),
        "entity_id": seed.get("entity_id"),
        "identity": {
            "canonical_name": seed.get("canonical_name"),
            "pronunciation": seed.get("pronunciation"),
            "entity_type": seed.get("entity_type", []),
            "nationality": seed.get("nationality", []),
            "status": seed.get("status", "unknown"),
        },
        "names": {
            "direct_aliases": knowledge["direct_aliases"],
            "trusted_transcription_variants": knowledge["trusted_variants"],
            "possible_transcription_variants": knowledge["candidate_variants"],
        },
        "trusted_detection_knowledge": {
            "terms": knowledge["trusted_terms"],
            "associates": knowledge["trusted_associates"],
            "rule": "Only promoted knowledge is reused in later passes. Candidate clues never train the detector.",
        },
        "online_presence": {
            "websites": radar_profile["websites"],
            "social_accounts": radar_profile["socials"],
            "public_emails": radar_profile["emails"],
            "handles": radar_profile["handles"],
        },
        "content": {
            "known_projects": seed.get("known_projects", []),
            "recurring_youtube_shows": radar_profile["shows"],
            "own_channel_detections": len(radar_profile["own_items"]),
        },
        "appearances": {"known_guest_appearances": [], "candidate_appearance_passages": final_scan["appearance_evidence"]},
        "comic_history": {"known_items": [], "candidate_passages": final_scan["comic_evidence"]},
        "associates": associates,
        "mentions": {
            "cluster_count": len(clusters),
            "confirmed": direct_count,
            "probable": probable_count,
            "possible": possible_count,
            "source_count": source_diversity,
            "top_sources": [{"source": name, "mentions": count} for name, count in mention_sources.most_common(10)],
        },
        "iteration": {
            "passes_run": len(pass_history),
            "stable": bool(pass_history and pass_history[-1].get("stopped_reason")),
            "passes": pass_history,
        },
        "assessments": {
            "power_level": {"score": power_score, "band": power_band, "confidence": 0.45, "components": {
                "independent_source_reach": reach_score, "current_content_activity": activity_score,
                "observed_mentions": mention_score, "trusted_network": network_score, "platform_footprint": footprint_score,
            }, "note": "Provisional evidence-based score from the current repository, not a personal judgement."},
            "threat_level": {"score": threat_score, "band": threat_band,
                "scope": "editorial, reputational and network capacity only; not physical danger", "confidence": 0.35,
                "components": threat_components,
                "note": "Provisional and intentionally conservative until behaviour and outcomes are modelled."},
        },
        "collection_status": {
            "radar_items_scanned": len(radar),
            "transcripts_in_index": len(transcripts),
            "transcripts_scanned": final_scan["scanned"],
            "missing_transcript_files": final_scan["missing_files"],
            "evidence_records": len(evidence),
            "candidate_clues": len(final_candidates),
        },
    }

    run_record = {"run_at": now_iso(), "passes": pass_history, "final_mentions": len(clusters),
                  "trusted_variants": knowledge["trusted_variants"], "trusted_associates": knowledge["trusted_associates"]}
    prior_history.setdefault("runs", []).append(run_record)
    prior_history["runs"] = prior_history["runs"][-50:]

    save_json(PROFILE_PATH, profile)
    save_json(MENTIONS_PATH, {"generated_at": now_iso(), "entity_id": "commi3-mark", "mention_clusters": clusters})
    save_json(CANDIDATES_PATH, {"generated_at": now_iso(), "entity_id": "commi3-mark", "candidates": final_candidates})
    save_json(EVIDENCE_PATH, {"generated_at": now_iso(), "entity_id": "commi3-mark", "evidence": evidence})
    save_json(KNOWLEDGE_PATH, {"generated_at": now_iso(), **knowledge})
    save_json(HISTORY_PATH, prior_history)

    lines = [
        "OCTOPUSS — COMMI3 MARK ITERATIVE PROFILE BUILD", "=" * 56,
        f"Passes run: {len(pass_history)}", f"Stable: {'yes' if profile['iteration']['stable'] else 'no'}",
        "", "PASS RESULTS",
    ]
    for row in pass_history:
        lines.append(f"- Pass {row['pass']}: {row['mention_clusters']} mentions; {row['new_mentions_since_previous_pass']} new; {len(row['promotions'])} promotions")
        for p in row["promotions"]:
            lines.append(f"    + {p['type']}: {p['value']} ({p['reason']})")
        if row.get("stopped_reason"):
            lines.append(f"    STOP: {row['stopped_reason']}")
    lines += [
        "", f"Radar items scanned: {len(radar)}", f"Transcripts indexed: {len(transcripts)}",
        f"Transcripts scanned: {final_scan['scanned']}", f"Missing transcript files: {len(final_scan['missing_files'])}",
        f"Mention clusters: {len(clusters)}", f"  Confirmed: {direct_count}", f"  Probable: {probable_count}",
        f"  Possible: {possible_count}", f"Independent mentioning sources: {source_diversity}",
        f"Trusted transcription variants: {len(knowledge['trusted_variants'])}",
        f"Trusted associate cues: {len(knowledge['trusted_associates'])}",
        f"Candidate clues awaiting review: {len(final_candidates)}",
        f"Power level: {power_score}/100 ({power_band})",
        f"Threat level: {threat_score}/100 ({threat_band}; editorial/reputational only)",
        "", "TOP MENTION CLUSTERS",
    ]
    for row in sorted(clusters, key=lambda r: str(r.get("published") or ""), reverse=True)[:30]:
        lines.append(f"- {row['source']} — {row['title']} @ {int(row['first_timestamp_seconds']//60)}:{int(row['first_timestamp_seconds']%60):02d} [{row['status']} {row['confidence']:.2f}]")
        lines.append(f"  {row['timestamp_url']}")
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n" + "\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
