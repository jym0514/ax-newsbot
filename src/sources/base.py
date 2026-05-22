"""소스 수집 오케스트레이션 — RSS/Substack + Bluesky + X 캐시를 한데 모은다.

각 소스의 실패는 격리된다(한 곳이 죽어도 나머지는 계속). 통계 dict 를 함께 반환해
GitHub Actions Job Summary 에 소스별 수집량을 기록한다(실패는 -1).
"""
from __future__ import annotations

import logging

from ..config import Config
from ..models import Article
from . import bluesky_source, rss_source, x_cache_source

log = logging.getLogger("axnewsbot.sources")


def collect_all(config: Config, run_date: str) -> tuple[list[Article], dict[str, int]]:
    articles: list[Article] = []
    stats: dict[str, int] = {}

    # --- RSS / Substack ----------------------------------------------------
    for feed in config.sources.get("rss", []) or []:
        name = feed.get("name", feed.get("url", "?"))
        try:
            got = rss_source.collect_feed(feed, config)
            articles += got
            stats[name] = len(got)
            log.info("RSS [%s] %d건", name, len(got))
        except Exception as e:  # noqa: BLE001
            log.warning("RSS 실패 [%s]: %s", name, e)
            stats[name] = -1

    # --- Bluesky -----------------------------------------------------------
    bs = config.sources.get("bluesky", {}) or {}
    if bs.get("enabled"):
        try:
            got = bluesky_source.collect(bs, config)
            articles += got
            stats["Bluesky"] = len(got)
            log.info("Bluesky %d건", len(got))
        except Exception as e:  # noqa: BLE001
            log.warning("Bluesky 실패: %s", e)
            stats["Bluesky"] = -1

    # --- X 캐시 (Mac 수집기 결과) ------------------------------------------
    try:
        got = x_cache_source.collect(config, run_date)
        articles += got
        stats["X (Mac 캐시)"] = len(got)
        log.info("X 캐시 %d건", len(got))
    except Exception as e:  # noqa: BLE001
        log.warning("X 캐시 로드 실패: %s", e)
        stats["X (Mac 캐시)"] = -1

    return articles, stats
