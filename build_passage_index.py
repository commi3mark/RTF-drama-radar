#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote

EXTRACTOR_VERSION = "3.0.0"
DEFAULT_REPO = "commi3mark/RTF-drama-radar"
DEFAULT_BRANCH = "main"

TRANSCRIPT_ROOT = Path("transcripts")
CONFIG_DIR = Path("config")
DATA_DIR = Path("data")
STATE_DIR = Path("state")
CACHE_DIR = Path("cache/passages")

OUTPUTS = {
    "entities": DATA_DIR / "entities.json",
    "occurrences": DATA_DIR / "entity-occurrences.json",
    "passages": DATA_DIR / "passage-index.json",
    "risk_signals": DATA_DIR / "risk-signals.json",
    "risk_profiles": DATA_DIR / "commi3-risk-profiles.json",
    "manifest": Path("transcript-manifest.json"),
    "state": STATE_DIR / "processing-state.json",
}

CLAIM_TERMS = [
    "said", "says", "told", "claims", "claimed", "alleged", "alleges",
    "accused", "denied", "admitted", "reported", "reports", "wrote",
    "posted", "tweeted", "replied", "threatened", "warned", "according to",
    "because", "lied", "lying", "true", "false", "did", "didn't", "was", "wasn't"
]

RISK_RULES = {
    "direct_hostility": {
        "severity": 1,
        "patterns": [
            r"\b(?:hate|despise|can't stand)\b",
            r"\b(?:idiot|moron|fraud|liar|loser|scumbag)\b",
        ],
    },
    "deplatforming": {
        "severity": 2,
        "patterns": [r"\b(?:report|mass report|flag|strike|ban|deplatform)\b"],
    },
    "privacy_signal": {
        "severity": 3,
        "patterns": [r"\b(?:address|home|house|workplace|phone number|real name|dox|doxx)\b"],
    },
    "threat_language": {
        "severity": 4,
        "patterns": [r"\b(?:hurt|attack|kill|shoot|stab|beat|smash|visit his house|come to your house)\b"],
    },
    "mobilisation": {
        "severity": 2,
        "patterns": [r"\b(?:everyone should|you all should|tell your followers|go after|dogpile)\b"],
    },
}

CAMPAIGN_PLATFORMS = ["indiegogo", "kickstarter", "fund my comic", "fundmycomic", "backerkit", "zoop"]
CAMPAIGN_ACTIONS = ["back", "backing", "launch", "launched", "closing", "funding", "funded", "stretch goal", "preorder", "pre-order", "signup", "mailing list"]


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


def build_windows(transcript: dict[str, Any], path: Path, alias_rules: dict[str, list[dict[str, Any]]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    segments = transcript.get("segments") or []
    occurrences = []
    segment_entities: dict[int, list[dict[str, Any]]] = {}

    for i, seg in enumerate(segments):
        if not isinstance(seg, dict):
            continue
        text = norm(str(seg.get("text") or ""))
        entities = detect_entities(text, alias_rules)
        segment_entities[i] = entities
        for item in entities:
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
                "start": float(seg.get("start") or 0),
                "timestamp": timestamp_text(float(seg.get("start") or 0)),
                "quote": text,
                "receipt_url": timestamp_url(transcript.get("url"), float(seg.get("start") or 0)),
                "transcript_file": path.as_posix(),
            })

    passages = []
    used_keys = set()

    for i, seg in enumerate(segments):
        if not isinstance(seg, dict):
            continue
        start = float(seg.get("start") or 0)

        # Build a +/- 20 second context window.
        indexes = []
        for j, candidate in enumerate(segments):
            if not isinstance(candidate, dict):
                continue
            candidate_start = float(candidate.get("start") or 0)
            if abs(candidate_start - start) <= 20:
                indexes.append(j)

        if not indexes:
            continue

        window_entities = {}
        for j in indexes:
            for item in segment_entities.get(j, []):
                window_entities[item["entity_id"]] = item

        if not window_entities:
            continue

        exact_text = norm(str(seg.get("text") or ""))
        before_text = " ".join(
            norm(str(segments[j].get("text") or ""))
            for j in indexes if j < i and isinstance(segments[j], dict)
        )
        after_text = " ".join(
            norm(str(segments[j].get("text") or ""))
            for j in indexes if j > i and isinstance(segments[j], dict)
        )
        full_context = norm(f"{before_text} {exact_text} {after_text}")

        claim_score = sum(1 for term in CLAIM_TERMS if term in full_context.lower())
        multi_person = len(window_entities) >= 2
        if not multi_person and claim_score == 0:
            continue

        entity_ids = sorted(window_entities)
        key = (int(start // 15), tuple(entity_ids))
        if key in used_keys:
            continue
        used_keys.add(key)

        passage_type = "multi_person_passage" if multi_person else "single_person_claim_passage"
        confidence = min(0.98, 0.55 + 0.08 * claim_score + 0.08 * len(entity_ids))

        passages.append({
            "passage_id": f"passage:{transcript.get('video_id')}:{int(start)}",
            "passage_type": passage_type,
            "claim_excerpt": exact_text,
            "context_before": before_text,
            "context_after": after_text,
            "full_context": full_context,
            "involved_entity_ids": entity_ids,
            "entity_matches": list(window_entities.values()),
            "claim_term_count": claim_score,
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
            "interpretation": {
                "speaker_entity_id": None,
                "attributed_speaker_entity_id": None,
                "recipient_entity_ids": [],
                "subject_entity_ids": entity_ids,
                "confidence": 0.0,
            },
            "confidence": round(confidence, 3),
        })

    return occurrences, passages


def build_risk(passages: list[dict[str, Any]], target: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    signals = []
    by_actor = defaultdict(list)

    for passage in passages:
        if target not in passage.get("involved_entity_ids", []):
            continue
        text = passage.get("full_context", "")
        other_entities = [eid for eid in passage.get("involved_entity_ids", []) if eid != target]
        for signal_type, rule in RISK_RULES.items():
            if not any(re.search(pattern, text, re.I) for pattern in rule["patterns"]):
                continue
            actor = other_entities[0] if len(other_entities) == 1 else None
            signal = {
                "risk_signal_id": f"risk-signal:{passage['passage_id']}:{signal_type}",
                "target_entity_id": target,
                "actor_entity_id": actor,
                "candidate_actor_entity_ids": other_entities,
                "signal_type": signal_type,
                "severity": rule["severity"],
                "passage_id": passage["passage_id"],
                "exact_excerpt": passage.get("claim_excerpt"),
                "full_context": text,
                "receipt_url": passage["source"].get("receipt_url"),
                "confidence": passage.get("confidence", 0.6),
                "review_status": "automatic_needs_review",
            }
            signals.append(signal)
            if actor:
                by_actor[actor].append(signal)

    profiles = []
    for actor, actor_signals in by_actor.items():
        score = min(100, sum(s["severity"] * 8 for s in actor_signals))
        label = "minimal" if score < 20 else "watch" if score < 40 else "concerning" if score < 60 else "elevated" if score < 80 else "severe"
        profiles.append({
            "entity_id": actor,
            "target_entity_id": target,
            "risk_label": label,
            "risk_score": score,
            "signal_count": len(actor_signals),
            "supporting_risk_signal_ids": [s["risk_signal_id"] for s in actor_signals],
            "last_updated_at": now_iso(),
            "review_status": "automatic_needs_review",
            "warning": "Interaction-risk sorting aid only. Not a finding that the person is dangerous.",
        })

    return signals, sorted(profiles, key=lambda x: -x["risk_score"])


def main() -> int:
    parser = argparse.ArgumentParser(description="Build passage-first Drama Radar indexes.")
    parser.add_argument("--transcripts", default=str(TRANSCRIPT_ROOT))
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--branch", default=DEFAULT_BRANCH)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    for directory in [DATA_DIR, STATE_DIR, CACHE_DIR]:
        directory.mkdir(parents=True, exist_ok=True)

    entities = load_entities()
    alias_rules = compile_aliases(entities)
    previous_state = load_json(OUTPUTS["state"], {})
    previous_files = previous_state.get("files", {})

    all_occurrences = []
    all_passages = []
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
                raise ValueError("Transcript root must be a JSON object")

            if use_cache:
                cached = load_json(cache_file, {})
                occurrences = cached.get("occurrences", [])
                passages = cached.get("passages", [])
                unchanged += 1
            else:
                occurrences, passages = build_windows(transcript, path, alias_rules)
                write_json(cache_file, {
                    "occurrences": occurrences,
                    "passages": passages,
                })
                changed += 1

            all_occurrences.extend(occurrences)
            all_passages.extend(passages)

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

    risk_signals, risk_profiles = build_risk(all_passages, "person:commi3-mark")

    write_json(OUTPUTS["entities"], {
        "generated_at": now_iso(),
        "schema_version": 3,
        "entities": entities,
    })
    write_json(OUTPUTS["occurrences"], {
        "generated_at": now_iso(),
        "schema_version": 3,
        "total": len(all_occurrences),
        "occurrences": all_occurrences,
    })
    write_json(OUTPUTS["passages"], {
        "generated_at": now_iso(),
        "schema_version": 3,
        "total": len(all_passages),
        "passages": all_passages,
    })
    write_json(OUTPUTS["risk_signals"], {
        "generated_at": now_iso(),
        "schema_version": 3,
        "risk_signals": risk_signals,
    })
    write_json(OUTPUTS["risk_profiles"], {
        "generated_at": now_iso(),
        "schema_version": 3,
        "risk_profiles": risk_profiles,
    })
    write_json(OUTPUTS["manifest"], sorted(manifest, key=lambda x: x.get("published") or "", reverse=True))
    write_json(OUTPUTS["state"], {
        "generated_at": now_iso(),
        "schema_version": 3,
        "extractor_version": EXTRACTOR_VERSION,
        "file_count": len(paths),
        "changed_files": changed,
        "unchanged_files": unchanged,
        "failed_files": failed,
        "files": state_files,
    })

    print("Drama Radar v3 passage build complete.")
    print(f"Transcript files: {len(paths)}")
    print(f"Changed/new: {changed}")
    print(f"Unchanged from cache: {unchanged}")
    print(f"Failed: {failed}")
    print(f"Entity occurrences: {len(all_occurrences)}")
    print(f"Searchable passages: {len(all_passages)}")
    print(f"Risk signals about Commi3 Mark: {len(risk_signals)}")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
