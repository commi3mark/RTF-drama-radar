import json

from youtube_transcript_api import YouTubeTranscriptApi


with open("drama-radar.json", "r", encoding="utf-8") as file:
    radar = json.load(file)


for item in radar:
    if item.get("platform", "").lower() != "youtube":
        continue

    video_id = item.get("url", "").split("v=")[-1]

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
