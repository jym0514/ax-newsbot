"""URL 정규화 + 중복 제거.

같은 기사가 여러 소스(예: X 링크 + 원문 매체)로 들어와도 한 번만 남긴다.
"""
from __future__ import annotations

import hashlib
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from ..models import Article
from ..state import State

# 이 접두/이름의 쿼리 파라미터는 추적용이므로 정규화 시 제거
_TRACKING = ("utm_", "fbclid", "gclid", "mc_cid", "mc_eid", "ref", "ref_src",
             "spm", "igshid", "_hsenc", "_hsmi", "cmpid", "ncid")


def normalize_url(url: str) -> str:
    """스킴/호스트/추적파라미터/말미 슬래시를 정규화한 URL."""
    url = (url or "").strip()
    try:
        p = urlparse(url)
    except Exception:
        return url.lower()
    netloc = p.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    query = urlencode(
        [
            (k, v)
            for k, v in parse_qsl(p.query, keep_blank_values=False)
            if not any(k.lower() == t or k.lower().startswith(t) for t in _TRACKING)
        ]
    )
    path = p.path.rstrip("/") or "/"
    return urlunparse(("https", netloc, path, "", query, ""))


def url_id(url: str) -> str:
    """정규화 URL의 해시 — 중복 판정/캐시 키."""
    return hashlib.sha1(normalize_url(url).encode("utf-8")).hexdigest()[:16]


def dedupe(articles: list[Article], state: State) -> tuple[list[Article], int]:
    """배치 내 중복 + 과거에 본 기사(state) 제거. (남은 기사, 제외 수) 반환."""
    seen_now: set[str] = set()
    out: list[Article] = []
    skipped = 0
    for a in articles:
        if not a.url:
            continue
        uid = url_id(a.url)
        if uid in seen_now or state.is_seen(uid):
            skipped += 1
            continue
        seen_now.add(uid)
        out.append(a)
    return out, skipped
