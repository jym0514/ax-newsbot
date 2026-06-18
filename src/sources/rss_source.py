"""RSS/Atom 피드 수집 (feedparser).

httpx 로 직접 받아 feedparser 에 바이트를 넘긴다 — 기본 UA 차단 회피 + 타임아웃 제어.
"""
from __future__ import annotations

import feedparser
import httpx

from ..config import Config
from ..models import Article
from ..util import HTTP_HEADERS, clean_text, parse_date, within_window


def _extract_thumbnail(entry) -> str:
    """피드 항목에 이미 들어 있는 썸네일(media:content / media:thumbnail / enclosure)."""
    for key in ("media_content", "media_thumbnail"):
        media = entry.get(key)
        if media and isinstance(media, list) and media[0].get("url"):
            return media[0]["url"]
    for link in entry.get("links", []):
        if link.get("rel") == "enclosure" and str(link.get("type", "")).startswith("image"):
            return link.get("href", "")
    return ""


def _entry_excerpt(entry) -> str:
    if entry.get("summary"):
        return clean_text(entry["summary"], 700)
    content = entry.get("content")
    if content and isinstance(content, list):
        return clean_text(content[0].get("value", ""), 700)
    return ""


def collect_feed(feed: dict, config: Config) -> list[Article]:
    url = feed["url"]
    name = feed.get("name", url)
    weight = float(feed.get("weight", 1.0))
    kind = feed.get("kind", "rss")
    region = feed.get("region", "global")
    window = int(config.get("time_window_hours", 30))

    resp = httpx.get(url, headers=HTTP_HEADERS, timeout=20, follow_redirects=True)
    resp.raise_for_status()
    parsed = feedparser.parse(resp.content)

    articles: list[Article] = []
    for entry in parsed.entries:
        link = (entry.get("link") or "").strip()
        title = clean_text(entry.get("title", ""), 300)
        if not link or not title:
            continue
        published = parse_date(
            entry.get("published_parsed")
            or entry.get("updated_parsed")
            or entry.get("published")
            or entry.get("updated")
        )
        if not within_window(published, window):
            continue
        articles.append(
            Article(
                title=title,
                url=link,
                source=name,
                source_type=kind,
                published_at=published,
                raw_excerpt=_entry_excerpt(entry),
                thumbnail=_extract_thumbnail(entry),
                source_weight=weight,
                region=region,
            )
        )
    return articles
