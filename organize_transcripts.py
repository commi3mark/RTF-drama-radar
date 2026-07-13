import json
import os
import re
import shutil
from datetime import datetime

TRANSCRIPT_FOLDER = "transcripts"


def safe_filename(name):
    """Convert a title into a Windows-safe filename."""
    if not name:
        return "Untitled video"

    name = re.sub(r'[<>:"/\\|?*]', "", name)
    name = re.sub(r"\s+", " ", name).strip()

    return name[:180]


for filename in os.listdir(TRANSCRIPT_FOLDER):
    source_path = os.path.join(TRANSCRIPT_FOLDER, filename)

    # Ignore folders and non-JSON files.
    if not os.path.isfile(source_path):
        continue

    if not filename.lower().endswith(".json"):
        continue

    try:
        with open(source_path, "r", encoding="utf-8") as file:
            transcript = json.load(file)

        published = transcript.get("published")
        video_id = transcript.get("video_id")
        title = transcript.get("title")

        if not published:
            print("SKIPPED - missing date:", filename)
            continue

        if not video_id:
            print("SKIPPED - missing video ID:", filename)
            continue

        published_date = datetime.fromisoformat(
            published.replace("Z", "+00:00")
        )

        month_folder = published_date.strftime("%Y-%m")
        destination_folder = os.path.join(
            TRANSCRIPT_FOLDER,
            month_folder,
        )

        os.makedirs(destination_folder, exist_ok=True)

        new_filename = (
            f"{safe_filename(title)} [{video_id}].json"
        )

        destination_path = os.path.join(
            destination_folder,
            new_filename,
        )

        if os.path.exists(destination_path):
            print("SKIPPED - already exists:", destination_path)
            continue

        shutil.move(source_path, destination_path)

        print(
            "MOVED:",
            filename,
            "->",
            destination_path,
        )

    except Exception as error:
        print("FAILED:", filename)
        print(type(error).__name__, str(error))


print()
print("Finished organizing transcripts.")
