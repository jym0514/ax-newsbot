"""Mac 수집기가 저장소에 올려둔 X 캐시(data/cache/x-<날짜>.json)를 읽는다.

오늘(KST) 날짜 파일이 없으면 — 즉 Mac 이 꺼져 있었으면 — 빈 리스트를 반환한다.
그 결과 그날 다이제스트는 X 없이 다른 소스만으로 정상 발송된다(요구사항).
"""
from __future__ import annotations

import json
import logging

from ..config import ROOT, Config
from ..models import Article
from ..util import within_window

CACHE_DIR = ROOT / "data" / "cache"
log = logging.getLogger("axnewsbot.sources.x")


def collect(config: Config, run_date: str) -> list[Article]:
    path = CACHE_DIR / f"x-{run_date}.json"
    if not path.exists():
        log.info("오늘자 X 캐시 없음(%s) — Mac OFF 로 간주, X 생략", path.name)
        return []
    window = int(config.get("time_window_hours", 30))
    raw = json.loads(path.read_text(encoding="utf-8"))
    articles: list[Article] = []
    for d in raw:
        art = Article.from_dict(d)
        if within_window(art.published_at, window):
            articles.append(art)
    return articles
