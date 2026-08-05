"""LLM(Claude) 기반 의미 중복제거 — 같은 사건을 다룬 기사를 군집화해 대표 1건만 남긴다.

제목/토큰 동시출현 휴리스틱은 AI 뉴스의 공유 어휘(ai·모델·출시·성능…) 때문에 오탐이 많아
(실데이터 검증상 42% 과잉제거), 기사의 '맥락(의미)'을 직접 판단하는 LLM 으로 같은 사건을
묶는다. 저비용 모델(기본 haiku) 1회 호출. 키가 없거나 호출 실패 시 입력을 그대로 통과시킨다.
"""
from __future__ import annotations

import json
import logging
import re

import anthropic

from ..config import ANTHROPIC_API_KEY, Config
from ..models import Article

log = logging.getLogger("axnewsbot.semantic_dedup")

_MODEL_FALLBACK = "claude-haiku-4-5"

_SYSTEM_PROMPT = """당신은 AI/테크 뉴스 큐레이션 에디터입니다.
주어진 기사 목록에서 '같은 사건을 다룬 중복 기사'를 군집으로 묶습니다.

판정 기준:
- 같은 발표/제품출시/사고/계약/연구결과 등 동일한 '사건'을 다루면, 매체·표현·각도가 달라도 중복입니다.
- 단지 같은 회사·인물·기술을 언급한다는 이유만으로는 중복이 아닙니다. 다루는 핵심 사건이 같아야 합니다.
- 후속 보도나 심층 분석이라도 동일 사건이 핵심이면 같은 군집으로 묶습니다.
- 서로 다른 사건이면 절대 묶지 마십시오. 확신이 없으면 묶지 마십시오(보수적으로).

출력 형식:
- JSON 배열만 출력합니다. 다른 설명·머리말·코드펜스 없이 JSON 만.
- 각 원소는 '서로 중복인 기사 id'들의 배열이며, 반드시 2개 이상의 id 를 담습니다.
- 중복 군집이 하나도 없으면 빈 배열 [] 을 출력합니다.
- 예: [[1, 5], [3, 8, 12]]
"""

# 본문에서 첫 줄만, 너무 길지 않게 — 맥락 판단에 충분하면서 토큰 절약.
_EXCERPT_CHARS = 160


def _parse_clusters(text: str, n: int) -> list[list[int]]:
    """모델 응답에서 JSON 배열(군집 목록)을 추출·검증한다. 실패하면 빈 목록."""
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except (json.JSONDecodeError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    clusters: list[list[int]] = []
    for cl in data:
        if not isinstance(cl, list):
            continue
        ids = sorted({i for i in cl if isinstance(i, int) and 0 <= i < n})
        if len(ids) >= 2:
            clusters.append(ids)
    return clusters


def dedupe_semantic(ranked: list[Article], config: Config) -> list[Article]:
    """같은 사건 기사를 LLM 으로 군집화해 군집별 최고 랭크 1건만 남긴다.

    ranked 는 랭킹 점수 내림차순이어야 한다(군집의 가장 앞 id = 최고 랭크 → 유지).
    키 미설정/호출 실패/파싱 실패 시 입력을 그대로 반환한다(중복제거를 건너뛸 뿐 안전).
    """
    if len(ranked) < 2 or not ANTHROPIC_API_KEY:
        return ranked

    model = config.get("summary_model") or _MODEL_FALLBACK
    lines = []
    for i, a in enumerate(ranked):
        excerpt = re.sub(r"\s+", " ", (a.raw_excerpt or "")).strip()[:_EXCERPT_CHARS]
        label = a.topic_label or a.topic or ""
        lines.append(f"{i}. [{label}] {a.title}" + (f" — {excerpt}" if excerpt else ""))
    user_msg = "기사 목록:\n\n" + "\n".join(lines)

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        resp = client.messages.create(
            model=model,
            max_tokens=1500,
            system=[{
                "type": "text",
                "text": _SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{"role": "user", "content": user_msg}],
        )
        text = "".join(b.text for b in resp.content if b.type == "text").strip()
    except Exception as e:  # noqa: BLE001 — 실패 시 중복제거만 건너뛴다
        log.warning("의미 기반 중복제거 실패 — 건너뜀: %s", e)
        return ranked

    clusters = _parse_clusters(text, len(ranked))
    drop: set[int] = set()
    for ids in clusters:
        keep = min(ids)  # ranked 가 점수 내림차순 → 가장 앞이 최고 랭크
        drop.update(i for i in ids if i != keep)

    if drop:
        log.info(
            "의미 기반 중복제거: %d개 군집에서 %d건 제거 (%d → %d)",
            len(clusters), len(drop), len(ranked), len(ranked) - len(drop),
        )
    return [a for i, a in enumerate(ranked) if i not in drop]
