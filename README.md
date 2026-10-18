# Matchday Predictor — Premier League

여러 데이터 출처를 교차 검증해 프리미어리그 경기일 스코어를 예측하는 멀티 에이전트 분석 프로그램.
파이썬 3.10+ 표준 라이브러리만 사용 (설치할 패키지 없음).

## 데이터 출처

이용약관이 있거나 사용 제한이 없는 출처만 쓴다. 위에 있을수록 우선한다.

| 출처 | 약관 | 가져오는 것 | 키 |
|---|---|---|---|
| [football-data.org](https://www.football-data.org) | 무료 등급 이용약관 | 팀, 이번 시즌 일정·결과(주 출처), 등급에 따라 선수 명단·득점자 | 필요 (무료) |
| [API-Football](https://www.api-football.com) | 무료 플랜 이용약관 (하루 100회) | 일정·결과(교차 확인), 득점·도움 순위, **부상·결장** | 필요 (무료) |
| [openfootball](https://github.com/openfootball/football.json) | 퍼블릭 도메인 | 이번·직전 시즌 일정·결과 (사람이 입력해 반영이 늦음) | 불필요 |
| [Fantasy Premier League](https://fantasy.premierleague.com) | **공식 약관 없음 (비공식)** | **부상·결장·출전 확률**만 사용 (일정·결과에는 쓰지 않음) | 불필요 |

- FPL 은 무료 출처 중 유일하게 이번 시즌 부상 정보를 준다. 비공식 API 라 형식이 바뀌거나 막힐 수 있고, 사이트에 "비공식" 출처로 표시된다.
  막히면 자동으로 건너뛰고 데이터 검증에 "부상 정보 없음"으로 나온다.

- 키가 없는 출처는 자동으로 건너뛴다. 키가 하나도 없으면 openfootball 만으로 동작하지만, 최근 결과와 선수·부상 정보가 빠진다.
- 공개 사이트로 운영할 때는 각 출처의 약관에서 공개 표시·출처 표기 조건을 직접 확인한다. 웹 하단에 사용 중인 출처 표기가 자동으로 붙는다.

### 키 설정

1. `.env.example` 을 `.env` 로 복사
2. 무료 가입 후 받은 키를 `FOOTBALL_DATA_API_KEY`, `API_FOOTBALL_KEY` 에 입력
3. 연결 점검:

```bash
python main.py check-sources
```

출처별로 응답 여부, 가져온 팀·경기·결과·선수·부상 수, 무료 등급에서 막힌 항목, 출처 간 스코어 불일치를 보여준다.

## 출처를 합치는 규칙 (`providers/multi.py`)

- **같은 경기**: 같은 시즌의 (홈, 원정) 조합은 한 번뿐이므로 이를 키로 묶는다. 연기돼 날짜가 달라도 같은 경기로 본다.
- **팀 이름**: 출처마다 표기가 달라(`Manchester City FC` / `Man City` / `Manchester City`) 공통 키로 맞춘다 (`providers/teamnames.py`).
- **경기 시각·라운드**: football-data → API-Football → openfootball 순. openfootball 의 영국 현지 시각은 서머타임을 반영해 UTC로 바꾼다.
- **결과**: 한 출처라도 종료 스코어가 있으면 종료. 2개 이상 출처가 같으면 "교차 확인", 다르면 우선순위 출처를 채택하고 "불일치"로 기록.
- **선수**: 이름 첫 글자 + 성으로 묶는다 (`Bukayo Saka` = `B. Saka`). 득점·도움은 큰 값, 출전 확률은 가장 낮은 값(보수적), 포지션은 처음 확인된 값.

## 에이전트 팀 구성

```
CoordinatorAgent (총괄: 순서대로 실행, 결과 캐시, 다음 경기일 계산)
 ├─ DataCollectorAgent   3개 출처 수집·병합, 수동 결장자(availability.json) 반영
 ├─ DataQualityAgent     출처 상태, 결과 교차 확인, 스코어 불일치, 결과 반영 지연, 선수 데이터 누락 점검
 ├─ TeamAnalystAgent     홈/원정 공격력·수비력(상대 보정 + 시간 가중), 최근 폼, 승격팀 사전값
 ├─ PlayerAnalystAgent   득점·도움 기여도 × (1 - 출전 확률) → 공격·수비 보정 계수
 ├─ PredictionAgent      Dixon-Coles 포아송 → 기대득점, 예상 스코어, 승무패·오버·BTTS 확률
 └─ ReportAgent          JSON 리포트 발행 (output/) → 웹/외부에서 사용
```

## 실행

```bash
python main.py check-sources          # 출처 점검
python main.py predict                # 다음 경기일 예측
python main.py -v predict --date 2026-10-25
python main.py serve                  # 웹: http://127.0.0.1:8000
python -m unittest discover -s tests -t .   # 테스트 (네트워크 없이 동작)
```

### 배포
`python main.py build` 로 서버 없이 올릴 수 있는 정적 사이트(`site/`)를 만든다. GitHub Pages + 커스텀 도메인 배포 절차는 [DEPLOY.md](DEPLOY.md).

### 웹 API
- `GET /api/leagues`, `GET /api/calendar?league=EPL`, `GET /api/next-date?league=EPL`
- `GET /api/predictions?league=EPL&date=2026-10-18` — 예측, 순위·전력, 출처 상태(`sources`), 검증 결과(`data_quality`)

## 모델 요약
- 기대득점 λ(홈) = 리그 평균 홈득점 × 홈팀 홈공격력 × 원정팀 원정수비력 × 선수 보정 × 폼 보정
- 전력은 최근 경기에 큰 가중치(`time_decay_xi`)를 두고, 표본이 적으면 사전값 쪽으로 당김(`shrinkage_games`).
  사전값은 리그 평균(1.0), 승격팀은 공격 0.85 / 수비 1.15.
- 선수 보정: 결장·불확실 선수의 득점·도움 기여분만큼 공격력 감소, 주전 수비수·GK 결장 시 실점 증가.
- 파라미터는 `config.py` 의 `ModelConfig` 에서 조정.

## 결장 정보 수동 보정
`data/availability.example.json` 을 `data/availability.json` 으로 복사해 적는다. 팀 이름은 웹에 보이는 표기(예: `Arsenal`, `Manchester City`).

## 무료 등급 실측 결과 (2026-10-18, `check-sources`)
| 출처 | 결과 |
|---|---|
| football-data.org | 팀 20, 이번 시즌 380경기(결과 65, 전날 경기까지 반영), 직전 시즌 380, 선수 명단 + 득점자 제공 |
| API-Football | **무료 플랜은 2022~2024 시즌만 제공** → 이번 시즌 일정·부상 사용 불가 (유료 플랜이면 자동으로 합쳐짐) |
| openfootball | 이번 시즌 결과 50경기까지만 반영(지연), 직전 시즌 380 |

## 알려진 한계
- 부상·결장 정보는 비공식 FPL 에 의존한다. 공식 발표와 다를 수 있다.
- API-Football 의 이번 시즌 응답 파싱은 무료 플랜에서 막혀 실데이터로 검증하지 못했다 (가짜 응답 테스트만 통과).
- xG(기대득점) 선수 데이터는 무료 출처에 없어 선수 보정은 실제 득점·도움만 쓴다.
- 지난 날짜를 예측해도 득점자 통계는 현재 시점 값이 쓰인다.
