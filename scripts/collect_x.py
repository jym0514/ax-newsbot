"""X(트위터) 수집기 — 사용자 Mac 에서 launchd 로 매일 실행.

secrets/x_state.json 의 로그인 쿠키로 x.com/home(및 선택 계정)을 스크롤 수집해
data/cache/x-<오늘날짜>.json 에 Article 형식으로 저장한다. 이 파일을 저장소에 푸시하면
클라우드(GitHub Actions) 다이제스트 잡이 읽어 간다.

로그인 월(wall)을 만나거나 쿠키가 만료되면 경고를 남기고 빈 결과를 쓴다 →
그날 다이제스트는 X 없이 다른 소스로 정상 발송된다.
"""
from __future__ import annotations

import json
import pathlib
import random
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.config import Config  # noqa: E402
from src.models import Article  # noqa: E402
from src.util import local_today, parse_date, within_window  # noqa: E402

STATE_FILE = ROOT / "secrets" / "x_state.json"
CACHE_DIR = ROOT / "data" / "cache"

# 현재 DOM 의 모든 트윗에서 핵심 필드를 뽑는 JS. X 가 DOM 을 바꾸면 여기를 손봐야 한다.
_EXTRACTOR = r"""
() => {
  const out = [];
  for (const art of document.querySelectorAll('article[data-testid="tweet"]')) {
    const textEl = art.querySelector('div[data-testid="tweetText"]');
    const text = textEl ? textEl.innerText : '';
    const timeEl = art.querySelector('time');
    const datetime = timeEl ? timeEl.getAttribute('datetime') : '';
    const timeLink = timeEl ? timeEl.closest('a') : null;
    const statusUrl = timeLink ? timeLink.href : '';
    const m = statusUrl.match(/(?:x|twitter)\.com\/([^\/]+)\/status\/\d+/);
    const handle = m ? m[1] : '';
    let ext = '';
    for (const a of art.querySelectorAll('a[href]')) {
      const h = a.href || '';
      if (/^https?:\/\//.test(h) && !/\/\/(mobile\.)?(x|twitter)\.com/.test(h)) {
        ext = h; break;
      }
    }
    let eng = 0;
    const grp = art.querySelector('[role="group"][aria-label]');
    if (grp) {
      const lbl = grp.getAttribute('aria-label') || '';
      for (const x of lbl.matchAll(/(\d[\d,]*)/g)) eng += parseInt(x[1].replace(/,/g, ''));
    }
    if ((text || ext) && statusUrl) out.push({text, datetime, statusUrl, handle, ext, eng});
  }
  return out;
}
"""


def _scrape(page, url: str, rounds: int) -> dict[str, dict]:
    """url 을 열고 rounds 회 스크롤하며 트윗을 수집. statusUrl 기준 중복 제거."""
    found: dict[str, dict] = {}
    page.goto(url, wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(3500)
    if "/login" in page.url or "/i/flow" in page.url:
        print(f"⚠️  로그인 월 감지({page.url}) — 쿠키 만료 가능. export_x_cookies.py 재실행 필요.")
        return found
    try:
        page.wait_for_selector('article[data-testid="tweet"]', timeout=15000)
    except Exception:
        print(f"⚠️  트윗을 찾지 못함: {url}")
        return found
    for _ in range(rounds):
        for t in page.evaluate(_EXTRACTOR):
            found[t["statusUrl"]] = t
        page.mouse.wheel(0, random.randint(2600, 3600))
        page.wait_for_timeout(random.randint(1400, 2600))
    print(f"  {url} → 누적 {len(found)}건")
    return found


def main() -> int:
    config = Config()
    x_cfg = config.sources.get("x", {}) or {}
    window = int(config.get("time_window_hours", 30))
    rounds = int(x_cfg.get("scroll_rounds", 18))
    min_eng = int(x_cfg.get("min_engagement", 0))
    today = local_today(config.get("timezone", "Asia/Seoul"))

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    out_path = CACHE_DIR / f"x-{today}.json"

    if not STATE_FILE.exists():
        print(f"⚠️  {STATE_FILE} 없음 — export_x_cookies.py 를 먼저 실행하세요.")
        out_path.write_text("[]", encoding="utf-8")
        return 1

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("⚠️  playwright 미설치 — `pip install -r requirements.txt && playwright install chromium`")
        out_path.write_text("[]", encoding="utf-8")
        return 1

    raw: dict[str, dict] = {}
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled"],
                ignore_default_args=["--enable-automation"],
            )
            context = browser.new_context(
                storage_state=str(STATE_FILE),
                viewport={"width": 1280, "height": 1900},
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/125.0.0.0 Safari/537.36"
                ),
            )
            context.add_init_script(
                "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
            )
            page = context.new_page()
            targets = []
            if x_cfg.get("home_feed", True):
                targets.append("https://x.com/home")
            for handle in x_cfg.get("accounts", []) or []:
                targets.append(f"https://x.com/{str(handle).lstrip('@')}")
            for url in targets:
                try:
                    raw.update(_scrape(page, url, rounds))
                except Exception as e:  # noqa: BLE001
                    print(f"⚠️  수집 오류 {url}: {e}")
            browser.close()
    except Exception as e:  # noqa: BLE001
        print(f"⚠️  Playwright 오류: {e}")

    # Article 형식으로 변환 + 시간창/인게이지먼트 필터
    articles: list[Article] = []
    for t in raw.values():
        text = (t.get("text") or "").strip()
        if not text:
            continue  # 텍스트 없는 트윗은 키워드 분류 불가 → 제외
        published = parse_date(t.get("datetime"))
        if not within_window(published, window):
            continue
        if t.get("eng", 0) < min_eng:
            continue
        handle = t.get("handle") or "x"
        articles.append(
            Article(
                title=text.split("\n", 1)[0][:140] or text[:140],
                url=t.get("ext") or t["statusUrl"],
                source=f"X @{handle}",
                source_type="x",
                published_at=published,
                raw_excerpt=text,
                engagement=int(t.get("eng", 0)),
                source_weight=1.0,
            )
        )

    out_path.write_text(
        json.dumps([a.to_dict() for a in articles], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"✅ X 수집 완료 — {len(articles)}건 → {out_path.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
