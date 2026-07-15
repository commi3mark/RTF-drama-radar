from __future__ import annotations

import hashlib
from collections import Counter
from pathlib import Path
from typing import Any

from radar_common import load_json, save_json, path_for, settings, now_iso


def timestamp(seconds: float | int | None) -> str:
    total = max(0, int(float(seconds or 0)))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)

    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"

    return f"{minutes:02d}:{secs:02d}"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)

    return digest.hexdigest()


def join_text(segments: list[dict[str, Any]]) -> str:
    parts: list[str] = []

    for segment in segments:
        text = str(segment.get("text") or "").strip()
        if text:
            parts.append(text)

    return " ".join(parts)


def receipts_by_block(
    mention_receipts: dict[str, list[dict[str, Any]]],
    start: float,
    end: float,
) -> tuple[dict[str, int], list[dict[str, Any]]]:
    counts: Counter[str] = Counter()
    receipts: list[dict[str, Any]] = []

    for entity, rows in mention_receipts.items():
        for row in rows or []:
            receipt_start = float(row.get("start") or 0)

            if start <= receipt_start < end:
                counts[entity] += 1
                receipts.append(
                    {
                        "entity": entity,
                        "start": receipt_start,
                        "timestamp": row.get("timestamp") or timestamp(receipt_start),
                        "text": row.get("text"),
                    }
                )

    receipts.sort(key=lambda row: float(row.get("start") or 0))
    return dict(counts.most_common()), receipts


def make_blocks(
    segments: list[dict[str, Any]],
    mention_receipts: dict[str, list[dict[str, Any]]],
    block_seconds: int,
) -> list[dict[str, Any]]:
    if not segments:
        return []

    cleaned = sorted(
        (
            {
                "start": float(row.get("start") or 0),
                "duration": float(row.get("duration") or 0),
                "text": str(row.get("text") or "").strip(),
            }
            for row in segments
            if str(row.get("text") or "").strip()
        ),
        key=lambda row: row["start"],
    )

    if not cleaned:
        return []

    first_start = cleaned[0]["start"]
    last_end = max(row["start"] + row["duration"] for row in cleaned)

    blocks: list[dict[str, Any]] = []
    block_start = (int(first_start) // block_seconds) * block_seconds
    block_number = 1

    while block_start <= last_end:
        block_end = block_start + block_seconds
        block_segments = [
            row
            for row in cleaned
            if block_start <= row["start"] < block_end
        ]

        if block_segments:
            entity_counts, receipts = receipts_by_block(
                mention_receipts,
                block_start,
                block_end,
            )
            text = join_text(block_segments)
            actual_start = block_segments[0]["start"]
            actual_end = max(
                row["start"] + row["duration"]
                for row in block_segments
            )

            blocks.append(
                {
                    "block_number": block_number,
                    "window_start": block_start,
                    "window_end": block_end,
                    "actual_start": round(actual_start, 3),
                    "actual_end": round(actual_end, 3),
                    "timestamp_start": timestamp(actual_start),
                    "timestamp_end": timestamp(actual_end),
                    "segment_count": len(block_segments),
                    "character_count": len(text),
                    "entity_counts": entity_counts,
                    "mention_receipts": receipts,
                    "text": text,
                }
            )
            block_number += 1

        block_start = block_end

    return blocks


def packet_markdown(packet: dict[str, Any]) -> str:
    lines = [
        f"# OCTOPUSS Evidence Packet: {packet['title']}",
        "",
        f"- **Video ID:** `{packet['video_id']}`",
        f"- **Source:** {packet.get('source') or 'Unknown'}",
        f"- **Published:** {packet.get('published') or 'Unknown'}",
        f"- **Transcript SHA-256:** `{packet['transcript_sha256']}`",
        f"- **Generated:** {packet['generated_at']}",
        f"- **Blocks:** {packet['block_count']}",
        f"- **Transcript span:** {packet['coverage']['timestamp_start']}–{packet['coverage']['timestamp_end']}",
        f"- **Caption type:** {'auto-generated' if packet.get('is_generated') else 'manual/unknown'}",
        "",
        "## Interpretation guardrails",
        "",
        "- A name appearing in a block does **not** prove that person appeared on the show.",
        "- A played clip, chat message, quotation, or imitation may introduce voices that are not live participants.",
        "- Appearance, discussion, support, criticism, collaboration, and faction membership must be classified separately.",
        "- Exact speaker attribution is unknown unless the transcript explicitly identifies the speaker.",
        "",
        "## Transcript blocks",
        "",
    ]

    for block in packet["blocks"]:
        lines.extend(
            [
                f"### Block {block['block_number']}: "
                f"{block['timestamp_start']}–{block['timestamp_end']}",
                "",
            ]
        )

        if block["entity_counts"]:
            entity_text = ", ".join(
                f"{entity} ({count})"
                for entity, count in block["entity_counts"].items()
            )
            lines.append(f"**Detected entity receipts:** {entity_text}")
            lines.append("")

        lines.append(block["text"])
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    cfg = settings()
    block_minutes = int(cfg.get("octopuss_block_minutes", 12))
    block_seconds = max(60, block_minutes * 60)

    transcript_root = path_for("transcripts")
    packet_root = path_for("octopuss_packets")
    packet_root.mkdir(parents=True, exist_ok=True)

    index = load_json(path_for("transcript_index"), {"transcripts": []})
    packet_rows: list[dict[str, Any]] = []

    built = 0
    skipped = 0
    failed = 0

    for row in index.get("transcripts", []):
        relative_path = row.get("path")
        video_id = str(row.get("video_id") or "").strip()

        if not relative_path or not video_id:
            continue

        transcript_path = transcript_root.parent / str(relative_path)
        json_path = packet_root / f"{video_id}.json"
        markdown_path = packet_root / f"{video_id}.md"

        try:
            transcript_hash = sha256_file(transcript_path)
            previous = load_json(json_path, {})

            if (
                previous.get("transcript_sha256") == transcript_hash
                and json_path.exists()
                and markdown_path.exists()
            ):
                packet = previous
                skipped += 1
            else:
                transcript = load_json(transcript_path, {})
                segments = transcript.get("segments", [])
                mention_receipts = transcript.get("mention_receipts", {})
                blocks = make_blocks(
                    segments,
                    mention_receipts,
                    block_seconds,
                )

                if blocks:
                    first_start = blocks[0]["actual_start"]
                    last_end = blocks[-1]["actual_end"]
                else:
                    first_start = 0
                    last_end = 0

                packet = {
                    "schema_version": "octopuss-evidence-packet-v0.1",
                    "video_id": video_id,
                    "title": transcript.get("title") or row.get("title"),
                    "source": transcript.get("source") or row.get("source"),
                    "platform": transcript.get("platform") or "YouTube",
                    "published": transcript.get("published") or row.get("published"),
                    "url": transcript.get("url"),
                    "language": transcript.get("language"),
                    "is_generated": transcript.get("is_generated"),
                    "transcript_path": str(relative_path).replace("\\", "/"),
                    "transcript_sha256": transcript_hash,
                    "generated_at": now_iso(),
                    "block_minutes": block_minutes,
                    "block_count": len(blocks),
                    "segment_count": len(segments),
                    "coverage": {
                        "first_caption_second": round(float(first_start), 3),
                        "last_caption_second": round(float(last_end), 3),
                        "timestamp_start": timestamp(first_start),
                        "timestamp_end": timestamp(last_end),
                        "caption_span_seconds": round(
                            max(0.0, float(last_end) - float(first_start)),
                            3,
                        ),
                        "status": "transcript_present" if blocks else "empty",
                        "note": (
                            "This measures the caption span only. It does not "
                            "prove complete coverage of the original video."
                        ),
                    },
                    "global_mention_counts": transcript.get(
                        "mention_counts",
                        {},
                    ),
                    "blocks": blocks,
                }

                save_json(json_path, packet)
                markdown_path.write_text(
                    packet_markdown(packet),
                    encoding="utf-8",
                )
                built += 1

            packet_rows.append(
                {
                    "video_id": video_id,
                    "title": packet.get("title"),
                    "source": packet.get("source"),
                    "published": packet.get("published"),
                    "transcript_sha256": packet.get("transcript_sha256"),
                    "block_count": packet.get("block_count"),
                    "segment_count": packet.get("segment_count"),
                    "coverage": packet.get("coverage"),
                    "json_path": str(
                        json_path.relative_to(packet_root.parent.parent)
                    ).replace("\\", "/"),
                    "markdown_path": str(
                        markdown_path.relative_to(packet_root.parent.parent)
                    ).replace("\\", "/"),
                }
            )

        except Exception as exc:
            failed += 1
            print(f"PACKET WARNING: {transcript_path}: {exc}")

    packet_rows.sort(
        key=lambda item: item.get("published") or "",
        reverse=True,
    )

    save_json(
        packet_root / "index.json",
        {
            "schema_version": "octopuss-packet-index-v0.1",
            "generated_at": now_iso(),
            "block_minutes": block_minutes,
            "count": len(packet_rows),
            "packets": packet_rows,
        },
    )

    print(f"OCTOPUSS packet index contains {len(packet_rows)} transcripts.")
    print(f"Packets built or refreshed: {built}")
    print(f"Packets unchanged:          {skipped}")
    print(f"Packets failed:             {failed}")

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
