"""AX 뉴스봇 오케스트레이터 — 클라우드(GitHub Actions)에서 매일 실행.

수집 → 중복제거 → 분류/필터 → 선별 → 썸네일 → 요약 → 텔레그램 발송
→ 데이터/상태 저장 → 아카이브 사이트 재생성.

각 단계는 격리되어 한 단계의 실패가 전체 파이프라인을 멈추지 않는다.
X·Anthropic·일부 소스가 없어도 다이제스트는 발송된다.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import logging
import os
import sys

from .config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, Config
from .delivery import site_builder, telegram
from .pipeline import enrich as enrich_mod
from .pipeline import filter as filter_mod
from .pipeline import summarizer
from .pipeline.dedup import dedupe, url_id
from .sources.base import collect_all
from .state import State, save_day
from .util import local_today

log = logging.getLogger("axnewsbot")

try:
    import holidays as _holidays_lib

    _KR_HOLIDAYS = _holidays_lib.SouthKorea()
except Exception:  # noqa: BLE001 — 라이브러리 미설치 시 주말만 거른다
    _KR_HOLIDAYS = {}


def _is_business_day(date_str: str) -> bool:
    """한국 기준 영업일 여부 — 토·일과 공휴일이면 False."""
    try:
        y, m, d = (int(x) for x in date_str.split("-"))
        day = _dt.date(y, m, d)
    except Exception:  # noqa: BLE001
        return True
    if day.weekday() >= 5:  # 5=토, 6=일
        return False
    return day not in _KR_HOLIDAYS


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )


def _github_summary(lines: list[str]) -> None:
    """GitHub Actions Job Summary 에 실행 결과를 기록(GITHUB_STEP_SUMMARY 가 있을 때만)."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except Exception:  # noqa: BLE001
        pass


def run(config: Config, dry_run: bool, run_date: str, force: bool) -> int:
    log.info("=== AX 뉴스봇 시작 — %s (dry_run=%s) ===", run_date, dry_run)

    if not dry_run and not force and not _is_business_day(run_date):
        log.info("%s 은 주말 또는 공휴일(한국 기준) — 다이제스트 발송 생략", run_date)
        return 0

    state = State()

    if state.already_sent(run_date) and not dry_run and not force:
        log.info("%s 다이제스트는 이미 발송됨 — 종료(--force 로 재발송)", run_date)
        return 0

    # 1. 수집 (RSS/Substack + Bluesky + X 캐시)
    collected, source_stats = collect_all(config, run_date)
    log.info("수집 합계: %d건", len(collected))

    # 2. 중복 제거 (배치 내 + 과거에 본 기사)
    deduped, dup_skipped = dedupe(collected, state)

    # 3. 키워드 필터 + 주제 분류
    classified = filter_mod.classify(deduped, config)

    # 4. 랭킹 + 선별 (주제별/전체 상한)
    selected = filter_mod.select(classified, config)
    log.info(
        "선별 %d건 (수집 %d → 중복제외 %d → 키워드매치 %d)",
        len(selected), len(collected), dup_skipped, len(classified),
    )

    # 5. 썸네일 보강
    enrich_mod.enrich(selected, state)

    # 6. AI 요약
    summarized_ok = summarizer.summarize(selected, config)

    # 7. 텔레그램 발송
    archive_base = (config.get("archive_base_url") or "").rstrip("/")
    archive_url = f"{archive_base}/{run_date}.html"
    messages = telegram.build_messages(selected, run_date, archive_url)
    sent, expected, send_failed = 0, len(messages), False
    if dry_run:
        log.info("[dry-run] 텔레그램 미발송 — 메시지 %d개 미리보기:", len(messages))
        for m in messages:
            print("\n" + "─" * 64 + "\n" + m)
    elif not (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID):
        log.error("TELEGRAM_BOT_TOKEN/CHAT_ID 미설정 — 발송 생략")
        send_failed = True
    else:
        sent, expected = telegram.send(messages, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)
        send_failed = sent < expected
        log.info("텔레그램 발송 %d/%d", sent, expected)

    # 8. 데이터 저장 (아카이브의 진실의 원천) — 사이트보다 먼저
    save_day(run_date, [a.to_dict() for a in selected])
    if not dry_run:
        for a in selected:
            state.mark_seen(url_id(a.url), run_date)
        if not send_failed:
            state.mark_sent(run_date)
        state.save()

    # 9. 아카이브 사이트 재생성
    page_count = site_builder.build_site(config)

    # 결과 요약 (Actions Job Summary)
    lines = [
        f"## 📰 AX 뉴스봇 — {run_date}",
        "",
        f"- 수집 {len(collected)}건 → 중복제외 {dup_skipped} → 키워드매치 "
        f"{len(classified)} → **다이제스트 {len(selected)}건**",
        f"- 요약 {summarized_ok}/{len(selected)} · 텔레그램 {sent}/{expected} 발송 "
        f"· 아카이브 {page_count}일치",
        "",
        "| 소스 | 수집 건수 |",
        "|---|---|",
    ]
    for name in sorted(source_stats):
        n = source_stats[name]
        lines.append(f"| {name} | {'⚠️ 실패' if n < 0 else n} |")
    _github_summary(lines)

    log.info("=== 완료 (다이제스트 %d건) ===", len(selected))
    return 1 if send_failed else 0


def main() -> None:
    _setup_logging()
    parser = argparse.ArgumentParser(description="AX 뉴스봇 일일 다이제스트")
    parser.add_argument("--dry-run", action="store_true",
                        help="텔레그램 미발송·상태 미갱신(데이터/사이트는 생성)")
    parser.add_argument("--date", default=None, help="실행 날짜 강제 지정(YYYY-MM-DD)")
    parser.add_argument("--force", action="store_true",
                        help="이미 발송된 날짜여도 재실행")
    args = parser.parse_args()

    config = Config()
    run_date = args.date or local_today(config.get("timezone", "Asia/Seoul"))
    try:
        code = run(config, dry_run=args.dry_run, run_date=run_date, force=args.force)
    except Exception as e:  # noqa: BLE001
        log.exception("치명적 오류: %s", e)
        code = 2
    sys.exit(code)


if __name__ == "__main__":
    main()
