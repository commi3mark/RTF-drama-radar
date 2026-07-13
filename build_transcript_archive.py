import json
import os
from urllib.parse import quote

TRANSCRIPT_FOLDER = "transcripts"
ARCHIVE_FILE = "transcript-archive.json"
INDEX_FILE = "transcript-index.json"

CHUNK_FOLDER = "transcript-index-chunks"
MANIFEST_FILE = os.path.join(CHUNK_FOLDER, "manifest.json")

MAX_CHUNK_BYTES = 250_000

RAW_REPO_BASE = (
    "https://raw.githubusercontent.com/"
    "commi3mark/RTF-drama-radar/"
    "refs/heads/main/"
)

RAW_TRANSCRIPT_BASE = RAW_REPO_BASE + "transcripts/"
RAW_CHUNK_BASE = RAW_REPO_BASE + CHUNK_FOLDER + "/"


def write_json(path, data):
    with open(path, "w", encoding="utf-8") as file:
        json.dump(
            data,
            file,
            indent=2,
            ensure_ascii=False,
        )


def json_size_bytes(data):
    return len(
        json.dumps(
            data,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
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

        plain_text = " ".join(
            segment.get("text", "")
            for segment in transcript.get("segments", [])
        )

        index.append(
            {
                "video_id": transcript.get("video_id"),
                "title": transcript.get("title"),
                "source": transcript.get("source"),
                "published": transcript.get("published"),
                "url": transcript.get("url"),
                "language": transcript.get("language"),
                "is_generated": transcript.get("is_generated"),
                "segment_count": len(
                    transcript.get("segments", [])
                ),
                "transcript_file": relative_path,
                "raw_url": RAW_TRANSCRIPT_BASE + encoded_filename,
                "plain_text": plain_text,
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


write_json(ARCHIVE_FILE, archive)
write_json(INDEX_FILE, index)


os.makedirs(CHUNK_FOLDER, exist_ok=True)

for existing_filename in os.listdir(CHUNK_FOLDER):
    if existing_filename.endswith(".json"):
        os.remove(
            os.path.join(
                CHUNK_FOLDER,
                existing_filename,
            )
        )


chunks = []
current_chunk = []

for item in index:
    test_chunk = current_chunk + [item]

    if (
        current_chunk
        and json_size_bytes(test_chunk) > MAX_CHUNK_BYTES
    ):
        chunks.append(current_chunk)
        current_chunk = [item]
    else:
        current_chunk = test_chunk

if current_chunk:
    chunks.append(current_chunk)


manifest = {
    "chunk_count": len(chunks),
    "total_transcripts": len(index),
    "max_chunk_bytes": MAX_CHUNK_BYTES,
    "chunks": [],
}


for chunk_number, chunk in enumerate(chunks, start=1):
    chunk_filename = f"chunk-{chunk_number:03d}.json"
    chunk_path = os.path.join(
        CHUNK_FOLDER,
        chunk_filename,
    )

    write_json(chunk_path, chunk)

    dates = [
        item.get("published")
        for item in chunk
        if item.get("published")
    ]

    sources = sorted(
        {
            item.get("source")
            for item in chunk
            if item.get("source")
        }
    )

    manifest["chunks"].append(
        {
            "file": f"{CHUNK_FOLDER}/{chunk_filename}",
            "raw_url": RAW_CHUNK_BASE + chunk_filename,
            "transcript_count": len(chunk),
            "size_bytes": os.path.getsize(chunk_path),
            "newest_published": max(dates) if dates else None,
            "oldest_published": min(dates) if dates else None,
            "sources": sources,
        }
    )

    print(
        "CREATED:",
        chunk_filename,
        "-",
        len(chunk),
        "transcripts",
        "-",
        os.path.getsize(chunk_path),
        "bytes",
    )


write_json(MANIFEST_FILE, manifest)


print()
print("Finished.")
print("Transcripts added:", len(archive))
print("Archive created:", ARCHIVE_FILE)
print("Index created:", INDEX_FILE)
print("Chunk folder:", CHUNK_FOLDER)
print("Chunks created:", len(chunks))
print("Manifest created:", MANIFEST_FILE)
