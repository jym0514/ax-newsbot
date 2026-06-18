"""data/*.json 전체로부터 아카이브 정적 사이트(site/)를 재생성한다.

site/ 는 매 실행 시 완전히 다시 만들어지고 GitHub Pages 아티팩트로 배포된다.
데이터(data/*.json)가 진실의 원천이고 HTML 은 일회성 생성물이다.
"""
from __future__ import annotations

import logging
from urllib.parse import urlsplit

from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..config import ROOT, Config
from ..state import load_all_days

log = logging.getLogger("axnewsbot.site")

SITE_DIR = ROOT / "site"
TEMPLATES_DIR = ROOT / "templates"


def _favicon_url(url: str) -> str:
    """기사 URL의 도메인에서 출처 사이트 로고(파비콘) 주소를 만든다.

    썸네일이 없는 기사의 기본 이미지로 쓴다. 도메인을 추출할 수 없으면 빈 문자열.
    """
    try:
        host = urlsplit(url or "").hostname or ""
    except ValueError:
        host = ""
    if not host:
        return ""
    # gstatic faviconV2: 사이즈 지정 가능하고 파비콘이 없는 사이트는 글로브 아이콘으로 폴백
    return (
        "https://t1.gstatic.com/faviconV2?client=SOCIAL&type=FAVICON"
        f"&fallback_opts=TYPE,SIZE,URL&size=128&url=https://{host}"
    )


def _slot_label(config: Config, key: str) -> str:
    if key == "all":
        return "전체 브리핑"
    return config.slot(key).get("label", key)


def _ordered_slots(slots_data: dict, config: Config) -> list[str]:
    """config.slots 순서(morning→noon→evening)대로, 그 외(레거시 'all')는 뒤에."""
    order = [s["key"] for s in config.slots]
    ordered = [k for k in order if k in slots_data]
    ordered += [k for k in slots_data if k not in order]
    return ordered


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
    env.filters["favicon"] = _favicon_url

    feedback_url = (config.get("feedback_form_url") or "").strip()
    feedback_ready = feedback_url.startswith("http") and "CHANGE-ME" not in feedback_url

    dates_desc = sorted(days.keys(), reverse=True)
    day_tpl = env.get_template("day.html.j2")
    index_tpl = env.get_template("index.html.j2")

    def _day_total(slots_data: dict) -> int:
        return sum(len(p.get("articles", [])) for p in slots_data.values())

    for date in dates_desc:
        slots_data = days[date]
        slot_blocks = []
        for key in _ordered_slots(slots_data, config):
            payload = slots_data[key]
            arts = payload.get("articles", [])
            slot_blocks.append({
                "key": key,
                "label": _slot_label(config, key),
                "insight": payload.get("insight", ""),
                "count": len(arts),
                "groups": _group_by_topic(arts, config),
            })
        html_out = day_tpl.render(
            date=date,
            slots=slot_blocks,
            count=_day_total(slots_data),
            feedback_url=feedback_url,
            feedback_ready=feedback_ready,
            all_dates=dates_desc[:14],
        )
        (SITE_DIR / f"{date}.html").write_text(html_out, encoding="utf-8")

    index_out = index_tpl.render(
        days=[{"date": d, "count": _day_total(days[d])} for d in dates_desc],
        feedback_url=feedback_url,
        feedback_ready=feedback_ready,
    )
    (SITE_DIR / "index.html").write_text(index_out, encoding="utf-8")
    # Pages 가 _ 로 시작하는 파일도 그대로 서빙하도록 Jekyll 비활성화
    (SITE_DIR / ".nojekyll").write_text("", encoding="utf-8")

    log.info("사이트 생성 완료 — %d개 날짜 페이지", len(dates_desc))
    return len(dates_desc)
