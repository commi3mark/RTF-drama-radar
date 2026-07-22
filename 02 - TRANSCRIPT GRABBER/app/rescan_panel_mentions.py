from __future__ import annotations

import json
import os
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

GRABBER_ROOT = Path(__file__).resolve().parents[1]
SYSTEM_ROOT = GRABBER_ROOT.parent
TRANSCRIPTS_ROOT = GRABBER_ROOT / "transcripts"
OUTPUT_JSON = GRABBER_ROOT / "analysis" / "panel-mention-rescan.json"
OUTPUT_TXT = GRABBER_ROOT / "analysis" / "panel-mention-rescan.txt"

PROFILES = {
    "Commi3 Mark": {
        "strong": [
            r"\bcommi3\s*mark\b",
            r"\bcommie\s*mark\b",
            r"\bcomey\s*mark\b",
            r"\bcomi3\s*mark\b",
            r"\brussian\s+troll\s+factory\b",
        ],
        "weak": [],
    },
    "Piper": {
        "strong": [
            r"\bfyzzgiggidy\b",
            r"\bfizzgiggidy\b",
            r"\bfgiggidy\b",
            r"\bpipersbizarreadventure\b",
        ],
        "weak": [r"\bpiper\b", r"\bfyzz\b", r"\bfizz\b"],
    },
    "Stencil Artist Yan": {
        "strong": [
            r"\bstencil\s+artist\s+yan\b",
            r"\bstencilartistyan\b",
            r"\byansonly\b",
            r"\byan['’]?s\s+only\b",
        ],
        "weak": [r"\byan\b"],
    },
    "Billy Bacsko": {
        "strong": [
            r"\bbilly\s+bacsko\b",
            r"\bbilly\s+basco\b",
            r"\bbilly\s+backsko\b",
            r"\bbilly\s+backso\b",
            r"\bbillybacsko\b",
        ],
        "weak": [r"\bbilly\b"],
    },
    "Kenzo": {
        "strong": [
            r"\bart\s+by\s+kenzo\b",
            r"\bartbykenzo\b",
            r"\bkenzo\s+uk\b",
        ],
        "weak": [r"\bkenzo\b"],
    },
}

COMPILED = {
    person: {
        level: [(pattern, re.compile(pattern, re.IGNORECASE)) for pattern in patterns]
        for level, patterns in levels.items()
    }
    for person, levels in PROFILES.items()
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def timestamp(seconds) -> str:
    try:
        total = max(0, int(float(seconds)))
    except Exception:
        total = 0
    return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def raw_transcript_files() -> list[Path]:
    files = []
    for path in TRANSCRIPTS_ROOT.rglob("*.json"):
        if path.name.endswith(".intelligence.json"):
            continue
        if path.name in {"transcript-index.json", "transcript-manifest.json"}:
            continue
        files.append(path)
    return sorted(files)


def source_is_rtf(source: str) -> bool:
    value = source.casefold()
    return "commi3" in value or "commi" in value or "ком" in value


def context_for(segments: list[dict], index: int, radius: int = 2) -> str:
    start = max(0, index - radius)
    end = min(len(segments), index + radius + 1)
    return " ".join(
        str(segments[position].get("text") or "").strip()
        for position in range(start, end)
    ).strip()


def confidence(person: str, level: str, source: str, context: str) -> str:
    if level == "strong":
        return "high"
    lowered = context.casefold()
    if source_is_rtf(source):
        if person == "Billy Bacsko" and "billy tucci" in lowered:
            return "low"
        return "medium-high"
    if person == "Billy Bacsko":
        signals = ("bacsko", "basco", "backsko", "backso", "rtf", "troll factory")
        return "medium" if any(signal in lowered for signal in signals) else "low"
    if person == "Stencil Artist Yan":
        signals = ("stencil", "rtf", "troll factory", "piper", "kenzo")
        return "medium" if any(signal in lowered for signal in signals) else "low"
    if person == "Piper":
        signals = ("fyzz", "fizz", "rtf", "troll factory", "commi")
        return "medium" if any(signal in lowered for signal in signals) else "low"
    if person == "Kenzo":
        signals = ("art by", "rtf", "troll factory", "piper", "yan")
        return "medium" if any(signal in lowered for signal in signals) else "low"
    return "low"


def scan() -> dict:
    transcripts = raw_transcript_files()
    hits = []
    for path in transcripts:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        segments = data.get("segments") or []
        source = str(data.get("source") or "")
        video_id = str(data.get("video_id") or data.get("youtube_id") or "")
        for index, segment in enumerate(segments):
            text = str(segment.get("text") or "")
            if not text:
                continue
            for person, levels in COMPILED.items():
                matched = None
                for level in ("strong", "weak"):
                    for pattern, regex in levels[level]:
                        match = regex.search(text)
                        if match:
                            matched = (level, pattern, match.group(0))
                            break
                    if matched:
                        break
                if not matched:
                    continue
                level, pattern, matched_text = matched
                context = context_for(segments, index)
                hits.append(
                    {
                        "person": person,
                        "confidence": confidence(person, level, source, context),
                        "alias_strength": level,
                        "matched_text": matched_text,
                        "pattern": pattern,
                        "video_id": video_id,
                        "title": data.get("title"),
                        "source": source,
                        "published": data.get("published"),
                        "url": data.get("url")
                        or (
                            f"https://www.youtube.com/watch?v={video_id}"
                            if video_id
                            else None
                        ),
                        "timestamp": timestamp(segment.get("start")),
                        "start_seconds": segment.get("start"),
                        "segment_text": text,
                        "context": context,
                        "path": path.relative_to(SYSTEM_ROOT).as_posix(),
                    }
                )

    summary = {}
    for person in PROFILES:
        person_hits = [hit for hit in hits if hit["person"] == person]
        videos = {hit["video_id"] for hit in person_hits if hit["video_id"]}
        summary[person] = {
            "total_hits": len(person_hits),
            "distinct_transcripts": len(videos),
            "confidence_counts": dict(Counter(hit["confidence"] for hit in person_hits)),
            "source_counts": dict(Counter(hit["source"] for hit in person_hits)),
            "strong_alias_hits": sum(
                hit["alias_strength"] == "strong" for hit in person_hits
            ),
            "weak_alias_hits": sum(
                hit["alias_strength"] == "weak" for hit in person_hits
            ),
        }

    return {
        "generated_at": now_iso(),
        "transcripts_scanned": len(transcripts),
        "method": {
            "scope": "raw transcript JSON only; intelligence reports excluded",
            "unit": "caption segment",
            "context_radius_segments": 2,
            "caveat": (
                "Matches flag review candidates. Captions do not identify speakers, "
                "and short-name hits can refer to other people or played clips."
            ),
        },
        "profiles": PROFILES,
        "summary": summary,
        "hits": hits,
    }


def render(report: dict) -> str:
    lines = [
        "CORE PANEL MENTION RESCAN",
        "=" * 78,
        "",
        f"Generated: {report['generated_at']}",
        f"Raw transcripts scanned: {report['transcripts_scanned']}",
        "Derived intelligence reports excluded to prevent duplicate counting.",
        "",
        "SUMMARY",
        "-" * 78,
    ]
    for person, values in report["summary"].items():
        confidence_text = ", ".join(
            f"{level} {count}"
            for level, count in sorted(values["confidence_counts"].items())
        ) or "none"
        lines.append(
            f"{person}: {values['total_hits']} hit(s) across "
            f"{values['distinct_transcripts']} transcript(s); "
            f"strong aliases {values['strong_alias_hits']}, "
            f"short aliases {values['weak_alias_hits']}; {confidence_text}"
        )

    lines.extend(
        [
            "",
            "REVIEW RULES",
            "-" * 78,
            "- High: distinctive multiword handle or programme identity.",
            "- Medium-high: short name inside a Commi3/RTF-source transcript.",
            "- Medium: short name with nearby panel or identity context.",
            "- Low: ambiguous short name outside a confirming context.",
            "- A match is not proof of attendance, authorship, alliance, or speaker identity.",
        ]
    )

    grouped = defaultdict(list)
    for hit in report["hits"]:
        grouped[hit["person"]].append(hit)

    for person in PROFILES:
        lines.extend(["", person.upper(), "=" * 78])
        person_hits = grouped.get(person, [])
        by_video = defaultdict(list)
        for hit in person_hits:
            by_video[hit["video_id"]].append(hit)
        for video_id, video_hits in sorted(
            by_video.items(),
            key=lambda row: (
                str(row[1][0].get("published") or ""),
                str(row[1][0].get("title") or ""),
            ),
            reverse=True,
        ):
            first = video_hits[0]
            confidence_counts = Counter(hit["confidence"] for hit in video_hits)
            confidence_text = ", ".join(
                f"{key} {value}" for key, value in sorted(confidence_counts.items())
            )
            lines.extend(
                [
                    "",
                    f"{first.get('published') or 'date unknown'} | "
                    f"{first.get('source') or 'source unknown'}",
                    f"{first.get('title') or 'Untitled'} [{video_id}]",
                    f"{first.get('url') or ''}",
                    f"Hits: {len(video_hits)} ({confidence_text})",
                ]
            )
            for hit in video_hits:
                context = re.sub(r"\s+", " ", hit["context"]).strip()
                if len(context) > 500:
                    context = context[:497].rstrip() + "..."
                lines.append(
                    f"  [{hit['timestamp']}] {hit['confidence']} | "
                    f"{hit['matched_text']!r} | {context}"
                )
        if not person_hits:
            lines.extend(["", "No matches found."])

    lines.extend(
        [
            "",
            "ANALYTIC LIMITS",
            "-" * 78,
            "This is a discovery ledger, not a relationship or attendance ledger.",
            "Manual review is required before profile claims are updated.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    report = scan()
    atomic_write(
        OUTPUT_JSON,
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
    )
    atomic_write(OUTPUT_TXT, render(report))
    print(
        f"Scanned {report['transcripts_scanned']} transcripts and flagged "
        f"{len(report['hits'])} caption-segment matches."
    )
    for person, values in report["summary"].items():
        print(
            f"  {person}: {values['total_hits']} hits / "
            f"{values['distinct_transcripts']} transcripts"
        )
    print(f"Readable report: {OUTPUT_TXT}")
    print(f"Structured report: {OUTPUT_JSON}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
