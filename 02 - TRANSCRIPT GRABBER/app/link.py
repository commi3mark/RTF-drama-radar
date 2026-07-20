from __future__ import annotations
import subprocess, sys
from pathlib import Path
from urllib.parse import quote
GRABBER_ROOT = Path(__file__).resolve().parents[1]
SYSTEM_ROOT = GRABBER_ROOT.parent
RADAR_ROOT = SYSTEM_ROOT / "01 - DRAMA RADAR"
ROOT = SYSTEM_ROOT
sys.path.insert(0, str(RADAR_ROOT / "app"))
from common import RADAR_PATH, TRANSCRIPT_INDEX_PATH, load_json, save_json


def _repo():
    try:
        remote = subprocess.check_output(["git","remote","get-url","origin"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL).strip()
        branch = subprocess.check_output(["git","branch","--show-current"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL).strip() or "main"
        if remote.startswith("git@github.com:"): remote = "https://github.com/" + remote.split(":",1)[1]
        remote = remote.removesuffix(".git")
        return remote.split("github.com/",1)[1].strip("/"), branch
    except Exception:
        return None


def link() -> int:
    radar = load_json(RADAR_PATH, [])
    index = load_json(TRANSCRIPT_INDEX_PATH, {"transcripts": []})
    location = _repo()
    rows = {str(r["video_id"]): r for r in index.get("transcripts", []) if r.get("video_id")}
    linked = 0
    for item in radar:
        vid = item.get("youtube_id")
        if not vid: continue
        row = rows.get(str(vid))
        if row:
            path = row["path"]
            url = None
            remote_path = path.replace("02 - TRANSCRIPT GRABBER/transcripts/", "transcripts/archive/", 1)
            if location:
                owner_repo, branch = location
                url = f"https://raw.githubusercontent.com/{owner_repo}/{quote(branch,safe='')}/{quote(remote_path,safe='/')}"
            item.update({"transcript_status":"available", "transcript_path":path, "transcript_url":url,
                         "transcript":{"available":True,"local_path":path,"github_raw":url,
                                       "segment_count":row.get("segment_count"),"downloaded_at":row.get("downloaded_at")}})
            linked += 1
        elif item.get("transcript_status") not in {"unavailable", "permanent_failure"}:
            item["transcript_status"] = "pending"
    save_json(RADAR_PATH, radar)
    return linked

if __name__ == "__main__": print(f"Linked {link()} Radar items to transcript files.")
