from __future__ import annotations

import subprocess
from urllib.parse import quote

from radar_common import ROOT, load_json, save_json, path_for, settings


def github_location() -> tuple[str, str] | None:
    """Return (owner/repository, branch) for raw GitHub links."""
    cfg = settings().get("git", {})
    configured_repo = str(cfg.get("repository", "")).strip()
    configured_branch = str(cfg.get("branch", "main")).strip() or "main"

    if configured_repo and "/" in configured_repo:
        return configured_repo, configured_branch

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
        ).strip() or configured_branch

        if remote.startswith("git@github.com:"):
            remote = "https://github.com/" + remote.split(":", 1)[1]

        if remote.endswith(".git"):
            remote = remote[:-4]

        if "github.com/" not in remote:
            return None

        owner_repo = remote.split("github.com/", 1)[1].strip("/")
        return owner_repo, branch

    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return None


def raw_url(owner_repo: str, branch: str, repository_path: str) -> str:
    encoded_path = quote(repository_path.replace("\\", "/"), safe="/")
    encoded_branch = quote(branch, safe="")
    return (
        f"https://raw.githubusercontent.com/{owner_repo}/"
        f"{encoded_branch}/{encoded_path}"
    )


def main() -> int:
    radar = load_json(path_for("radar"), [])
    index = load_json(path_for("transcript_index"), {"transcripts": []})

    location = github_location()
    owner_repo = location[0] if location else None
    branch = location[1] if location else None

    by_video: dict[str, dict] = {}

    for row in index.get("transcripts", []):
        video_id = row.get("video_id")
        if not video_id:
            continue

        transcript_path = str(row.get("path") or "")
        github_url = (
            raw_url(owner_repo, branch, transcript_path)
            if owner_repo and branch and transcript_path
            else None
        )

        row["github_url"] = github_url
        row["repository"] = owner_repo
        row["branch"] = branch
        by_video[str(video_id)] = row

    linked = 0

    for item in radar:
        video_id = item.get("youtube_id")

        if not video_id:
            item["transcript_status"] = "not_applicable"
            item["transcript_path"] = None
            item["transcript_url"] = None
            item["transcript"] = None
            continue

        row = by_video.get(str(video_id))

        if row:
            transcript_path = row["path"]
            github_url = row.get("github_url")

            item["transcript_status"] = "available"
            item["transcript_path"] = transcript_path
            item["transcript_url"] = github_url
            item["transcript"] = {
                "available": True,
                "local_path": transcript_path,
                "github_raw": github_url,
                "segment_count": row.get("segment_count"),
                "downloaded_at": row.get("downloaded_at"),
            }
            linked += 1

        elif item.get("transcript_status") != "permanent_failure":
            item["transcript_status"] = "pending"
            item["transcript_path"] = None
            item["transcript_url"] = None
            item["transcript"] = {
                "available": False,
                "local_path": None,
                "github_raw": None,
            }

    save_json(path_for("transcript_index"), index)
    save_json(path_for("radar"), radar)

    print(f"Linked {linked} Radar items to transcript files.")

    if location is None:
        print(
            "GitHub transcript URLs were not generated because no GitHub "
            "repository could be resolved. Local paths remain available."
        )
    else:
        print(
            f"Generated {linked} raw GitHub transcript URLs for "
            f"{owner_repo}@{branch}."
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
