import json

from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import (
    NoTranscriptFound,
    TranscriptsDisabled,
    VideoUnavailable,
)


with open("drama-radar.json", "r", encoding="utf-8") as file:
    radar = json.load(file)


for item in radar:
    if item.get("platform", "").lower() != "youtube":
        continue

    url = item.get("url", "")
    video_id = url.split("v=")[-1].split("&")[0]

    try:
        transcript = YouTubeTranscriptApi().fetch(
            video_id,
            languages=["en", "en-GB", "en-US"],
        )

        print("SUCCESS")
        print("Title:", item.get("title"))
        print("Video ID:", video_id)
        print("Segments:", len(transcript))
        print("First line:", transcript[0].text)
        break

    except TranscriptsDisabled:
        print("SKIPPED - subtitles disabled:", item.get("title"))

    except NoTranscriptFound:
        print("SKIPPED - no English transcript:", item.get("title"))

    except VideoUnavailable:
        print("SKIPPED - video unavailable:", item.get("title"))

else:
    print("No usable transcript was found.")
