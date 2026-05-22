"""data/*.json 전체로부터 아카이브 정적 사이트(site/)를 재생성한다.

site/ 는 매 실행 시 완전히 다시 만들어지고 GitHub Pages 아티팩트로 배포된다.
데이터(data/*.json)가 진실의 원천이고 HTML 은 일회성 생성물이다.
"""
from __future__ import annotations

import logging

from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..config import ROOT, Config
from ..state import load_all_days

log = logging.getLogger("axnewsbot.site")

SITE_DIR = ROOT / "site"
TEMPLATES_DIR = ROOT / "templates"


def _group_by_topic(articles: list[dict], config: Config) -> list[dict]:
    """기사 dict 리스트를 주제별로 묶고 config priority 순으로 정렬."""
    order = {
        t["key"]: (t.get("priority", 99), t.get("label", t["key"]))
        for t in config.topics
    }
    buckets: dict[str, list[dict]] = {}
    for a in articles:
        buckets.setdefault(a.get("topic", ""), []).append(a)
    groups = []
    for key, items in buckets.items():
        prio, label = order.get(key, (99, key or "기타"))
        items.sort(key=lambda x: x.get("score", 0), reverse=True)
        groups.append({"key": key, "label": label, "priority": prio, "articles": items})
    groups.sort(key=lambda g: g["priority"])
    return groups


def build_site(config: Config) -> int:
    """site/ 전체를 재생성. 생성한 날짜 페이지 수를 반환."""
    days = load_all_days()
    SITE_DIR.mkdir(parents=True, exist_ok=True)
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html", "j2"]),
    )

    feedback_url = (config.get("feedback_form_url") or "").strip()
    feedback_ready = feedback_url.startswith("http") and "CHANGE-ME" not in feedback_url

    dates_desc = sorted(days.keys(), reverse=True)
    day_tpl = env.get_template("day.html.j2")
    index_tpl = env.get_template("index.html.j2")

    for date in dates_desc:
        html_out = day_tpl.render(
            date=date,
            groups=_group_by_topic(days[date], config),
            count=len(days[date]),
            feedback_url=feedback_url,
            feedback_ready=feedback_ready,
            all_dates=dates_desc[:14],
        )
        (SITE_DIR / f"{date}.html").write_text(html_out, encoding="utf-8")

    index_out = index_tpl.render(
        days=[{"date": d, "count": len(days[d])} for d in dates_desc],
        feedback_url=feedback_url,
        feedback_ready=feedback_ready,
    )
    (SITE_DIR / "index.html").write_text(index_out, encoding="utf-8")
    # Pages 가 _ 로 시작하는 파일도 그대로 서빙하도록 Jekyll 비활성화
    (SITE_DIR / ".nojekyll").write_text("", encoding="utf-8")

    log.info("사이트 생성 완료 — %d개 날짜 페이지", len(dates_desc))
    return len(dates_desc)
