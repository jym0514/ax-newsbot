# 📰 AX 뉴스봇

매일 **오전 9시**에 텔레그램으로 AI/AX 뉴스 다이제스트를 보내주는 에이전트.

- **주제**: 핫한 테크 트렌드, 국내외 AI/AX 동향, 기업의 AI 도입·업무 변화 사례, AI Native,
  AI 툴(Claude/Gemini/Codex/Genspark 등) 동향, AI Agent / Agentic AI
- **소스**: X(트위터) 홈 피드, Substack 뉴스레터, 국내외 AI/테크 매체, Bluesky
- **출력**: 주제별로 묶인 AI 3줄 요약 + 기사별 링크. 메시지 하단에 **오늘의 아카이브 사이트** 링크
- **아카이브**: 날짜별 기사 카드(썸네일·링크)와 팀 개선의견 수집 폼(Google Form)을 갖춘 웹사이트

---

## 동작 방식

```
[08:35 KST] Mac (launchd)              [09:00 KST] GitHub Actions (클라우드)
  run_x_collector.sh                     .github/workflows/daily.yml
   └ X 홈 피드 스크래핑(Playwright)         └ src.main 실행
   └ data/cache/x-<날짜>.json 푸시            · X 캐시 + RSS/Substack + Bluesky 수집
                                             · 키워드 필터 → 주제 분류 → 랭킹/선별
  (Mac 가 꺼져 있으면 이 단계는 생략 →         · Claude(Haiku)로 3줄 요약
   그날은 X 없이 다른 소스로 정상 발송)         · 텔레그램 발송
                                             · data/·state/ 커밋, 아카이브 사이트 배포
```

X 수집만 Mac에서 돌리는 이유: 클라우드 러너 IP는 X가 자주 차단하기 때문입니다.
Mac이 꺼져 있어도 다이제스트는 다른 소스로 정상 발송됩니다.

---

## 셋업 체크리스트

### 1. GitHub 저장소 만들기

```bash
cd ~/ax-newsbot
git add -A
git commit -m "AX 뉴스봇 초기 커밋"
gh repo create ax-newsbot --public --source=. --remote=origin --push
```

> GitHub 무료 플랜에서 Pages는 **공개 저장소**가 필요합니다. 코드·설정만 공개되며
> 토큰·키 같은 시크릿은 저장소에 들어가지 않습니다(아래 Secrets 사용).
> 비공개로 운영하려면 아카이브 사이트만 Vercel 등으로 옮기면 됩니다.

### 2. 텔레그램 봇 만들기

1. 텔레그램에서 **@BotFather** 와 대화 → `/newbot` → 봇 이름 지정 → **봇 토큰** 수령
2. 만든 봇과 대화를 한 번 시작(아무 메시지나 전송)
3. **chat ID** 확인:
   ```bash
   curl "https://api.telegram.org/bot<봇토큰>/getUpdates"
   ```
   응답의 `chat.id` 숫자가 chat ID 입니다. (그룹/채널로 받으려면 봇을 그 방에 초대 후 동일하게 확인)

### 3. Anthropic API 키 발급

[console.anthropic.com](https://console.anthropic.com) → API Keys → 키 생성.
요약은 저비용 모델(Haiku)을 쓰므로 하루 비용은 수십 원 수준입니다.

### 4. Google Form 만들기 (팀 개선의견 수집용)

1. [forms.google.com](https://forms.google.com) 에서 폼 생성 (예: "AX 뉴스봇 개선 의견")
2. 우상단 **보내기 → 링크(🔗)** 의 URL 복사
3. `config.yaml` 의 `feedback_form_url` 에 붙여넣기

### 5. GitHub Actions Secrets 등록

저장소 → **Settings → Secrets and variables → Actions → New repository secret** 로 3개 등록:

| 이름 | 값 |
|---|---|
| `TELEGRAM_BOT_TOKEN` | BotFather 봇 토큰 |
| `TELEGRAM_CHAT_ID` | 위에서 확인한 chat ID |
| `ANTHROPIC_API_KEY` | Anthropic API 키 |

### 6. GitHub Pages 활성화

저장소 → **Settings → Pages → Source** 를 **"GitHub Actions"** 로 설정.
주소는 `https://<사용자명>.github.io/ax-newsbot` 형태입니다.

### 7. config.yaml 마무리

`config.yaml` 의 `settings` 에서 아래 2개를 실제 값으로 바꿉니다:

```yaml
archive_base_url: "https://<사용자명>.github.io/ax-newsbot"
feedback_form_url: "https://docs.google.com/forms/d/e/..../viewform"
```

### 8. 첫 실행 테스트

저장소 → **Actions → Daily AX News Digest → Run workflow** 로 수동 실행.
텔레그램 메시지가 오고 Pages 사이트가 뜨면 성공입니다.
이후 매일 09:00(KST) 무렵 자동 실행됩니다.

---

## Mac X 수집기 셋업 (선택이지만 권장)

X 데이터를 받으려면 Mac에서 1회 설정합니다. (이 설정을 건너뛰어도 봇은 X 없이 동작합니다.)

```bash
cd ~/ax-newsbot
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/playwright install chromium

# X 로그인 세션 저장 (브라우저 창에서 로그인 후 터미널에서 Enter)
.venv/bin/python scripts/export_x_cookies.py

# 수집기 단독 테스트
.venv/bin/python scripts/collect_x.py

# 매일 08:35 자동 실행 등록 (plist 안의 경로가 실제 클론 위치와 같은지 먼저 확인)
cp launchd/site.scopelabs.axnewsbot.x.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/site.scopelabs.axnewsbot.x.plist
```

- X 쿠키(`secrets/x_state.json`)는 Mac에만 저장되고 저장소에 올라가지 않습니다.
- 수집기가 "로그인 월(wall)" 경고를 내기 시작하면 쿠키 만료 → `export_x_cookies.py` 재실행.
- 로그: `logs/x_collector.log`

---

## 커스터마이징 — `config.yaml`

| 항목 | 설명 |
|---|---|
| `topics` | 주제와 분류 키워드. 키워드가 곧 필터 — 어느 주제에도 안 걸리면 제외됩니다. |
| `exclude_keywords` | 이 단어가 있으면 무조건 제외(광고·채용 등) |
| `sources.rss` | RSS/Substack 소스 목록. `name`/`url`/`weight` 로 추가·삭제 자유롭게 |
| `sources.bluesky.accounts` | 추적할 Bluesky 핸들(추천 소스 — 실제 핸들로 교체 권장) |
| `sources.x` | X 홈 피드/계정, 스크롤 횟수, 최소 인게이지먼트 |
| `settings.max_articles_total` | 하루 최대 기사 수 |
| `settings.time_window_hours` | "최근 N시간" 이내 발행분만 후보 |

수록된 추천 소스: One Useful Thing, Latent Space, Interconnects, Import AI, Ahead of AI,
Exponential View, Last Week in AI, Berkeley RDI(Agentic AI Weekly), The Decoder, TechCrunch AI,
The Verge, MIT Tech Review, OpenAI/DeepMind/Hugging Face, AI타임스, 인공지능신문, THE AI,
바이라인네트워크, GeekNews, ZDNet Korea, 전자신문 등.

---

## 로컬 테스트

```bash
.venv/bin/python -m src.main --dry-run
```

`--dry-run` 은 텔레그램을 보내지 않고 상태를 갱신하지 않으며, 다이제스트를 콘솔에 출력하고
`site/` 를 생성합니다. `site/index.html` 을 브라우저로 열어 아카이브를 미리 볼 수 있습니다.
요약(Claude)을 함께 테스트하려면 `.env` 파일에 `ANTHROPIC_API_KEY` 를 넣으세요(`.env.example` 참고).

옵션: `--date YYYY-MM-DD`(날짜 강제), `--force`(이미 발송된 날 재실행)

---

## 알려진 한계

- GitHub Actions 스케줄은 5~20분 지연될 수 있어 "9시 정각"이 아니라 9시 전후로 도착합니다.
- 저장소가 60일간 활동이 없으면 GitHub이 스케줄을 비활성화합니다(수동 실행 시 다시 활성화).
- X 자동 수집은 약관 주의 영역이며, X가 화면 구조를 바꾸면 `scripts/collect_x.py` 의
  셀렉터를 손봐야 할 수 있습니다.
- 무료 플랜에서 GitHub Pages는 공개 저장소를 요구합니다(시크릿은 저장소 밖에 보관됨).
