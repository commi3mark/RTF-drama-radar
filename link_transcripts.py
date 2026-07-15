from __future__ import annotations

import subprocess

from radar_common import ROOT, load_json, save_json, path_for


def remote_base() -> str | None:
    """
    Return the raw GitHub base URL when this folder is a configured Git clone.

    During local-only testing, failure to resolve Git is expected and silent.
    """
    try:
        remote = subprocess.check_output(
            ["git", "remote", "get-url", "origin"],
            cwd=ROOT,
            text=True,
            encoding="utf-8",
            stderr=subprocess.DEVNULL,
        ).strip()

        branch = subprocess.check_output(
            ["git", "branch", "--show-current"],
            cwd=ROOT,
            text=True,
            encoding="utf-8",
            stderr=subprocess.DEVNULL,
        ).strip() or "main"

        if remote.startswith("git@github.com:"):
            remote = "https://github.com/" + remote.split(":", 1)[1]

        if remote.endswith(".git"):
            remote = remote[:-4]

        if "github.com/" not in remote:
            return None

        owner_repo = remote.split("github.com/", 1)[1]
        return f"https://raw.githubusercontent.com/{owner_repo}/{branch}/"

    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return None


def main() -> int:
    radar = load_json(path_for("radar"), [])
    index = load_json(path_for("transcript_index"), {"transcripts": []})

    by_video = {
        str(row.get("video_id")): row
        for row in index.get("transcripts", [])
        if row.get("video_id")
    }

    base = remote_base()
    linked = 0
    local_only = 0

    for item in radar:
        video_id = item.get("youtube_id")

        if not video_id:
            item["transcript_status"] = "not_applicable"
            item["transcript_path"] = None
            item["transcript_url"] = None
            continue

        row = by_video.get(str(video_id))

        if row:
            transcript_path = row["path"]
            item["transcript_status"] = "available"
            item["transcript_path"] = transcript_path
            item["transcript_url"] = (base + transcript_path) if base else None
            linked += 1

            if base is None:
                local_only += 1
        elif item.get("transcript_status") != "permanent_failure":
            item["transcript_status"] = "pending"
            item["transcript_path"] = None
            item["transcript_url"] = None

    save_json(path_for("radar"), radar)

    print(f"Linked {linked} Radar items to local transcript files.")

    if base is None:
        print(
            "GitHub transcript URLs not generated because this is not yet "
            "a configured Git repository. Local transcript paths remain available."
        )
    else:
        print(f"Published transcript URLs generated for {linked - local_only} items.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
