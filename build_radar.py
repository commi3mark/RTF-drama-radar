import html
import json
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, unquote, urlparse

import feedparser


DAYS_TO_KEEP = 30

IGNORED_DOMAINS = [
    "comicsgate.org",
]


def clean_text(value):
    """Remove simple HTML formatting while preserving the original wording."""
    if not value:
        return ""

    text = re.sub(r"<[^>]+>", "", value)
    text = html.unescape(text)
    return " ".join(text.split())


def get_real_url(url):
    """
    Google Alerts links often point to google.com/url?url=REAL_ADDRESS.
    Extract the real destination URL.
    """
    parsed_url = urlparse(url)

    google_domains = {
        "google.com",
        "www.google.com",
        "google.co.uk",
        "www.google.co.uk",
    }

    if parsed_url.netloc.lower() in google_domains and parsed_url.path == "/url":
        query = parse_qs(parsed_url.query)
        destination = query.get("url") or query.get("q")

        if destination:
            return unquote(destination[0])

    return url


def is_ignored_url(url):
    real_url = get_real_url(url)
    domain = urlparse(real_url).netloc.lower()

    if domain.startswith("www."):
        domain = domain[4:]

    return any(
        domain == ignored_domain
        or domain.endswith("." + ignored_domain)
        for ignored_domain in IGNORED_DOMAINS
    )


def get_description(entry):
    """Use the fullest text supplied by the feed."""
    summary = entry.get("summary", "")

    if summary:
        return clean_text(summary)

    content = entry.get("content", [])

    if content and isinstance(content, list):
        return clean_text(content[0].get("value", ""))

    return ""


with open("sources.json", "r", encoding="utf-8") as file:
    sources = json.load(file)


cutoff_date = datetime.now(timezone.utc) - timedelta(days=DAYS_TO_KEEP)

radar = []


for source in sources:
    feed = feedparser.parse(source["feed"])

    if feed.bozo:
        print(f"Warning: possible feed issue for {source['name']}: {feed.bozo_exception}")

    print(f"{source['name']}: found {len(feed.entries)} feed entries")

    for entry in feed.entries:
        published_struct = (
            entry.get("published_parsed")
            or entry.get("updated_parsed")
        )

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

        original_url = entry.get("link", "")

        if is_ignored_url(original_url):
            continue

        radar.append(
            {
                "published": published_date.isoformat(),
                "source": source["name"],
                "platform": source["platform"],
                "title": clean_text(entry.get("title", "")),
                "description": get_description(entry),
                "url": get_real_url(original_url),
            }
        )


radar.sort(key=lambda item: item["published"], reverse=True)


with open("drama-radar.json", "w", encoding="utf-8") as file:
    json.dump(radar, file, indent=2, ensure_ascii=False)


print(
    f"Built radar with {len(radar)} items "
    f"from the last {DAYS_TO_KEEP} days."
)
