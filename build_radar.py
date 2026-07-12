import json
import feedparser

with open("sources.json", "r", encoding="utf-8") as f:
    sources = json.load(f)

radar = []

for source in sources:
    feed = feedparser.parse(source["feed"])

    for entry in feed.entries:
        radar.append({
            "published": entry.get("published", ""),
            "source": source["name"],
            "platform": source["platform"],
            "title": entry.get("title", ""),
            "description": entry.get("summary", ""),
            "url": entry.get("link", "")
        })

radar.sort(key=lambda x: x["published"], reverse=True)

with open("drama-radar.json", "w", encoding="utf-8") as f:
    json.dump(radar, f, indent=2, ensure_ascii=False)

print(f"Built radar with {len(radar)} items.")
