"""실행 간 영속 상태.

- seen.json: 본 적 있는 기사(중복 방지), 썸네일 캐시, 마지막 발송일.
- data/<날짜>.json: 그날 다이제스트에 담긴 기사들 — 아카이브 사이트의 진실의 원천.

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
        self._data.setdefault("last_sent_date", "")

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

    # --- 멱등성: 같은 날 재실행 시 중복 발송 방지 --------------------------
    def already_sent(self, day: str) -> bool:
        return self._data.get("last_sent_date") == day

    def mark_sent(self, day: str) -> None:
        self._data["last_sent_date"] = day

    # --- 저장 -------------------------------------------------------------
    def _prune(self) -> None:
        cutoff = (date.today() - timedelta(days=RETENTION_DAYS)).isoformat()
        self._data["seen"] = {k: v for k, v in self._data["seen"].items() if v >= cutoff}
        keep = set(self._data["seen"])
        self._data["thumbnails"] = {
            k: v for k, v in self._data["thumbnails"].items() if k in keep
        }

    def save(self) -> None:
        self._prune()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8"
        )


# --- 날짜별 아카이브 데이터 -------------------------------------------------
def save_day(day: str, articles: list[dict]) -> Path:
    """그날 다이제스트 기사들을 data/<날짜>.json 에 기록."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    p = DATA_DIR / f"{day}.json"
    p.write_text(json.dumps(articles, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def load_all_days() -> dict[str, list[dict]]:
    """data/20*.json 전체를 {날짜: [기사...]} 로 로드 (사이트 재생성용)."""
    result: dict[str, list[dict]] = {}
    for p in sorted(DATA_DIR.glob("20*.json")):
        try:
            result[p.stem] = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
    return result
