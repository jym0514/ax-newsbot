"""Bluesky 수집 — AT Protocol 공개 AppView(getAuthorFeed), 인증 불필요.

X 수집이 빠진 날(Mac OFF)의 공백을 메우는 보조 소스. 잘못된 핸들은 조용히 건너뛴다.
"""
from __future__ import annotations

import httpx

from ..config import Config
from ..models import Article
from ..util import HTTP_HEADERS, clean_text, parse_date, within_window

API = "https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed"


def _post_url(handle: str, uri: str) -> str:
    """at://did/app.bsky.feed.post/<rkey> → https://bsky.app/profile/<handle>/post/<rkey>"""
    rkey = uri.rsplit("/", 1)[-1] if uri else ""
    return f"https://bsky.app/profile/{handle}/post/{rkey}"


def collect(bs_config: dict, config: Config) -> list[Article]:
    accounts = bs_config.get("accounts", []) or []
    limit = int(bs_config.get("posts_per_account", 25))
    window = int(config.get("time_window_hours", 30))
    articles: list[Article] = []

    with httpx.Client(headers=HTTP_HEADERS, timeout=20, follow_redirects=True) as client:
        for handle in accounts:
            handle = str(handle).lstrip("@").strip()
            if not handle:
                continue
            try:
                r = client.get(API, params={"actor": handle, "limit": limit})
                r.raise_for_status()
                feed = r.json().get("feed", [])
            except Exception:  # noqa: BLE001 — 잘못된 핸들/일시 오류 → 건너뜀
                continue
            for item in feed:
                post = item.get("post", {})
                record = post.get("record", {})
                text = clean_text(record.get("text", ""), 500)
                if not text:
                    continue
                published = parse_date(record.get("createdAt"))
                if not within_window(published, window):
                    continue
                # 외부 링크가 있으면 그 기사 URL을, 없으면 게시물 URL을 쓴다.
                embed = post.get("embed", {}) or {}
                external = embed.get("external", {}) or {}
                url = external.get("uri") or _post_url(handle, post.get("uri", ""))
                articles.append(
                    Article(
                        title=(text.split("\n", 1)[0] or text)[:160],
                        url=url,
                        source=f"Bluesky @{handle}",
                        source_type="bluesky",
                        published_at=published,
                        raw_excerpt=text,
                        engagement=int(post.get("likeCount", 0)) + int(post.get("repostCount", 0)),
                        thumbnail=external.get("thumb", "") if isinstance(external.get("thumb"), str) else "",
                        source_weight=0.9,
                    )
                )
    return articles
