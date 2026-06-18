"""config.yaml + 환경변수(시크릿) 로더."""
from __future__ import annotations

import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    """프로젝트 루트의 .env 를 환경변수로 로드(로컬 개발용). 운영은 Actions Secrets 사용."""
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


_load_dotenv()


class Config:
    """config.yaml 래퍼."""

    def __init__(self, path: Path | str | None = None):
        path = Path(path) if path else ROOT / "config.yaml"
        with open(path, encoding="utf-8") as f:
            self._raw = yaml.safe_load(f) or {}

    @property
    def settings(self) -> dict:
        return self._raw.get("settings", {})

    @property
    def topics(self) -> list[dict]:
        return self._raw.get("topics", [])

    @property
    def slots(self) -> list[dict]:
        return self._raw.get("slots", [])

    def slot(self, key: str) -> dict:
        """회차 key 로 slot 설정을 찾는다. 없으면 빈 dict."""
        for s in self.slots:
            if s.get("key") == key:
                return s
        return {}

    @property
    def exclude_keywords(self) -> list[str]:
        return [k.lower() for k in self._raw.get("exclude_keywords", [])]

    @property
    def sources(self) -> dict:
        return self._raw.get("sources", {})

    def get(self, key: str, default=None):
        return self.settings.get(key, default)


# --- 시크릿 (환경변수) ------------------------------------------------------
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
