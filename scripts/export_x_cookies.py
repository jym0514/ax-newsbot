"""1회용: X 에 로그인하고 세션 쿠키를 secrets/x_state.json 에 저장한다.

Mac 에서 한 번 실행한다:
    python scripts/export_x_cookies.py

브라우저 창이 뜨면 X 계정으로 로그인(2FA 포함)한 뒤, 홈 타임라인이 보이면
터미널로 돌아와 Enter 를 누른다. 쿠키는 수개월 뒤 만료될 수 있으니,
collect_x 가 로그인 월(wall)을 만나기 시작하면 이 스크립트를 다시 실행한다.
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


def main() -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto("https://x.com/login", wait_until="domcontentloaded")
        print("\n" + "=" * 60)
        print(" 브라우저 창에서 X 계정으로 로그인하세요 (2FA 포함).")
        print(" 홈 타임라인이 보이면 이 터미널로 돌아와 Enter 를 누르세요.")
        print("=" * 60)
        input("\n> 로그인을 완료했으면 Enter: ")
        context.storage_state(path=str(STATE_FILE))
        browser.close()
    print(f"\n✅ 세션 쿠키 저장 완료 → {STATE_FILE}")
    print("   이 파일은 .gitignore 처리되어 저장소에 올라가지 않습니다.")


if __name__ == "__main__":
    main()
