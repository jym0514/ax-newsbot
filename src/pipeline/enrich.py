"""기사 썸네일(og:image) 비동기 수집.

RSS 에서 이미 썸네일을 얻었거나 state 캐시에 있으면 네트워크 호출을 건너뛴다.
실패해도 무방 — 사이트는 썸네일 없는 카드에 플레이스홀더를 쓴다.
"""
from __future__ import annotations

import asyncio
import logging

import httpx
from bs4 import BeautifulSoup

from ..models import Article
from ..state import State
from ..util import HTTP_HEADERS
from .dedup import url_id

log = logging.getLogger("axnewsbot.enrich")

_CONCURRENCY = 8
_MAX_BYTES = 600_000  # <head> 만 필요하므로 앞부분만 읽는다


async def _fetch_og(client: httpx.AsyncClient, url: str, sem: asyncio.Semaphore) -> str:
    async with sem:
        try:
            async with client.stream(
                "GET", url, headers=HTTP_HEADERS, timeout=8, follow_redirects=True
            ) as resp:
                if resp.status_code >= 400:
                    return ""
                buf = b""
                async for chunk in resp.aiter_bytes():
                    buf += chunk
                    if len(buf) >= _MAX_BYTES or b"</head>" in buf.lower():
                        break
            soup = BeautifulSoup(buf, "lxml")
            for name, attrs in (
                ("meta", {"property": "og:image"}),
                ("meta", {"property": "og:image:url"}),
                ("meta", {"name": "twitter:image"}),
                ("meta", {"property": "twitter:image"}),
            ):
                tag = soup.find(name, attrs=attrs)
                if tag and tag.get("content"):
                    return tag["content"].strip()
        except Exception:  # noqa: BLE001
            return ""
        return ""


async def _run(articles: list[Article], state: State) -> None:
    sem = asyncio.Semaphore(_CONCURRENCY)
    targets: list[Article] = []
    async with httpx.AsyncClient() as client:
        tasks = []
        for a in articles:
            if a.thumbnail:
                continue
            cached = state.get_thumbnail(url_id(a.url))
            if cached:
                a.thumbnail = cached
                continue
            targets.append(a)
            tasks.append(_fetch_og(client, a.url, sem))
        results = await asyncio.gather(*tasks, return_exceptions=True)
    for a, res in zip(targets, results):
        if isinstance(res, str) and res:
            a.thumbnail = res
            state.set_thumbnail(url_id(a.url), res)


def enrich(articles: list[Article], state: State) -> None:
    """선별된 기사들의 썸네일을 채운다(제자리 수정)."""
    try:
        asyncio.run(_run(articles, state))
    except Exception as e:  # noqa: BLE001
        log.warning("썸네일 수집 중 오류: %s", e)
