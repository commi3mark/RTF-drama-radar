import html
import json
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, unquote, urlparse
from urllib.request import Request, urlopen

import feedparser
import yt_dlp


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


def get_youtube_video_id(url):
    """Extract a YouTube video ID from common YouTube URL formats."""
    parsed_url = urlparse(url)
    domain = parsed_url.netloc.lower()

    if domain.startswith("www."):
        domain = domain[4:]

    if domain == "youtu.be":
        return parsed_url.path.strip("/").split("/")[0]

    if domain in {"youtube.com", "m.youtube.com", "music.youtube.com"}:
        if parsed_url.path == "/watch":
            query = parse_qs(parsed_url.query)
            video_ids = query.get("v")

            if video_ids:
                return video_ids[0]

        path_parts = parsed_url.path.strip("/").split("/")

        if (
            len(path_parts) >= 2
            and path_parts[0] in {"shorts", "live", "embed"}
        ):
            return path_parts[1]

    return None


def choose_caption_track(info):
    """
    Find the best available English caption track.

    Manual subtitles are preferred. Automatically generated captions
    are used when manual subtitles are unavailable.
    """
    language_preferences = [
        "en",
        "en-US",
        "en-GB",
        "en-orig",
    ]

    caption_sources = [
        ("manual", info.get("subtitles") or {}),
        ("automatic", info.get("automatic_captions") or {}),
    ]

    for caption_type, caption_data in caption_sources:
        for language_code in language_preferences:
            formats = caption_data.get(language_code, [])

            for caption_format in formats:
                if (
                    caption_format.get("ext") == "json3"
                    and caption_format.get("url")
                ):
                    return {
                        "caption_type": caption_type,
                        "language_code": language_code,
                        "format": "json3",
                        "url": caption_format["url"],
                    }

    # Some English caption codes contain extra suffixes.
    for caption_type, caption_data in caption_sources:
        for language_code, formats in caption_data.items():
            if not language_code.lower().startswith("en"):
                continue

            for caption_format in formats:
                if (
                    caption_format.get("ext") == "json3"
                    and caption_format.get("url")
                ):
                    return {
                        "caption_type": caption_type,
                        "language_code": language_code,
                        "format": "json3",
                        "url": caption_format["url"],
                    }

    return None


def download_json(url):
    """Download and decode a JSON document."""
    request = Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/150.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "en-GB,en;q=0.9",
        },
    )

    with urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def parse_json3_transcript(caption_data):
    """Convert YouTube JSON3 captions into timestamped transcript segments."""
    segments = []

    for event in caption_data.get("events", []):
        text_parts = []

        for segment in event.get("segs", []):
            text = segment.get("utf8", "")

            if text:
                text_parts.append(text)

        text = clean_text("".join(text_parts))

        if not text:
            continue

        start_ms = event.get("tStartMs", 0)
        duration_ms = event.get("dDurationMs", 0)

        segments.append(
            {
                "start": round(start_ms / 1000, 3),
                "duration": round(duration_ms / 1000, 3),
                "text": text,
            }
        )

    return segments


def short_error(error):
    """Keep transcript errors useful without filling the JSON with huge messages."""
    message = clean_text(str(error))

    if len(message) > 500:
        message = message[:500] + "..."

    return message


def get_youtube_transcript(video_url):
    """
    Retrieve English YouTube captions through yt-dlp.

    This records speech and timestamps only. It does not attempt to
    identify individual speakers.
    """
    video_id = get_youtube_video_id(video_url)

    if not video_id:
        return {
            "available": False,
            "reason": "video_id_not_found",
        }

    ydl_options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "socket_timeout": 30,
        "extractor_retries": 2,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_options) as ydl:
            info = ydl.extract_info(video_url, download=False)

        if not info:
            return {
                "available": False,
                "video_id": video_id,
                "reason": "video_information_not_found",
            }

        caption_track = choose_caption_track(info)

        if not caption_track:
            return {
                "available": False,
                "video_id": video_id,
                "reason": "english_captions_not_found",
            }

        caption_data = download_json(caption_track["url"])
        segments = parse_json3_transcript(caption_data)

        if not segments:
            return {
                "available": False,
                "video_id": video_id,
                "reason": "caption_file_was_empty",
            }

        full_text = " ".join(
            segment["text"]
            for segment in segments
            if segment["text"]
        )

        return {
            "available": True,
            "video_id": video_id,
            "language_code": caption_track["language_code"],
            "caption_type": caption_track["caption_type"],
            "is_generated": caption_track["caption_type"] == "automatic",
            "text": full_text,
            "segments": segments,
        }

    except Exception as error:
        return {
            "available": False,
            "video_id": video_id,
            "reason": error.__class__.__name__,
            "error": short_error(error),
        }


with open("sources.json", "r", encoding="utf-8") as file:
    sources = json.load(file)


cutoff_date = datetime.now(timezone.utc) - timedelta(days=DAYS_TO_KEEP)

radar = []


for source in sources:
    feed = feedparser.parse(source["feed"])

    if feed.bozo:
        print(
            f"Warning: possible feed issue for "
            f"{source['name']}: {feed.bozo_exception}"
        )

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

        real_url = get_real_url(original_url)

        radar_item = {
            "published": published_date.isoformat(),
            "source": source["name"],
            "platform": source["platform"],
            "title": clean_text(entry.get("title", "")),
            "description": get_description(entry),
            "url": real_url,
        }

        if source["platform"].lower() == "youtube":
            print(f"Fetching transcript with yt-dlp: {real_url}")
            radar_item["transcript"] = get_youtube_transcript(real_url)

        radar.append(radar_item)


radar.sort(key=lambda item: item["published"], reverse=True)


with open("drama-radar.json", "w", encoding="utf-8") as file:
    json.dump(radar, file, indent=2, ensure_ascii=False)


print(
    f"Built radar with {len(radar)} items "
    f"from the last {DAYS_TO_KEEP} days."
)
