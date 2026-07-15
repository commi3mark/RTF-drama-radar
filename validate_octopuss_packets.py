from __future__ import annotations

from radar_common import load_json, path_for


def main() -> int:
    transcript_index = load_json(
        path_for("transcript_index"),
        {"transcripts": []},
    )
    packet_root = path_for("octopuss_packets")
    packet_index = load_json(packet_root / "index.json", {"packets": []})

    transcript_ids = {
        str(row.get("video_id"))
        for row in transcript_index.get("transcripts", [])
        if row.get("video_id")
    }
    packet_ids = {
        str(row.get("video_id"))
        for row in packet_index.get("packets", [])
        if row.get("video_id")
    }

    missing = sorted(transcript_ids - packet_ids)
    orphaned = sorted(packet_ids - transcript_ids)
    invalid: list[str] = []

    for row in packet_index.get("packets", []):
        video_id = str(row.get("video_id") or "")
        json_path = packet_root / f"{video_id}.json"
        markdown_path = packet_root / f"{video_id}.md"

        if not json_path.exists() or not markdown_path.exists():
            invalid.append(video_id)
            continue

        packet = load_json(json_path, {})
        if (
            packet.get("video_id") != video_id
            or not isinstance(packet.get("blocks"), list)
            or packet.get("block_count") != len(packet.get("blocks", []))
        ):
            invalid.append(video_id)

    if missing or orphaned or invalid:
        print("OCTOPUSS packet validation failed.")
        if missing:
            print(f"Missing packets: {len(missing)}")
        if orphaned:
            print(f"Orphaned packets: {len(orphaned)}")
        if invalid:
            print(f"Invalid packets: {len(invalid)}")
        return 1

    print(
        "OCTOPUSS packet validation passed: "
        f"{len(packet_ids)} transcript packets."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
