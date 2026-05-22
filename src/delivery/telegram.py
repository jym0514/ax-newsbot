"""텔레그램 다이제스트 메시지 빌드 + 발송.

- HTML parse_mode 사용(MarkdownV2 의 광범위한 이스케이프 회피).
- 동적 필드(제목/요약/출처)만 이스케이프하고 자체 마크업은 그대로 둔다.
- 카테고리(주제)별로 그룹핑하고 카테고리 사이에 구분선을 넣는다.
- 항상 단일 메시지로 보낸다 — 4096자 한계에 맞춰 들어가는 만큼만 담고,
  나머지는 아카이브에서 보도록 안내한다.
"""
from __future__ import annotations

import html
import logging
import time

import httpx

from ..models import Article

log = logging.getLogger("axnewsbot.telegram")

_API = "https://api.telegram.org/bot{token}/sendMessage"
_TG_LIMIT = 4096         # 텔레그램 메시지 1건 최대 길이(UTF-16 코드유닛)
_DIVIDER = "━━━━━━━━━━━━━━━━"
_TITLE = "📰 <b>AX 전략실 오늘의 뉴스</b>"


def _esc(s: str) -> str:
    return html.escape(s or "", quote=False)


def _u16len(s: str) -> int:
    """텔레그램이 세는 UTF-16 코드유닛 길이(이모지는 보통 2를 차지)."""
    return len(s.encode("utf-16-le")) // 2


def _article_block(article: Article, index: int) -> str:
    lines = [ln.strip() for ln in (article.summary or "").splitlines() if ln.strip()]
    summary = "\n".join(lines) if lines else (article.raw_excerpt or "")[:90]
    return (
        f'{index}. <a href="{_esc(article.url)}">{_esc(article.title)}</a>\n'
        f"{_esc(summary)}\n"
        f"<i>— {_esc(article.source)}</i>"
    )


def build_messages(
    articles: list[Article], date_str: str, archive_url: str
) -> list[str]:
    """다이제스트를 카테고리별로 그룹핑한 '단일' 메시지로 빌드(리스트 길이 1)."""
    header = f"{_TITLE}\n🗓️ {_esc(date_str)} · 총 {len(articles)}건"
    footer = (
        f'📊 <a href="{_esc(archive_url)}">전체 기사·썸네일 보기 → 오늘의 아카이브</a>'
    )

    if not articles:
        return [f"{header}\n\n오늘은 조건에 맞는 기사를 찾지 못했습니다.\n\n{footer}"]

    # 카테고리별 그룹화 — 랭크 순 첫 등장 순서로 카테고리 정렬
    groups: list[tuple[str, list[Article]]] = []
    bucket: dict[str, list[Article]] = {}
    for a in articles:
        if a.topic not in bucket:
            bucket[a.topic] = []
            groups.append((a.topic_label or a.topic, bucket[a.topic]))
        bucket[a.topic].append(a)

    parts = [header]
    shown = 0
    truncated = False

    for gi, (label, items) in enumerate(groups):
        group_open = False
        for a in items:
            block = _article_block(a, shown + 1)
            if not group_open:
                sep = f"\n\n{_DIVIDER}\n\n" if gi > 0 else "\n\n"
                addition = f"{sep}<b>{_esc(label)}</b>\n\n{block}"
            else:
                addition = f"\n\n{block}"
            # 실제 UTF-16 길이로 검사 — 잔여 안내문구(~35) + footer 자리 확보
            if _u16len("".join(parts) + addition) + 110 > _TG_LIMIT:
                truncated = True
                break
            parts.append(addition)
            group_open = True
            shown += 1
        if truncated:
            break

    if shown < len(articles):
        parts.append(
            f"\n\n<i>… 외 {len(articles) - shown}건은 아카이브에서 확인하세요.</i>"
        )
    parts.append(f"\n\n{footer}")
    return ["".join(parts)]


def send(messages: list[str], token: str, chat_id: str) -> int:
    """메시지를 발송. 발송 성공 건수 반환. 429 는 retry_after 준수."""
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
            time.sleep(1)
    return sent
