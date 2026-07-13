import json
import os
import random
import re
import time
from urllib.parse import parse_qs, urlparse

from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import (
    NoTranscriptFound,
    TranscriptsDisabled,
    VideoUnavailable,
)

TRANSCRIPT_FOLDER = "transcripts"

# Wait a random amount between YouTube requests.
MIN_DELAY_SECONDS = 8
MAX_DELAY_SECONDS = 20

# Optional safety limit. Set to None to process everything available.
MAX_NEW_TRANSCRIPTS_PER_RUN = 25


def safe_filename(name):
    """Convert a video title into a Windows-safe filename."""
    if not name:
        return "Untitled video"

    name = re.sub(r'[<>:"/\\|?*]', "", name)
    name = re.sub(r"\s+", " ", name).strip()

    # Leave space for the video ID and .json extension.
    return name[:180]


def get_video_id(url):
    """Extract a YouTube video ID from common YouTube URL formats."""
    parsed = urlparse(url)
    domain = parsed.netloc.lower()

    if domain.startswith("www."):
        domain = domain[4:]

    if domain == "youtu.be":
        return parsed.path.strip("/").split("/")[0]

    if domain in {
        "youtube.com",
        "m.youtube.com",
        "music.youtube.com",
    }:
        if parsed.path == "/watch":
            return parse_qs(parsed.query).get("v", [None])[0]

        parts = parsed.path.strip("/").split("/")

        if len(parts) >= 2 and parts[0] in {
            "live",
            "shorts",
            "embed",
        }:
            return parts[1]

    return None


def transcript_already_saved(video_id):
    """
    Detect transcripts saved using either the old filename format:

        VIDEO_ID.json

    or the new human-readable format:

        Video title [VIDEO_ID].json
    """
    old_filename = os.path.join(
        TRANSCRIPT_FOLDER,
        f"{video_id}.json",
    )

    if os.path.exists(old_filename):
        return True

    expected_ending = f"[{video_id}].json".lower()

    for filename in os.listdir(TRANSCRIPT_FOLDER):
        if filename.lower().endswith(expected_ending):
            return True

    return False


def wait_before_request():
    """Pause between YouTube requests to reduce rate limiting."""
    delay = random.uniform(
        MIN_DELAY_SECONDS,
        MAX_DELAY_SECONDS,
    )

    print(f"Waiting {delay:.1f} seconds before request...")
    time.sleep(delay)


with open("drama-radar.json", "r", encoding="utf-8") as file:
    radar = json.load(file)

os.makedirs(TRANSCRIPT_FOLDER, exist_ok=True)

saved = 0
skipped_existing = 0
skipped_unavailable = 0
failed = 0
blocked = False

for item in radar:
    if item.get("platform", "").lower() != "youtube":
        continue

    video_id = get_video_id(item.get("url", ""))

    if not video_id:
        print(
            "SKIPPED - could not find video ID:",
            item.get("title"),
        )
        failed += 1
        continue

    if transcript_already_saved(video_id):
        print(
            "SKIPPED - already saved:",
            item.get("title"),
        )
        skipped_existing += 1
        continue

    if (
        MAX_NEW_TRANSCRIPTS_PER_RUN is not None
        and saved >= MAX_NEW_TRANSCRIPTS_PER_RUN
    ):
        print()
        print(
            "Reached the safety limit of",
            MAX_NEW_TRANSCRIPTS_PER_RUN,
            "new transcripts.",
        )
        break

    wait_before_request()

    try:
        transcript = YouTubeTranscriptApi().fetch(
            video_id,
            languages=["en", "en-GB", "en-US"],
        )

        title = safe_filename(item.get("title"))

        filename = os.path.join(
            TRANSCRIPT_FOLDER,
            f"{title} [{video_id}].json",
        )

        data = {
            "video_id": video_id,
            "title": item.get("title"),
            "source": item.get("source"),
            "published": item.get("published"),
            "url": item.get("url"),
            "language": transcript.language_code,
            "is_generated": transcript.is_generated,
            "segments": [
                {
                    "start": round(segment.start, 3),
                    "duration": round(segment.duration, 3),
                    "text": segment.text,
                }
                for segment in transcript
            ],
        }

        # Each successful transcript is saved immediately.
        with open(filename, "w", encoding="utf-8") as outfile:
            json.dump(
                data,
                outfile,
                indent=2,
                ensure_ascii=False,
            )

        print("SAVED:", item.get("title"))
        print("FILE:", filename)
        saved += 1

    except TranscriptsDisabled:
        print(
            "SKIPPED - subtitles disabled:",
            item.get("title"),
        )
        skipped_unavailable += 1

    except NoTranscriptFound:
        print(
            "SKIPPED - no English transcript:",
            item.get("title"),
        )
        skipped_unavailable += 1

    except VideoUnavailable:
        print(
            "SKIPPED - video unavailable:",
            item.get("title"),
        )
        skipped_unavailable += 1

    except Exception as error:
        error_name = error.__class__.__name__

        if error_name in {
            "IpBlocked",
            "RequestBlocked",
        }:
            print()
            print("YOUTUBE TEMPORARILY BLOCKED THIS IP.")
            print("The script will stop instead of retrying repeatedly.")
            print("Run it again later to continue where it stopped.")
            print()
            print("Video:", item.get("title"))
            print("Error:", error_name)
            blocked = True
            break

        if error_name == "VideoUnplayable":
            print(
                "SKIPPED - video currently unplayable:",
                item.get("title"),
            )
            skipped_unavailable += 1
            continue

        print("FAILED:", item.get("title"))
        print(error_name, str(error))
        failed += 1

print()
print("Finished.")
print("Saved this run:", saved)
print("Already existed:", skipped_existing)
print("Unavailable:", skipped_unavailable)
print("Failed:", failed)
print("Stopped due to IP block:", blocked)
