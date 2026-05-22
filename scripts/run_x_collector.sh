#!/usr/bin/env bash
# Mac launchd 진입점 — 저장소 최신화 → X 수집 → 커밋·푸시.
# 매일 08:35 KST 실행(launchd/site.scopelabs.axnewsbot.x.plist).
# Mac 이 꺼져 있으면 실행되지 않고, 그날 클라우드 다이제스트는 X 없이 정상 발송된다.
set -uo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR" || exit 1

mkdir -p logs data/cache
LOG="logs/x_collector.log"
echo "===== $(date '+%F %T') X 수집 시작 =====" >> "$LOG"

# 실행할 python 결정 — 프로젝트 .venv 를 우선 사용
if [ -x ".venv/bin/python" ]; then
  PY=".venv/bin/python"
else
  PY="$(command -v python3 || true)"
fi
if [ -z "$PY" ]; then
  echo "python 을 찾을 수 없음 — README 의 Mac 셋업 참고" >> "$LOG"
  exit 1
fi

git pull --rebase --autostash >> "$LOG" 2>&1 || echo "git pull 경고(계속 진행)" >> "$LOG"

"$PY" scripts/collect_x.py >> "$LOG" 2>&1
echo "collect_x.py 종료코드 $?" >> "$LOG"

if [ -n "$(git status --porcelain data/cache 2>/dev/null)" ]; then
  git add data/cache
  git -c user.name="ax-newsbot (mac)" -c user.email="axnewsbot@local" \
      commit -m "chore: X 캐시 $(date '+%F') [skip ci]" >> "$LOG" 2>&1
  git pull --rebase --autostash >> "$LOG" 2>&1
  if git push >> "$LOG" 2>&1; then
    echo "푸시 완료" >> "$LOG"
  else
    echo "푸시 실패 — git 인증/네트워크 확인" >> "$LOG"
  fi
else
  echo "data/cache 변경 없음 — 커밋 생략" >> "$LOG"
fi
echo "===== $(date '+%F %T') 종료 =====" >> "$LOG"
