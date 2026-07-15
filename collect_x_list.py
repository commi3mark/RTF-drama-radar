#!/usr/bin/env python3
"""Collect recent posts from an X List and store deduplicated daily JSON.

Required environment variables:
  X_BEARER_TOKEN  X API bearer token

Optional environment variables:
  X_LIST_ID       Numeric X List ID. Defaults to the Drama Radar list.

Outputs:
  twitter/lists/<list_id>/YYYY/MM/YYYY-MM-DD.json
  twitter/lists/<list_id>/latest.json
  twitter/lists/<list_id>/state.json
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

API_BASE = "https://api.x.com/2"
DEFAULT_LIST_ID = "1040297289561063424"
MAX_RESULTS = 100


def require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def api_get(path: str, token: str, params: dict[str, str]) -> dict[str, Any]:
    query = urllib.parse.urlencode(params)
    request = urllib.request.Request(
        f"{API_BASE}{path}?{query}",
        headers={
            "Authorization": f"Bearer {token}",
            "User-Agent": "RTF-Drama-Radar/1.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"X API returned HTTP {exc.code}: {body}") from exc


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def atomic_write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def index_by_id(items: list[dict[str, Any]] | None) -> dict[str, dict[str, Any]]:
    return {
        str(item["id"]): item
        for item in (items or [])
        if "id" in item
    }


def normalise_post(
    tweet: dict[str, Any],
    users: dict[str, dict[str, Any]],
    tweets: dict[str, dict[str, Any]],
    media: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    author = users.get(str(tweet.get("author_id")), {})
    username = author.get("username")

    references = tweet.get("referenced_tweets", []) or []
    reference_map = {
        ref.get("type"): str(ref.get("id"))
        for ref in references
        if ref.get("id")
    }

    post_type = "original"
    if "replied_to" in reference_map:
        post_type = "reply"
    elif "quoted" in reference_map:
        post_type = "quote"
    elif "retweeted" in reference_map:
        post_type = "repost"

    def referenced_post(kind: str) -> dict[str, Any] | None:
        ref_id = reference_map.get(kind)
        if not ref_id:
            return None

        ref = tweets.get(ref_id, {"id": ref_id})
        ref_author = users.get(str(ref.get("author_id")), {})
        ref_username = ref_author.get("username")

        return {
            "post_id": ref_id,
            "author": f"@{ref_username}" if ref_username else None,
            "text": ref.get("text"),
            "url": (
                f"https://x.com/{ref_username}/status/{ref_id}"
                if ref_username
                else f"https://x.com/i/status/{ref_id}"
            ),
        }

    attachments = tweet.get("attachments", {}) or {}
    media_items = [
        media[key]
        for key in attachments.get("media_keys", [])
        if key in media
    ]

    metrics = tweet.get("public_metrics", {}) or {}
    post_id = str(tweet["id"])

    return {
        "post_id": post_id,
        "author": f"@{username}" if username else None,
        "author_id": (
            str(tweet.get("author_id"))
            if tweet.get("author_id")
            else None
        ),
        "type": post_type,
        "text": tweet.get("text", ""),
        "url": (
            f"https://x.com/{username}/status/{post_id}"
            if username
            else f"https://x.com/i/status/{post_id}"
        ),
        "timestamp": tweet.get("created_at"),
        "conversation_id": (
            str(tweet.get("conversation_id"))
            if tweet.get("conversation_id")
            else None
        ),
        "in_reply_to": referenced_post("replied_to"),
        "quoted_post": referenced_post("quoted"),
        "reposted_post": referenced_post("retweeted"),
        "mentions": [
            f"@{item.get('username')}"
            for item in tweet.get("entities", {}).get("mentions", [])
            if item.get("username")
        ],
        "links": [
            item.get("expanded_url") or item.get("url")
            for item in tweet.get("entities", {}).get("urls", [])
            if item.get("expanded_url") or item.get("url")
        ],
        "media": media_items,
        "engagement": {
            "likes": metrics.get("like_count", 0),
            "reposts": metrics.get("retweet_count", 0),
            "quotes": metrics.get("quote_count", 0),
            "replies": metrics.get("reply_count", 0),
            "bookmarks": metrics.get("bookmark_count"),
            "impressions": metrics.get("impression_count"),
        },
        "collected_at": datetime.now(timezone.utc).isoformat(),
    }


def main() -> int:
    token = require_env("X_BEARER_TOKEN")
    list_id = os.getenv("X_LIST_ID", DEFAULT_LIST_ID).strip() or DEFAULT_LIST_ID

    root = Path("twitter") / "lists" / list_id
    state_path = root / "state.json"
    state = load_json(state_path, {})

    params = {
        "max_results": str(MAX_RESULTS),
        "tweet.fields": (
            "id,text,author_id,created_at,conversation_id,"
            "referenced_tweets,attachments,entities,public_metrics"
        ),
        "expansions": (
            "author_id,referenced_tweets.id,"
            "referenced_tweets.id.author_id,attachments.media_keys"
        ),
        "user.fields": "id,name,username",
        "media.fields": (
            "media_key,type,url,preview_image_url,width,height,alt_text"
        ),
    }

    if state.get("since_id"):
        params["since_id"] = str(state["since_id"])

    payload = api_get(f"/lists/{list_id}/tweets", token, params)
    data = payload.get("data", []) or []
    includes = payload.get("includes", {}) or {}

    users = index_by_id(includes.get("users"))
    referenced_tweets = index_by_id(includes.get("tweets"))
    media = {
        item["media_key"]: item
        for item in includes.get("media", []) or []
        if item.get("media_key")
    }

    collected = [
        normalise_post(tweet, users, referenced_tweets, media)
        for tweet in data
    ]

    if not collected:
        print("No new X List posts found.")
        return 0

    by_day: dict[str, list[dict[str, Any]]] = {}
    for post in collected:
        timestamp = post.get("timestamp") or datetime.now(timezone.utc).isoformat()
        day = timestamp[:10]
        by_day.setdefault(day, []).append(post)

    for day, posts in by_day.items():
        date = datetime.strptime(day, "%Y-%m-%d")
        daily_path = (
            root / f"{date:%Y}" / f"{date:%m}" / f"{day}.json"
        )

        existing = load_json(
            daily_path,
            {"list_id": list_id, "date": day, "activity": []},
        )

        merged = {
            item["post_id"]: item
            for item in existing.get("activity", [])
            if item.get("post_id")
        }
        merged.update({item["post_id"]: item for item in posts})

        existing["activity"] = sorted(
            merged.values(),
            key=lambda item: item.get("timestamp") or "",
        )
        existing["updated_at"] = datetime.now(timezone.utc).isoformat()
        atomic_write(daily_path, existing)

    latest_path = root / "latest.json"
    existing_latest = load_json(
        latest_path,
        {"list_id": list_id, "activity": []},
    )

    latest_merged = {
        item["post_id"]: item
        for item in existing_latest.get("activity", [])
        if item.get("post_id")
    }
    latest_merged.update({item["post_id"]: item for item in collected})

    latest_activity = sorted(
        latest_merged.values(),
        key=lambda item: item.get("timestamp") or "",
        reverse=True,
    )[:500]

    atomic_write(
        latest_path,
        {
            "list_id": list_id,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "activity": latest_activity,
        },
    )

    newest_id = max(int(item["post_id"]) for item in collected)
    previous_id = int(state.get("since_id", 0))

    atomic_write(
        state_path,
        {
            "list_id": list_id,
            "since_id": str(max(newest_id, previous_id)),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
    )

    print(f"Collected {len(collected)} new posts from X List {list_id}.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
