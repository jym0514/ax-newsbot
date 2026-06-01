"""AX 뉴스봇 오케스트레이터 — 클라우드(GitHub Actions)에서 매일 실행.

수집 → 중복제거 → 분류/필터 → 선별 → 썸네일 → 요약 → 텔레그램 발송
→ 데이터/상태 저장 → 아카이브 사이트 재생성.

각 단계는 격리되어 한 단계의 실패가 전체 파이프라인을 멈추지 않는다.
X·Anthropic·일부 소스가 없어도 다이제스트는 발송된다.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import logging
import os
import sys

from .config import ROOT, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, Config
from .delivery import site_builder, telegram
from .pipeline import enrich as enrich_mod
from .pipeline import filter as filter_mod
from .pipeline import summarizer
from .pipeline.dedup import dedupe, url_id
from .sources.base import collect_all
from .models import Article
from .state import State, load_all_days, load_insight, save_day, save_insight
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


def _backfill_insights(config: Config) -> int:
    """data/*.json 들 중 인사이트가 없는 날짜에 대해 생성·저장. 채운 개수 반환."""
    filled = 0
    for day, items in load_all_days().items():
        if load_insight(day):
            continue
        articles = [Article.from_dict(d) for d in items]
        insight = summarizer.synthesize_insight(articles, config)
        if insight:
            save_insight(day, insight)
            log.info("핵심 인사이트 백필: %s (%d자)", day, len(insight))
            filled += 1
    return filled


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


def run(
    config: Config,
    dry_run: bool,
    run_date: str,
    force: bool,
    prepare_only: bool = False,
) -> int:
    """단일 단계 실행. prepare_only=True 면 텔레그램 발송·발송 마커를 건너뛴다.

    워크플로에서는 prepare_only=True 로 사이트까지 만든 뒤 Pages 가 라이브에 올라간
    다음 별도 단계에서 run_send_only() 가 텔레그램을 보낸다 (사이트 배포 전에
    메시지가 가서 링크 클릭 시 404 가 뜨는 문제를 막기 위함).
    """
    log.info("=== AX 뉴스봇 시작 — %s (dry_run=%s, prepare_only=%s) ===",
             run_date, dry_run, prepare_only)

    if not dry_run and not force and not _is_business_day(run_date):
        log.info("%s 은 주말 또는 공휴일(한국 기준) — 발송 생략, 인사이트 백필 + 사이트 재배포", run_date)
        # 발송은 건너뛰되 누락된 인사이트를 채우고 사이트는 기존 data/ 로 재생성한다.
        _backfill_insights(config)
        site_builder.build_site(config)
        return 0

    state = State()

    # prepare 단계는 같은 날 재실행해도 안전하므로 already_sent 체크를 건너뛴다.
    if not prepare_only and state.already_sent(run_date) and not dry_run and not force:
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

    # 6. AI 요약 (기사별 1줄) + 오늘의 핵심 인사이트 (4~5 문장 메타 요약)
    summarized_ok = summarizer.summarize(selected, config)
    insight = summarizer.synthesize_insight(selected, config)

    # 7. 텔레그램 발송 (prepare-only 모드에서는 건너뛴다)
    archive_base = (config.get("archive_base_url") or "").rstrip("/")
    archive_url = f"{archive_base}/{run_date}.html"
    messages = telegram.build_messages(selected, run_date, archive_url)
    sent, expected, send_failed = 0, len(messages), False
    if prepare_only:
        log.info("[prepare-only] 텔레그램은 사이트 배포 후 별도 단계에서 발송")
    elif dry_run:
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
    save_insight(run_date, insight)
    _backfill_insights(config)  # 기존 데이터 중 인사이트 없는 날짜 보충
    if not dry_run:
        for a in selected:
            state.mark_seen(url_id(a.url), run_date)
        # 발송 마커는 실제로 텔레그램이 나간 경우에만 — prepare-only 는 건너뛴다.
        if not prepare_only and not send_failed:
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


def run_send_only(config: Config, run_date: str) -> int:
    """이미 준비된 data/<날짜>.json 를 읽어 텔레그램 발송만 수행.

    워크플로에서 Pages 배포 완료 후 호출 — 사이트가 라이브에 올라간 다음에야
    메시지가 나가도록 한다.
    """
    log.info("=== send-only 단계 — %s ===", run_date)

    if not _is_business_day(run_date):
        log.info("%s 은 주말/공휴일 — 발송 생략", run_date)
        return 0

    state = State()
    if state.already_sent(run_date):
        log.info("%s 다이제스트는 이미 발송됨 — 생략", run_date)
        return 0

    p = ROOT / "data" / f"{run_date}.json"
    if not p.exists():
        log.warning("발송할 data/%s.json 가 없음 — prepare 단계가 누락됐는지 확인", run_date)
        return 0

    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        log.error("data 로드 실패: %s", e)
        return 1
    articles = [Article.from_dict(d) for d in raw]
    if not articles:
        log.info("선별된 기사가 없음 — 발송 생략")
        return 0

    archive_base = (config.get("archive_base_url") or "").rstrip("/")
    archive_url = f"{archive_base}/{run_date}.html"
    messages = telegram.build_messages(articles, run_date, archive_url)

    if not (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID):
        log.error("TELEGRAM_BOT_TOKEN/CHAT_ID 미설정 — 발송 생략")
        return 1

    sent, expected = telegram.send(messages, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)
    log.info("텔레그램 발송 %d/%d", sent, expected)
    if sent < expected:
        return 1

    state.mark_sent(run_date)
    state.save()
    log.info("=== send-only 완료 ===")
    return 0


def main() -> None:
    _setup_logging()
    parser = argparse.ArgumentParser(description="AX 뉴스봇 일일 다이제스트")
    parser.add_argument("--dry-run", action="store_true",
                        help="텔레그램 미발송·상태 미갱신(데이터/사이트는 생성)")
    parser.add_argument("--date", default=None, help="실행 날짜 강제 지정(YYYY-MM-DD)")
    parser.add_argument("--force", action="store_true",
                        help="이미 발송된 날짜여도 재실행")
    parser.add_argument("--prepare-only", action="store_true",
                        help="텔레그램 발송·발송 마커 없이 수집·요약·사이트만 준비")
    parser.add_argument("--send-only", action="store_true",
                        help="이미 준비된 data 로 텔레그램 발송만 (Pages 배포 후 호출)")
    args = parser.parse_args()

    config = Config()
    run_date = args.date or local_today(config.get("timezone", "Asia/Seoul"))
    try:
        if args.send_only:
            code = run_send_only(config, run_date)
        else:
            code = run(
                config,
                dry_run=args.dry_run,
                run_date=run_date,
                force=args.force,
                prepare_only=args.prepare_only,
            )
    except Exception as e:  # noqa: BLE001
        log.exception("치명적 오류: %s", e)
        code = 2
    sys.exit(code)


if __name__ == "__main__":
    main()
