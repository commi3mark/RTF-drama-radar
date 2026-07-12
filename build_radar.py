import json
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import feedparser


DAYS_TO_KEEP = 30

IGNORED_DOMAINS = [
    "comicsgate.org",
]


with open("sources.json", "r", encoding="utf-8") as file:
    sources = json.load(file)


cutoff_date = datetime.now(timezone.utc) - timedelta(days=DAYS_TO_KEEP)

radar = []


for source in sources:
    feed = feedparser.parse(source["feed"])

    for entry in feed.entries:
        published_struct = entry.get("published_parsed")

        if not published_struct:
            continue

        published_date = datetime(
            published_struct.tm_year,
            published_struct.tm_mon,
            published_struct.tm_mday,
            published_struct.tm_hour,
            published_struct.tm_min,
            published_struct.tm_sec,
            tzinfo=timezone.utc,
        )

        if published_date < cutoff_date:
            continue

        url = entry.get("link", "")
        domain = urlparse(url).netloc.lower()

        if any(
            domain == ignored_domain
            or domain.endswith("." + ignored_domain)
            for ignored_domain in IGNORED_DOMAINS
        ):
            continue

        radar.append(
            {
                "published": published_date.isoformat(),
                "source": source["name"],
                "platform": source["platform"],
                "title": entry.get("title", ""),
                "description": entry.get("summary", ""),
                "url": url,
            }
        )


radar.sort(key=lambda item: item["published"], reverse=True)


with open("drama-radar.json", "w", encoding="utf-8") as file:
    json.dump(radar, file, indent=2, ensure_ascii=False)


print(f"Built radar with {len(radar)} items from the last {DAYS_TO_KEEP} days.")
