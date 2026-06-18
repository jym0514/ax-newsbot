"""공유 데이터 스키마. 수집·필터·요약·발송·사이트 생성 모든 단계가 Article 에 의존한다."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields


@dataclass
class Article:
    """뉴스 1건. 수집 시점에 채워지고 파이프라인을 거치며 topic/summary/thumbnail 이 보강된다."""

    title: str
    url: str
    source: str                       # 사람이 읽는 출처명, 예: "AI타임스"
    source_type: str                  # "rss" | "substack" | "x" | "bluesky"
    published_at: str                 # ISO8601 (UTC). 미상이면 수집 시각.
    raw_excerpt: str = ""             # 원문 설명/본문 일부 (요약 폴백용)
    topic: str = ""                   # 분류된 주제 key
    topic_label: str = ""             # 주제 표시명 (이모지 포함)
    summary: str = ""                 # Claude 가 생성한 3줄 요약
    thumbnail: str = ""               # og:image URL ("" 이면 플레이스홀더 사용)
    score: float = 0.0                # 랭킹 점수
    engagement: int = 0               # X/Bluesky 의 좋아요+리포스트 등
    matched_keywords: list[str] = field(default_factory=list)
    source_weight: float = 1.0        # 소스 가중치 (랭킹용)
    region: str = "global"            # "global" | "domestic" — 소스 출신 지역(회차 부스트 기준)
    slot: str = ""                    # 선별된 회차 key (morning|noon|evening), 아카이브 섹션용

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Article":
        valid = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in valid})
