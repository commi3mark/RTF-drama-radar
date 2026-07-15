import json
import os
import re
from collections import Counter
from urllib.parse import quote

TRANSCRIPT_FOLDER = "transcripts"
OUTPUT_FILE = "transcript-manifest.json"

RAW_REPO_BASE = (
    "https://raw.githubusercontent.com/"
    "commi3mark/RTF-drama-radar/"
    "refs/heads/main/"
)

# Words too common to be useful as navigation keywords.
STOPWORDS = {
    "about", "after", "again", "against", "also", "because", "been",
    "before", "being", "between", "both", "could", "does", "doing",
    "down", "during", "each", "even", "every", "from", "going",
    "have", "having", "here", "into", "just", "like", "more",
    "most", "much", "only", "other", "over", "really", "right",
    "said", "same", "should", "some", "than", "that", "their",
    "them", "then", "there", "these", "they", "thing", "think",
    "this", "those", "through", "very", "want", "what", "when",
    "where", "which", "while", "who", "will", "with", "would",
    "yeah", "your", "youre", "video", "stream", "show", "people",
    "gonna", "know", "okay", "well", "actually", "something",
}

MAX_KEYWORDS = 40


def normalise_spaces(text):
    return re.sub(r"\s+", " ", text or "").strip()


def extract_keywords(transcript):
    """
    Produce simple mechanical navigation keywords.

    This is not AI analysis. It favours repeated capitalised names,
    handles and recurring meaningful words.
    """
    title = transcript.get("title") or ""
    segments = transcript.get("segments") or []

    full_text = " ".join(
        segment.get("text", "")
        for segment in segments
    )

    combined_text = f"{title} {full_text}"

    candidates = []

    # Multi-word capitalised names such as "Liam Gray".
    name_pattern = re.compile(
        r"\b(?:[A-Z][A-Za-z0-9'’_-]+"
        r"(?:\s+[A-Z][A-Za-z0-9'’_-]+){1,3})\b"
    )

    candidates.extend(name_pattern.findall(combined_text))

    # Handles such as @Jon_Malin.
    candidates.extend(
        re.findall(r"@[A-Za-z0-9_]{2,}", combined_text)
    )

    # Repeated meaningful single words.
    words = re.findall(
        r"\b[A-Za-z][A-Za-z0-9'’_-]{3,}\b",
        combined_text,
    )

    word_counts = Counter(
        word
        for word in words
        if word.lower() not in STOPWORDS
    )

    for word, count in word_counts.most_common(100):
        if count >= 3:
            candidates.append(word)

    cleaned = []
    seen = set()

    for candidate in candidates:
        candidate = normalise_spaces(candidate).strip(".,:;!?()[]{}\"'")
        key = candidate.casefold()

        if len(candidate) < 3:
            continue

        if key in STOPWORDS:
            continue

        if key in seen:
            continue

        seen.add(key)
        cleaned.append(candidate)

        if len(cleaned) >= MAX_KEYWORDS:
            break

    return cleaned


manifest = []

for root, _, filenames in os.walk(TRANSCRIPT_FOLDER):
    for filename in filenames:
        if not filename.lower().endswith(".json"):
            continue

        filepath = os.path.join(root, filename)

        try:
            with open(filepath, "r", encoding="utf-8") as file:
                transcript = json.load(file)

            relative_path = filepath.replace("\\", "/")
            encoded_path = quote(relative_path, safe="/")

            manifest.append(
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
                    "raw_url": RAW_REPO_BASE + encoded_path,
                    "keywords": extract_keywords(transcript),
                }
            )

            print("ADDED:", relative_path)

        except Exception as error:
            print("FAILED:", filepath)
            print(type(error).__name__, str(error))


manifest.sort(
    key=lambda item: item.get("published") or "",
    reverse=True,
)

with open(OUTPUT_FILE, "w", encoding="utf-8") as file:
    json.dump(
        manifest,
        file,
        indent=2,
        ensure_ascii=False,
    )

print()
print("Finished.")
print("Transcripts indexed:", len(manifest))
print("Manifest created:", OUTPUT_FILE)
