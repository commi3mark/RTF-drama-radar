import json
import os
from urllib.parse import quote

TRANSCRIPT_FOLDER = "transcripts"
ARCHIVE_FILE = "transcript-archive.json"
INDEX_FILE = "transcript-index.json"

RAW_BASE_URL = (
    "https://raw.githubusercontent.com/"
    "commi3mark/RTF-drama-radar/"
    "refs/heads/main/transcripts/"
)

archive = []
index = []

for filename in os.listdir(TRANSCRIPT_FOLDER):
    if not filename.lower().endswith(".json"):
        continue

    filepath = os.path.join(TRANSCRIPT_FOLDER, filename)

    try:
        with open(filepath, "r", encoding="utf-8") as file:
            transcript = json.load(file)

        relative_path = filepath.replace("\\", "/")
        transcript["transcript_file"] = relative_path
        archive.append(transcript)

        encoded_filename = quote(filename)

        index.append(
    {
        "video_id": transcript.get("video_id"),
        "title": transcript.get("title"),
        "source": transcript.get("source"),
        "published": transcript.get("published"),
        "url": transcript.get("url"),
        "language": transcript.get("language"),
        "is_generated": transcript.get("is_generated"),
        "segment_count": len(transcript.get("segments", [])),
        "transcript_file": relative_path,
        "raw_url": RAW_BASE_URL + encoded_filename,
        "plain_text": " ".join(
            segment.get("text", "")
            for segment in transcript.get("segments", [])
        ),
    }
)
        print("ADDED:", filename)

    except Exception as error:
        print("FAILED:", filename)
        print(type(error).__name__, str(error))

archive.sort(
    key=lambda item: item.get("published") or "",
    reverse=True,
)

index.sort(
    key=lambda item: item.get("published") or "",
    reverse=True,
)
with open(ARCHIVE_FILE, "w", encoding="utf-8") as file:
    json.dump(
        archive,
        file,
        indent=2,
        ensure_ascii=False,
    )

with open(INDEX_FILE, "w", encoding="utf-8") as file:
    json.dump(
        index,
        file,
        indent=2,
        ensure_ascii=False,
    )

print()
print("Finished.")
print("Transcripts added:", len(archive))
print("Archive created:", ARCHIVE_FILE)
print("Index created:", INDEX_FILE)
