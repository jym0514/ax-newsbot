"""1회용: X 에 로그인하고 세션 쿠키를 secrets/x_state.json 에 저장한다.

Mac 에서 한 번 실행한다:
    python scripts/export_x_cookies.py

브라우저 창이 뜨면 X 계정으로 로그인(2FA 포함)한 뒤, 홈 타임라인이 보이면
터미널로 돌아와 Enter 를 누른다. 쿠키는 수개월 뒤 만료될 수 있으니,
collect_x 가 로그인 월(wall)을 만나기 시작하면 이 스크립트를 다시 실행한다.

X 는 자동화된 브라우저의 로그인을 차단하므로(navigator.webdriver / --enable-automation
탐지), 아래처럼 탐지 우회 설정과 실제 Chrome 채널을 사용한다.
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
STATE_FILE = ROOT / "secrets" / "x_state.json"

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sys.exit("playwright 미설치 — `pip install -r requirements.txt && playwright install chromium`")

# 자동화 탐지를 줄이는 실행 인자/설정
STEALTH_ARGS = ["--disable-blink-features=AutomationControlled"]
IGNORE_ARGS = ["--enable-automation"]
REAL_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)
_STEALTH_JS = "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"


def _launch(p):
    """가능하면 실제 Chrome(채널)로, 없으면 번들 Chromium 으로 — 둘 다 stealth 적용."""
    try:
        return p.chromium.launch(
            headless=False, channel="chrome",
            args=STEALTH_ARGS, ignore_default_args=IGNORE_ARGS,
        )
    except Exception:
        return p.chromium.launch(
            headless=False, args=STEALTH_ARGS, ignore_default_args=IGNORE_ARGS,
        )


def main() -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = _launch(p)
        context = browser.new_context(user_agent=REAL_UA, viewport={"width": 1280, "height": 900})
        context.add_init_script(_STEALTH_JS)
        page = context.new_page()
        page.goto("https://x.com/login", wait_until="domcontentloaded")
        print("\n" + "=" * 62)
        print(" 브라우저 창에서 X 계정으로 로그인하세요 (2FA 포함).")
        print(" 홈 타임라인이 보이면 이 터미널로 돌아와 Enter 를 누르세요.")
        print(" ※ 여전히 '안전하지 않은 브라우저' 가 뜨면 터미널에서 Ctrl+C 후")
        print("    담당자에게 알려 주세요 — 수동 쿠키 방식으로 진행합니다.")
        print("=" * 62)
        input("\n> 로그인을 완료했으면 Enter: ")
        context.storage_state(path=str(STATE_FILE))
        browser.close()
    print(f"\n✅ 세션 쿠키 저장 완료 → {STATE_FILE}")
    print("   이 파일은 .gitignore 처리되어 저장소에 올라가지 않습니다.")


if __name__ == "__main__":
    main()
