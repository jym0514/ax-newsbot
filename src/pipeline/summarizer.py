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
주어진 기사 1건을 한국어 1~2줄로 핵심만 압축 요약합니다.

규칙:
- 1줄 또는 2줄(최대 2줄). 한 줄은 한 문장이며 줄바꿈으로 구분합니다.
- 무슨 일이 있었는지와 왜 중요한지를 압축해 담습니다.
- 가능한 한 간결하게 — 각 줄은 짧을수록 좋습니다.
- 과장·추측 금지. 기사에 없는 내용을 지어내지 않습니다.
- 인사말·머리말·번호 없이 요약 본문만 출력합니다.
- 영어 고유명사는 그대로 두고, 설명은 자연스러운 한국어로 씁니다.
"""

_MAX_WORKERS = 5
_MODEL_FALLBACK = "claude-haiku-4-5"


def _fallback_summary(article: Article) -> str:
    """요약 불가 시: 원문 발췌(없으면 제목)를 짧게 잘라 사용."""
    return (article.raw_excerpt or article.title)[:130].strip()


def _summarize_one(client: anthropic.Anthropic, model: str, article: Article) -> str:
    user = (
        f"[제목] {article.title}\n"
        f"[출처] {article.source}\n"
        f"[원문 발췌]\n{article.raw_excerpt or '(본문 없음 — 제목 기반으로 요약)'}"
    )
    resp = client.messages.create(
        model=model,
        max_tokens=250,
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
