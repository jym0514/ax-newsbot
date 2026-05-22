"""키워드 기반 필터 + 주제 분류 + 랭킹/선별.

키워드가 곧 필터다 — 어느 주제 키워드에도 걸리지 않는 기사는 다이제스트에서 빠진다.
주제 분류는 결정적(키워드 점수 최댓값, 동점 시 priority 가 낮은 = 더 구체적인 주제).
"""
from __future__ import annotations

import re
from datetime import timezone
from functools import lru_cache

from dateutil import parser as dateparser

from ..config import Config
from ..models import Article
from ..util import now_utc


@lru_cache(maxsize=4096)
def _kw_pattern(kw: str) -> "re.Pattern":
    # 영숫자 사이에 끼어든 경우는 제외(예: 'ai' 가 'rainy' 안에서 매칭되지 않도록)
    return re.compile(r"(?<![a-z0-9])" + re.escape(kw) + r"(?![a-z0-9])")


def _kw_in(kw: str, text: str) -> bool:
    """ASCII 키워드(ai, claude, gpt-5 ...)는 단어 경계로, 한글 키워드는 부분 문자열로 매칭."""
    if kw.isascii():
        return _kw_pattern(kw).search(text) is not None
    return kw in text


def _score_topic(article: Article, keywords: list[str]) -> tuple[int, list[str]]:
    """제목 매치는 3점, 본문 매치는 1점. ASCII 키워드는 단어 경계 기준."""
    title = article.title.lower()
    body = article.raw_excerpt.lower()
    score = 0
    matched: list[str] = []
    for kw in keywords:
        k = kw.lower()
        in_title = _kw_in(k, title)
        in_body = _kw_in(k, body)
        if in_title:
            score += 3
        if in_body:
            score += 1
        if in_title or in_body:
            matched.append(kw)
    return score, matched


def classify(articles: list[Article], config: Config) -> list[Article]:
    """exclude 적용 + 주제 배정. 어느 주제에도 안 걸리는 기사는 버린다."""
    topics = sorted(config.topics, key=lambda t: t.get("priority", 99))
    excludes = config.exclude_keywords
    kept: list[Article] = []
    for a in articles:
        haystack = f"{a.title} {a.raw_excerpt}".lower()
        if any(x in haystack for x in excludes):
            continue
        best_topic = None
        best_score = 0
        best_matched: list[str] = []
        for t in topics:  # priority 오름차순 → 동점이면 먼저 만난(더 구체적인) 주제 유지
            score, matched = _score_topic(a, t.get("keywords", []))
            if score > best_score:
                best_score, best_topic, best_matched = score, t, matched
        if not best_topic or best_score <= 0:
            continue
        a.topic = best_topic["key"]
        a.topic_label = best_topic["label"]
        a.matched_keywords = best_matched
        kept.append(a)
    return kept


def _rank(articles: list[Article], config: Config) -> list[Article]:
    now = now_utc()
    for a in articles:
        try:
            dt = dateparser.parse(a.published_at)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            age_h = (now - dt.astimezone(timezone.utc)).total_seconds() / 3600
        except Exception:
            age_h = 24.0
        recency = max(0.0, 2.0 - age_h / 24.0)            # 최신일수록 최대 2.0
        kw_score = min(len(a.matched_keywords), 6) * 0.5    # 키워드 적합도 최대 3.0
        engagement = min(a.engagement / 50.0, 2.0) if a.engagement else 0.0
        a.score = round(a.source_weight * (1.0 + recency + kw_score) + engagement, 3)
    return sorted(articles, key=lambda x: x.score, reverse=True)


def select(articles: list[Article], config: Config) -> list[Article]:
    """랭킹 후 최소 점수 / 주제별 상한 / 전체 상한 적용."""
    ranked = _rank(articles, config)
    max_total = int(config.get("max_articles_total", 28))
    max_per = int(config.get("max_articles_per_topic", 6))
    min_score = float(config.get("min_score", 1.0))
    per_topic: dict[str, int] = {}
    chosen: list[Article] = []
    for a in ranked:
        if a.score < min_score:
            continue
        if per_topic.get(a.topic, 0) >= max_per:
            continue
        per_topic[a.topic] = per_topic.get(a.topic, 0) + 1
        chosen.append(a)
        if len(chosen) >= max_total:
            break
    return chosen
