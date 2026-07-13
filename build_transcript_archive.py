import json
import os

TRANSCRIPT_FOLDER = "transcripts"
OUTPUT_FILE = "transcript-archive.json"


archive = []

for filename in os.listdir(TRANSCRIPT_FOLDER):
    if not filename.lower().endswith(".json"):
        continue

    filepath = os.path.join(TRANSCRIPT_FOLDER, filename)

    try:
        with open(filepath, "r", encoding="utf-8") as file:
            transcript = json.load(file)

        transcript["transcript_file"] = filepath.replace("\\", "/")
        archive.append(transcript)

        print("ADDED:", filename)

    except Exception as error:
        print("FAILED:", filename)
        print(type(error).__name__, str(error))


archive.sort(
    key=lambda item: item.get("published", ""),
    reverse=True,
)


with open(OUTPUT_FILE, "w", encoding="utf-8") as file:
    json.dump(
        archive,
        file,
        indent=2,
        ensure_ascii=False,
    )


print()
print("Finished.")
print("Transcripts added:", len(archive))
print("Archive created:", OUTPUT_FILE)
