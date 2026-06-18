"""실행 간 영속 상태.

- seen.json: 본 적 있는 기사(중복 방지), 썸네일 캐시, 회차별 발송 마커.
- data/<날짜>.<회차>.json: 그 날·그 회차 다이제스트에 담긴 기사들 — 아카이브의 진실의 원천.
  (회차 = morning|noon|evening. 회차 없는 레거시 파일 data/<날짜>.json 은 "all" 로 취급)

GitHub Actions 잡이 매 실행 후 state/ 와 data/ 를 저장소에 커밋한다.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from .config import ROOT

STATE_PATH = ROOT / "state" / "seen.json"
DATA_DIR = ROOT / "data"
RETENTION_DAYS = 7  # seen 기록 보관 기간(롤링 윈도우)


class State:
    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path else STATE_PATH
        if self.path.exists():
            self._data = json.loads(self.path.read_text(encoding="utf-8"))
        else:
            self._data = {}
        self._data.setdefault("seen", {})         # url_id -> "YYYY-MM-DD"
        self._data.setdefault("thumbnails", {})   # url_id -> 이미지 URL
        self._data.setdefault("sent", {})          # "YYYY-MM-DD" -> [회차 key...]
        self._data.setdefault("last_sent_date", "")  # 레거시 호환(미사용)

    # --- 중복 방지 ---------------------------------------------------------
    def is_seen(self, uid: str) -> bool:
        return uid in self._data["seen"]

    def mark_seen(self, uid: str, day: str | None = None) -> None:
        self._data["seen"][uid] = day or date.today().isoformat()

    # --- 썸네일 캐시 -------------------------------------------------------
    def get_thumbnail(self, uid: str) -> str:
        return self._data["thumbnails"].get(uid, "")

    def set_thumbnail(self, uid: str, image_url: str) -> None:
        if image_url:
            self._data["thumbnails"][uid] = image_url

    # --- 멱등성: 같은 날·같은 회차 재실행 시 중복 발송 방지 ----------------
    def already_sent(self, day: str, slot: str = "all") -> bool:
        return slot in self._data.get("sent", {}).get(day, [])

    def mark_sent(self, day: str, slot: str = "all") -> None:
        slots = self._data.setdefault("sent", {}).setdefault(day, [])
        if slot not in slots:
            slots.append(slot)
        self._data["last_sent_date"] = day  # 레거시 호환

    # --- 저장 -------------------------------------------------------------
    def _prune(self) -> None:
        cutoff = (date.today() - timedelta(days=RETENTION_DAYS)).isoformat()
        self._data["seen"] = {k: v for k, v in self._data["seen"].items() if v >= cutoff}
        keep = set(self._data["seen"])
        self._data["thumbnails"] = {
            k: v for k, v in self._data["thumbnails"].items() if k in keep
        }
        self._data["sent"] = {
            k: v for k, v in self._data.get("sent", {}).items() if k >= cutoff
        }

    def save(self) -> None:
        self._prune()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8"
        )


# --- 날짜·회차별 아카이브 데이터 -------------------------------------------
def _day_file(day: str, slot: str) -> Path:
    """회차 데이터 파일 경로. slot 이 비었거나 'all' 이면 레거시 data/<날짜>.json."""
    if slot and slot != "all":
        return DATA_DIR / f"{day}.{slot}.json"
    return DATA_DIR / f"{day}.json"


def _insight_file(day: str, slot: str) -> Path:
    if slot and slot != "all":
        return DATA_DIR / f"{day}.{slot}.insight.txt"
    return DATA_DIR / f"{day}.insight.txt"


def _parse_day_file(p: Path) -> tuple[str, str]:
    """data 파일명에서 (날짜, 회차) 를 뽑는다. 회차 없으면 'all'.

    '2026-06-18.json' -> ('2026-06-18', 'all')
    '2026-06-18.morning.json' -> ('2026-06-18', 'morning')
    """
    parts = p.stem.split(".")  # 날짜에는 점이 없다(YYYY-MM-DD)
    day = parts[0]
    slot = parts[1] if len(parts) > 1 else "all"
    return day, slot


def save_day(day: str, slot: str, articles: list[dict]) -> Path:
    """그 날·그 회차 다이제스트 기사들을 data 파일에 기록."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    p = _day_file(day, slot)
    p.write_text(json.dumps(articles, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def load_all_days() -> dict[str, dict[str, dict]]:
    """data/20*.json 전체를 {날짜: {회차: {"articles": [...], "insight": str}}} 로 로드.

    사이트 재생성용. 회차 정렬은 호출 측(site_builder)이 config.slots 순서로 처리한다.
    """
    result: dict[str, dict[str, dict]] = {}
    for p in sorted(DATA_DIR.glob("20*.json")):
        day, slot = _parse_day_file(p)
        try:
            articles = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        result.setdefault(day, {})[slot] = {
            "articles": articles,
            "insight": load_insight(day, slot),
        }
    return result


def save_insight(day: str, slot: str, text: str) -> None:
    """그 날·그 회차의 '핵심 인사이트'(4~5문장) 를 insight 파일에 저장."""
    if not text:
        return
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    _insight_file(day, slot).write_text(text, encoding="utf-8")


def load_insight(day: str, slot: str = "all") -> str:
    p = _insight_file(day, slot)
    if p.exists():
        return p.read_text(encoding="utf-8").strip()
    return ""
