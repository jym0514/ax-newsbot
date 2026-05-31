"""Anthropic API 로 각 기사를 한국어 3줄로 요약.

모델은 config.yaml 의 summary_model(기본 claude-haiku-4-5) — 저비용 대량 요약에 적합.
정적 시스템 프롬프트에 prompt caching 을 적용한다. 요약 실패 시 원문 발췌로 폴백한다.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

import anthropic

from ..config import ANTHROPIC_API_KEY, Config
from ..models import Article

log = logging.getLogger("axnewsbot.summarizer")

_SYSTEM_PROMPT = """당신은 AI/테크 뉴스 다이제스트의 전문 에디터입니다.
주어진 기사 1건을 한국어 1줄(한 문장)로 핵심만 압축 요약합니다.

규칙:
- 정확히 1줄, 한 문장. 줄바꿈 없이.
- 무슨 일이 있었는지와 왜 중요한지를 한 문장에 압축합니다.
- 80자 이내를 목표로 최대한 간결하게 씁니다.
- 과장·추측 금지. 기사에 없는 내용을 지어내지 않습니다.
- 인사말·머리말·번호 없이 요약 문장만 출력합니다.
- 영어 고유명사는 그대로 두고, 설명은 자연스러운 한국어로 씁니다.
"""

_MAX_WORKERS = 5
_MODEL_FALLBACK = "claude-haiku-4-5"


def _fallback_summary(article: Article) -> str:
    """요약 불가 시: 원문 발췌(없으면 제목)를 짧게 잘라 사용."""
    return (article.raw_excerpt or article.title)[:90].strip()


_INSIGHT_PROMPT = """당신은 AI/테크 뉴스 다이제스트의 시니어 에디터입니다.
오늘 큐레이션된 기사 목록을 읽고, 가장 중요한 흐름과 시사점을 4~5 문장의 한국어로 압축합니다.

규칙:
- 정확히 4~5 문장.
- 개별 기사를 나열하지 말고, 여러 기사를 관통하는 패턴·테마·시사점을 짚습니다.
- 첫 문장은 오늘의 가장 큰 흐름을 한 문장으로 요약.
- 이어서 주요 동향 2~3개를 묶어 설명.
- 마지막 문장은 기업·실무 관점의 시사점.
- 자연스러운 한국어 문장. 과장·추측·기사에 없는 내용 금지.
- 인사말·머리말·번호·불릿 없이 문장만 출력.
"""


def synthesize_insight(articles: list[Article], config: Config) -> str:
    """오늘 큐레이션된 기사들을 종합해 4~5문장의 메타 인사이트를 생성한다."""
    if not articles or not ANTHROPIC_API_KEY:
        return ""
    model = config.get("summary_model") or _MODEL_FALLBACK
    bullets = []
    for a in articles:
        s = (a.summary or a.raw_excerpt or "").split("\n", 1)[0].strip()
        label = a.topic_label or a.topic or ""
        bullets.append(f"- [{label}] {a.title} — {s}")
    user_msg = "오늘 큐레이션된 기사 목록:\n\n" + "\n".join(bullets)
    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        resp = client.messages.create(
            model=model,
            max_tokens=600,
            system=[{
                "type": "text",
                "text": _INSIGHT_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{"role": "user", "content": user_msg}],
        )
        return "".join(b.text for b in resp.content if b.type == "text").strip()
    except Exception as e:  # noqa: BLE001
        log.warning("핵심 인사이트 생성 실패: %s", e)
        return ""


def _summarize_one(client: anthropic.Anthropic, model: str, article: Article) -> str:
    user = (
        f"[제목] {article.title}\n"
        f"[출처] {article.source}\n"
        f"[원문 발췌]\n{article.raw_excerpt or '(본문 없음 — 제목 기반으로 요약)'}"
    )
    resp = client.messages.create(
        model=model,
        max_tokens=150,
        system=[
            {
                "type": "text",
                "text": _SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user}],
    )
    return "".join(b.text for b in resp.content if b.type == "text").strip()


def summarize(articles: list[Article], config: Config) -> int:
    """선별된 기사들의 summary 를 채운다(제자리 수정). 요약 성공 건수를 반환."""
    if not articles:
        return 0
    if not ANTHROPIC_API_KEY:
        log.warning("ANTHROPIC_API_KEY 없음 — 요약 생략, 원문 발췌로 대체")
        for a in articles:
            a.summary = _fallback_summary(a)
        return 0

    model = config.get("summary_model") or _MODEL_FALLBACK
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    def work(a: Article) -> bool:
        try:
            a.summary = _summarize_one(client, model, a)
            return bool(a.summary)
        except Exception as e:  # noqa: BLE001 — SDK 가 429/5xx 는 이미 재시도함
            log.warning("요약 실패 [%s]: %s", a.title[:40], e)
            a.summary = _fallback_summary(a)
            return False

    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as ex:
        results = list(ex.map(work, articles))
    return sum(1 for r in results if r)
