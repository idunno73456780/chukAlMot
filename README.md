# MatchMate Agent

MatchMate Agent는 사용자가 관심 등록한 팀, 선수, 국가대표 경기 일정을 확인하고 캘린더 등록 대상과 관람 가능성을 판단하는 스포츠 일정 AI Agent입니다.

단순히 경기 일정을 보여주는 앱이 아니라, 관심 대상과 생활 패턴을 저장하고, 경기 중요도와 사용자의 실제 일정 조건을 평가한 뒤, 캘린더 등록 링크, 관람 가능성 이메일, 주간 뉴스레터까지 생성합니다.

## 핵심 기능

- Streamlit 웹 앱
- TheSportsDB 외부 DB 검색 기반 관심 팀, 선수, 국가대표 자동완성 등록
- 외부 DB 검색 결과를 런타임 캐시에 누적해 자동완성 후보 확장
- 한국 사용자를 위한 `한국어명 (원문)` 표시
- TheSportsDB 스포츠 경기 일정 API 연동
- 실제 API 장애 시 선택적으로만 켤 수 있는 내장 데이터 fallback
- 관심 경기 전체 캘린더 등록 대상 생성
- Google OAuth 로그인으로 Calendar와 Gmail 권한 연동
- Google Calendar API 선택 연동
- Google Calendar 템플릿 링크 fallback
- 중복 캘린더 등록 방지
- 내 업무시간, 수면 시간, 다음날 오전 일정 기반 관람 가능성 분류
- 관심 밖이지만 중요한 경기 자동 추천
- 지난 경기 결과, 순위표, 토너먼트 현황, 이번 주 경기 뉴스레터 생성
- SMTP 이메일 선택 발송
- GitHub Actions 예약 실행 스크립트

## 왜 AI Agent인가

MatchMate Agent는 단순 질의응답형 챗봇이 아니라 다음 흐름으로 동작합니다.

1. 사용자의 관심 팀, 선수, 국가대표, 생활 패턴을 구조화해 저장합니다.
2. 스포츠 일정 API를 조회합니다.
3. 관심 경기 여부와 스포츠 중요도를 판단합니다.
4. 사용자의 캘린더성 일정, 업무시간, 수면 조건을 바탕으로 관람 가능성을 평가합니다.
5. 관심 경기는 캘린더 등록 대상으로 만들고 중복 등록을 방지합니다.
6. 이메일 안내문과 주간 뉴스레터를 생성합니다.
7. 예약 스크립트를 통해 반복 실행할 수 있습니다.

## 실행 방법

```bash
cd /Users/keunhee/Documents/matchmate_agent
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

기본 실행은 실제 API 모드입니다. TheSportsDB 무료 키 `123`도 사용할 수 있지만, 배포 운영에서는 개인 API 키를 발급해 Secrets에 넣는 것을 권장합니다.

## 기본 사용 흐름

1. 메인 화면 상단에서 Google 계정을 연동합니다.
2. 사용자 정보에서 거주 지역, 시간대, 업무시간, 수면 시간을 저장합니다.
3. 관심 팀, 관심 선수, 관심 국가대표를 DB 자동완성 검색으로 선택합니다.
4. 후보에 없는 대상은 `외부 DB에서 검색`으로 TheSportsDB에서 찾아 자동완성 목록에 추가합니다.
5. 관심 목록의 `일정 확인` 버튼을 누릅니다.
6. Agent가 관심 경기, 관람 가능성, 중요도를 종합해 추천 경기 1개를 메인 화면에 보여줍니다.
7. 관심 경기 일정은 Google Calendar에 등록되고, Gmail 권한이 있으면 뉴스레터를 발송합니다.

## 관람 가능성 판단 기준

예시는 다음과 같습니다.

- 기존 일정과 충돌 없음: 가점
- 퇴근 후 저녁 시간대: 가점
- 다음날이 주말: 가점
- 평일 업무시간 경기: 감점
- 새벽 경기: 감점
- 다음날 오전 일정 있음: 감점
- 경기 후 예상 수면 시간이 부족함: 감점

결과는 다음 세 단계로 분류됩니다.

- 관람 가능성 높음
- 약간 무리하면 가능
- 많이 무리해야 함

## 중요 경기 추천 기준

관심 등록하지 않은 경기라도 다음 요소가 있으면 중요 경기로 추천됩니다.

- 결승, 준결승, 플레이오프
- 국가대표 경기
- 월드컵급 대회
- 더비/라이벌전
- 상위권 맞대결
- 우승, 강등, 진출권에 영향을 주는 경기
- 시리즈 최종전

## 환경 변수

`.env.example`을 참고하세요.

```text
MATCHMATE_SAMPLE_MODE=false
MATCHMATE_ALLOW_SAMPLE_FALLBACK=false
MATCHMATE_LOOKBACK_DAYS=7
MATCHMATE_LOOKAHEAD_DAYS=14
MATCHMATE_SEND_EMAIL=false
MATCHMATE_FETCH_TV=true
MATCHMATE_MAX_TV_LOOKUPS=2
MATCHMATE_IMPORTANT_LEAGUE_IDS=4328,4480,4387,4424
SPORTS_API_PROVIDER=thesportsdb
THESPORTSDB_API_KEY=123
GOOGLE_OAUTH_CLIENT_ID=
GOOGLE_OAUTH_CLIENT_SECRET=
GOOGLE_OAUTH_REDIRECT_URI=http://localhost:8503/
GOOGLE_OAUTH_CALENDAR_ID=primary
GOOGLE_OAUTH_TOKEN_JSON=
EMAIL_SMTP_HOST=
EMAIL_SMTP_USER=
EMAIL_SMTP_PASSWORD=
```

Google OAuth를 사용하면 사용자의 Google 계정으로 MatchMate 전용 캘린더를 만들고 Gmail 발송 권한을 사용할 수 있습니다. 서비스 계정 방식도 선택적으로 사용할 수 있으며, 설정이 없으면 캘린더 템플릿 링크가 생성됩니다.

SMTP 설정이 없으면 이메일을 실제 발송하지 않고 `storage/latest_newsletter.md` 또는 `storage/latest_viewing_email.md`에 미리보기를 저장합니다.

## 배포 방법

1. 이 폴더를 GitHub 저장소에 업로드합니다.
2. Streamlit Community Cloud에서 `Create app`을 선택합니다.
3. Repository, branch, main file path를 선택합니다. main file path는 `app.py`입니다.
4. Advanced settings의 Secrets에 아래 값을 입력합니다.

```toml
MATCHMATE_SAMPLE_MODE = "false"
MATCHMATE_ALLOW_SAMPLE_FALLBACK = "false"
MATCHMATE_SEND_EMAIL = "false"
MATCHMATE_FETCH_TV = "true"
MATCHMATE_MAX_TV_LOOKUPS = "2"
MATCHMATE_IMPORTANT_LEAGUE_IDS = "4328,4480,4387,4424"
SPORTS_API_PROVIDER = "thesportsdb"
THESPORTSDB_API_KEY = "123"

GOOGLE_OAUTH_CLIENT_ID = "발급받은 OAuth Client ID"
GOOGLE_OAUTH_CLIENT_SECRET = "발급받은 OAuth Client Secret"
GOOGLE_OAUTH_REDIRECT_URI = "https://배포된앱주소.streamlit.app/"
GOOGLE_OAUTH_CALENDAR_ID = "primary"
```

5. 배포 후 앱 URL을 복사합니다.
6. Google Cloud Console의 OAuth Client 설정에서 Authorized redirect URI에 배포 URL을 추가합니다. 예: `https://배포된앱주소.streamlit.app/`
7. Google OAuth 동의 화면이 Testing 상태라면 테스트 사용자에 본인 Google 계정을 추가합니다. 다른 사람에게 제출할 때는 앱 게시 상태를 확인합니다.
8. 배포 URL에서 Google 계정 연동, 관심 대상 검색, 일정 확인, Calendar 등록, Gmail 발송을 순서대로 테스트합니다.

예약 실행까지 운영하려면 GitHub repository secrets에도 같은 값을 넣고, 필요하면 `MATCHMATE_SEND_EMAIL=true`로 설정합니다. Gmail OAuth 토큰을 Actions에서 쓰려면 로컬에서 생성된 `storage/google_oauth_token.json` 내용을 `GOOGLE_OAUTH_TOKEN_JSON` secret으로 저장해야 합니다.

## 예약 실행

로컬에서 실행:

```bash
python scripts/check_sports_events.py
```

GitHub Actions:

```text
.github/workflows/check-sports-events.yml
```

기본 설정은 매주 월요일 UTC 00:00에 실행됩니다.

## 프로젝트 구조

```text
matchmate_agent/
  app.py
  matchmate_agent.py
  sports_api_client.py
  google_calendar_client.py
  email_notifier.py
  requirements.txt
  README.md
  .env.example
  data/
    sample_sports_events.json
    korean_terms.json
    sports_catalog.json
  storage/
    sports_catalog_cache.json
    user_profile.json
    sports_interests.json
    busy_blocks.json
    calendar_registry.json
    email_history.json
  scripts/
    check_sports_events.py
  .github/
    workflows/
      check-sports-events.yml
```
