"""텔레그램 다이제스트 메시지 빌드 + 발송.

- HTML parse_mode 사용(MarkdownV2 의 광범위한 이스케이프 회피).
- 동적 필드(제목/요약/출처)만 이스케이프하고 자체 마크업은 그대로 둔다.
- 4096자 한계 대비 보수적으로 분할하며, 기사 경계에서만 자른다.
- 마지막 메시지에 '오늘의 아카이브' 링크를 붙인다.
"""
from __future__ import annotations

import html
import logging
import time

import httpx

from ..models import Article

log = logging.getLogger("axnewsbot.telegram")

_API = "https://api.telegram.org/bot{token}/sendMessage"
_MAX_LEN = 3800  # 4096 한계 대비 여유


def _esc(s: str) -> str:
    return html.escape(s or "", quote=False)


def _article_block(article: Article, index: int) -> str:
    lines = [ln.strip() for ln in (article.summary or "").splitlines() if ln.strip()]
    summary = "\n".join(lines) if lines else (article.raw_excerpt or "")[:200]
    return (
        f'{index}. <a href="{_esc(article.url)}">{_esc(article.title)}</a>\n'
        f"{_esc(summary)}\n"
        f"<i>— {_esc(article.source)}</i>"
    )


def build_messages(
    articles: list[Article], date_str: str, archive_url: str
) -> list[str]:
    """다이제스트를 4096자 이하의 메시지 여러 개로 빌드."""
    header = f"📰 <b>AX 뉴스 다이제스트</b>\n🗓️ {_esc(date_str)} · 총 {len(articles)}건"
    footer = (
        f'📊 <a href="{_esc(archive_url)}">오늘의 뉴스 아카이브 보기</a>\n'
        "썸네일·전체 기사 링크 확인 및 개선 의견 남기기 → 위 링크"
    )

    # 토픽 헤더를 각 토픽 첫 기사 앞에 붙여 '블록'을 자기완결적으로 만든다.
    blocks: list[str] = []
    last_topic = None
    for idx, a in enumerate(articles, start=1):
        prefix = ""
        if a.topic != last_topic:
            prefix = f"<b>{_esc(a.topic_label or a.topic)}</b>\n\n"
            last_topic = a.topic
        blocks.append(prefix + _article_block(a, idx))

    if not blocks:
        blocks = ["오늘은 조건에 맞는 기사를 찾지 못했습니다."]

    # 그리디 패킹 — 기사 블록 경계에서만 분할.
    chunks: list[str] = []
    cur = header
    for block in blocks:
        candidate = f"{cur}\n\n{block}"
        if len(candidate) > _MAX_LEN:
            chunks.append(cur)
            cur = block
        else:
            cur = candidate

    if len(cur) + len(footer) + 2 > _MAX_LEN:
        chunks.append(cur)
        cur = footer
    else:
        cur = f"{cur}\n\n{footer}"
    chunks.append(cur)
    return chunks


def send(messages: list[str], token: str, chat_id: str) -> int:
    """메시지들을 순차 발송. 발송 성공 건수 반환. 429 는 retry_after 준수."""
    url = _API.format(token=token)
    sent = 0
    with httpx.Client(timeout=30) as client:
        for text in messages:
            for attempt in range(4):
                try:
                    resp = client.post(
                        url,
                        json={
                            "chat_id": chat_id,
                            "text": text,
                            "parse_mode": "HTML",
                            "disable_web_page_preview": True,
                        },
                    )
                except httpx.HTTPError as e:
                    log.warning("텔레그램 네트워크 오류(시도 %d): %s", attempt + 1, e)
                    time.sleep(2)
                    continue
                if resp.status_code == 200:
                    sent += 1
                    break
                if resp.status_code == 429:
                    retry = resp.json().get("parameters", {}).get("retry_after", 3)
                    log.warning("텔레그램 429 — %ds 대기", retry)
                    time.sleep(retry + 1)
                    continue
                log.error("텔레그램 발송 실패(%s): %s", resp.status_code, resp.text[:300])
                break
            time.sleep(1)  # 동일 채팅 약 1msg/s 제한 준수
    return sent
