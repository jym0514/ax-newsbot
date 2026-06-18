"""키워드 기반 필터 + 주제 분류 + 랭킹/선별.

키워드가 곧 필터다 — 어느 주제 키워드에도 걸리지 않는 기사는 다이제스트에서 빠진다.
주제 분류는 결정적(키워드 점수 최댓값, 동점 시 priority 가 낮은 = 더 구체적인 주제).
"""
from __future__ import annotations

import re
from collections import Counter
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


def _rank(
    articles: list[Article],
    config: Config,
    boost_region: str = "",
    boost: float = 1.0,
) -> list[Article]:
    """랭킹 점수 계산. boost_region 과 일치하는 region 의 기사는 점수에 boost 배수를 곱한다.

    회차 '중심'을 구현 — 키워드/맥락 필터는 그대로 두고 해당 지역 소스만 상위로 끌어올린다.
    """
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
        base = a.source_weight * (1.0 + recency + kw_score) + engagement
        if boost_region and a.region == boost_region:
            base *= boost
        a.score = round(base, 3)
    return sorted(articles, key=lambda x: x.score, reverse=True)


_BRACKET_RE = re.compile(r"\[[^\]]*\]")
_NONWORD_RE = re.compile(r"[^0-9a-z가-힣]+")


def _title_tokens(title: str) -> list[str]:
    t = _BRACKET_RE.sub(" ", title.lower())
    t = _NONWORD_RE.sub(" ", t)
    return [w for w in t.split() if len(w) >= 2]


def _tok_alike(x: str, y: str) -> bool:
    """같은 단어로 볼지 판정. 접두사 차이가 2자(한국어 조사: 로/는/가/이…) 이내일 때만.

    'ai' 가 'ai연구원' 같은 합성어를 잘못 흡수하지 않도록 차이 길이를 제한한다.
    """
    if x == y:
        return True
    short, long = (x, y) if len(x) <= len(y) else (y, x)
    return (
        len(short) >= 2
        and long.startswith(short)
        and len(long) - len(short) <= 2
    )


def _dedupe_stories(
    ranked: list[Article], prior_titles: list[str] | None = None
) -> list[Article]:
    """같은 사건을 다룬 중복 보도(서로 다른 매체)를 제거 — 상위 랭크 기사만 남긴다.

    제목의 '희소 토큰'(후보군에서 3개 이하 제목에만 등장)을 2개 이상 공유하면 중복으로 본다.
    prior_titles 가 주어지면(같은 날 앞 회차에서 이미 발송된 제목들) 그 제목들과 겹치는
    기사도 중복으로 보고 제거한다 — 회차 간 같은 사건 중복 발송 방지.
    """
    token_lists = [_title_tokens(a.title) for a in ranked]
    df: Counter = Counter()
    for toks in token_lists:
        for w in set(toks):
            df[w] += 1
    kept: list[Article] = []
    # 앞 회차에서 이미 발송된 제목들의 토큰으로 kept_tokens 를 미리 채운다.
    kept_tokens: list[list[str]] = [_title_tokens(t) for t in (prior_titles or [])]
    for art, toks in zip(ranked, token_lists):
        rare = [w for w in set(toks) if df[w] <= 3]
        is_dup = False
        for other in kept_tokens:
            shared = sum(1 for w in rare if any(_tok_alike(w, v) for v in other))
            if shared >= 2:
                is_dup = True
                break
        if not is_dup:
            kept.append(art)
            kept_tokens.append(toks)
    return kept


def select(
    articles: list[Article],
    config: Config,
    slot_key: str = "",
    prior_titles: list[str] | None = None,
) -> list[Article]:
    """랭킹 → 중복 기사 제거 → 최소 점수 / 주제별 상한 / 전체 상한 적용.

    slot_key 가 주어지면 해당 회차가 강조하는 region 소스를 부스트하고, 선별된 기사에
    slot 을 표기한다. prior_titles(같은 날 앞 회차 제목)는 회차 간 중복 제거에 쓰인다.
    """
    slot_cfg = config.slot(slot_key) if slot_key else {}
    boost_region = slot_cfg.get("boost_region", "")
    boost = float(config.get("region_boost", 1.5)) if boost_region else 1.0
    ranked = _dedupe_stories(
        _rank(articles, config, boost_region, boost), prior_titles
    )
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
        a.slot = slot_key
        chosen.append(a)
        if len(chosen) >= max_total:
            break
    return chosen
