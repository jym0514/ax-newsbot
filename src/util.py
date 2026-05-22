"""공용 유틸: HTTP 헤더, 날짜 파싱, 텍스트 정리, 시간창 판정."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from dateutil import parser as dateparser

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)
HTTP_HEADERS = {"User-Agent": USER_AGENT, "Accept-Language": "ko,en;q=0.8"}


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def local_now(tz_name: str = "Asia/Seoul") -> datetime:
    return datetime.now(ZoneInfo(tz_name))


def local_today(tz_name: str = "Asia/Seoul") -> str:
    return local_now(tz_name).date().isoformat()


def to_utc_iso(dt: datetime | None) -> str:
    if dt is None:
        return now_utc().isoformat()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def parse_date(value) -> str:
    """다양한 날짜 표현(struct_time, datetime, 문자열)을 UTC ISO8601 로. 실패 시 현재 시각."""
    if not value:
        return now_utc().isoformat()
    if isinstance(value, datetime):
        return to_utc_iso(value)
    if hasattr(value, "tm_year"):  # feedparser 의 time.struct_time
        try:
            return to_utc_iso(datetime(*value[:6], tzinfo=timezone.utc))
        except Exception:
            return now_utc().isoformat()
    try:
        return to_utc_iso(dateparser.parse(str(value)))
    except Exception:
        return now_utc().isoformat()


def clean_text(html_or_text: str, limit: int = 600) -> str:
    """HTML 태그/엔티티 제거 + 공백 정리 + 길이 제한."""
    if not html_or_text:
        return ""
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html_or_text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&[a-zA-Z#0-9]+;", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def within_window(published_iso: str, hours: int) -> bool:
    """published 시각이 '지금으로부터 hours 시간 이내'인지. 미래 시각은 하루까지 허용."""
    try:
        dt = dateparser.parse(published_iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = now_utc() - dt.astimezone(timezone.utc)
        return -86400 <= delta.total_seconds() <= hours * 3600
    except Exception:
        return True  # 날짜 미상이면 일단 포함
