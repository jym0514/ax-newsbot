"""텔레그램 다이제스트 메시지 빌드 + 발송.

- HTML parse_mode 사용(MarkdownV2 의 광범위한 이스케이프 회피).
- 동적 필드(제목/요약/출처)만 이스케이프하고 자체 마크업은 그대로 둔다.
- 카테고리(주제)별로 그룹핑하고 카테고리 사이에 구분선을 넣는다.
- 항상 단일 메시지로 보낸다 — 4096자 한계에 맞춰 들어가는 만큼만 담고,
  나머지는 아카이브에서 보도록 안내한다.
"""
from __future__ import annotations

import hashlib
import html
import logging
import time
from collections import Counter

import httpx

from ..models import Article

log = logging.getLogger("axnewsbot.telegram")

_API = "https://api.telegram.org/bot{token}/sendMessage"
_TG_LIMIT = 4096         # 텔레그램 메시지 1건 최대 길이(UTF-16 코드유닛)
_DIVIDER = "━━━━━━━━━━━━━━━━"
_TITLE = "📰 <b>LIFE CANVAS Lab실 오늘의 뉴스</b>"

# 타이틀 바로 아래 한 줄 격려 문구 — 회차(아침/점심/저녁) 컨셉별 + 날짜 기준으로 고정
# (같은 날 같은 회차는 항상 같은 문구).
_ENCOURAGEMENTS = {
    # 아침 — 출근길, 하루 시작
    "morning": [
        "좋은 아침이에요! 오늘 하루도 가볍게 시작해봐요 ☀️",
        "출근길 수고 많으세요. 오늘도 당신의 하루를 응원합니다",
        "커피 한 잔과 함께, 상쾌하게 하루를 열어보세요 ☕",
        "오늘 하루도 당신 속도대로, 차근차근이면 충분해요",
        "새로운 하루가 밝았습니다. 오늘도 좋은 일만 가득하길 🌤️",
    ],
    # 점심 — 식사 후 오후 시작
    "noon": [
        "맛있게 드셨나요? 남은 오후도 가볍게 이어가봐요",
        "점심 든든히 채우셨다면, 오후도 거뜬할 거예요 💪",
        "식곤증 몰려와도 괜찮아요, 잠깐 숨 고르고 다시 힘내봐요",
        "오전 수고하셨어요, 이제 오후 한 스퍼트만 더!",
        "나른한 오후, 잠깐의 여유도 챙기면서 화이팅해요 🍵",
    ],
    # 저녁 — 하루 마무리, 위로
    "evening": [
        "오늘 하루도 정말 고생 많으셨어요. 편안한 저녁 보내세요 🌙",
        "애쓴 당신, 오늘은 여기까지. 푹 쉬어도 괜찮아요",
        "수고한 하루였어요. 따뜻한 저녁식사와 함께 마무리하세요",
        "오늘도 최선을 다한 당신에게 박수를 보냅니다 👏",
        "하루의 끝, 좋은 사람들과 포근한 저녁 되세요 🏡",
    ],
    # 회차 구분 없음(all/수동 실행) — 범용 문구
    "all": [
        "오늘도 한 걸음씩, 꾸준함이 결국 이깁니다 💪",
        "잘하고 있어요. 오늘 하루도 당신을 응원합니다 🌱",
        "작은 진전도 진전입니다. 오늘도 화이팅!",
        "애쓴 오늘, 수고했다고 스스로에게 말해주세요 ☕",
        "당신의 하루를 응원합니다. 오늘도 좋은 일만 가득하길 🌤️",
    ],
}


def _daily_encouragement(date_str: str, slot: str = "") -> str:
    pool = _ENCOURAGEMENTS.get(slot) or _ENCOURAGEMENTS["all"]
    idx = int(hashlib.md5(f"{date_str}:{slot}".encode("utf-8")).hexdigest(), 16) % len(pool)
    return pool[idx]


def _esc(s: str) -> str:
    return html.escape(s or "", quote=False)


def _u16len(s: str) -> int:
    """텔레그램이 세는 UTF-16 코드유닛 길이(이모지는 보통 2를 차지)."""
    return len(s.encode("utf-16-le")) // 2


def _hashtags(articles: list[Article], limit: int = 10) -> str:
    """기사들에서 가장 많이 매칭된 키워드를 해시태그 문자열로 만든다."""
    counter: Counter = Counter()
    for a in articles:
        for kw in a.matched_keywords:
            counter[kw] += 1
    tags = []
    for kw, _ in counter.most_common(limit):
        token = kw.replace(" ", "").replace("-", "")
        token = "".join(c.upper() if c.isascii() else c for c in token)
        tags.append(f"#{token}")
    return " ".join(tags)


def _article_block(article: Article, index: int) -> str:
    lines = [ln.strip() for ln in (article.summary or "").splitlines() if ln.strip()]
    summary = "\n".join(lines) if lines else (article.raw_excerpt or "")[:90]
    return (
        f'{index}. <a href="{_esc(article.url)}">{_esc(article.title)}</a>\n'
        f"{_esc(summary)}\n"
        f"<i>— {_esc(article.source)}</i>"
    )


def build_messages(
    articles: list[Article],
    date_str: str,
    archive_url: str,
    slot_label: str = "",
    config=None,
    slot: str = "",
) -> list[str]:
    """다이제스트를 카테고리별로 그룹핑한 '단일' 메시지로 빌드(리스트 길이 1).

    slot_label 이 주어지면(회차명, 예: '🌍 아침 브리핑 · 글로벌 중심') 헤더에 표기한다.
    slot("morning"/"noon"/"evening")에 따라 타이틀 아래 격려 문구의 컨셉이 달라진다.
    config 가 주어지면 카테고리 순서를 config.yaml topics 의 priority 오름차순으로 정렬한다
    (아카이브 사이트와 동일한 규칙 — site_builder._group_by_topic 참고). config 없으면
    기존처럼 랭크 순 첫 등장 순서로 정렬한다.
    """
    title = f"{_TITLE}\n{_esc(slot_label)}" if slot_label else _TITLE
    head_lines = [
        title,
        f"<i>{_esc(_daily_encouragement(date_str, slot))}</i>",
        f"🗓️ {_esc(date_str)} · 총 {len(articles)}건",
    ]
    tags = _hashtags(articles)
    if tags:
        head_lines.append(_esc(tags))
    head_lines.append(_DIVIDER)
    header = "\n".join(head_lines)
    footer = f'📊 <a href="{_esc(archive_url)}">오늘의 뉴스 전체보기</a>'

    if not articles:
        return [f"{header}\n\n오늘은 조건에 맞는 기사를 찾지 못했습니다.\n\n{footer}"]

    # 카테고리별 그룹화 — config 가 있으면 topics priority 오름차순, 없으면 랭크 순 첫 등장 순서.
    order = {t.get("key"): t.get("priority", 99) for t in config.topics} if config else {}
    bucket: dict[str, list[Article]] = {}
    labels: dict[str, str] = {}
    for a in articles:
        bucket.setdefault(a.topic, []).append(a)
        labels.setdefault(a.topic, a.topic_label or a.topic)
    keyed_groups = [(key, labels[key], items) for key, items in bucket.items()]
    if config:
        keyed_groups.sort(key=lambda g: order.get(g[0], 99))
    groups: list[tuple[str, list[Article]]] = [(label, items) for _, label, items in keyed_groups]

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


def send(messages: list[str], token: str, chat_id: str) -> tuple[int, int]:
    """메시지를 발송하고 (성공 건수, 전체 시도 건수)를 반환. 429 는 retry_after 준수.

    chat_id 는 쉼표로 여러 개 지정 가능 — 개인 채팅·그룹방 등에 동시 발송된다.
    """
    chat_ids = [c.strip() for c in str(chat_id).split(",") if c.strip()]
    url = _API.format(token=token)
    sent = 0
    with httpx.Client(timeout=30) as client:
        for cid in chat_ids:
            for text in messages:
                for attempt in range(4):
                    try:
                        resp = client.post(
                            url,
                            json={
                                "chat_id": cid,
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
                    log.error(
                        "텔레그램 발송 실패 chat=%s (%s): %s",
                        cid, resp.status_code, resp.text[:200],
                    )
                    break
                time.sleep(1)
    return sent, len(chat_ids) * len(messages)
