# 배포 가이드 — GitHub Pages + 커스텀 도메인

서버 없이 동작한다. GitHub 가 1시간마다 예측을 다시 계산해(`python main.py build`) 정적 파일로 배포한다.

```
GitHub Actions (1시간마다)  →  main.py build  →  site/ (HTML + JSON)  →  GitHub Pages  →  matchdaypredictor.com
          ↑ Secrets 에 저장한 API 키 사용
```

비용: 호스팅 무료, 도메인만 연 1만~1.5만 원 수준 (.com 기준).

---

## 1. 도메인 구매

- 후보 `matchdaypredictor.com` 은 2026-10-18 기준 미등록 상태였다 (구매 직전에 다시 확인).
- 아무 등록 대행사나 된다. 예: Cloudflare Registrar(원가 판매, 연 약 $10), 가비아·후이즈 등.
- 이름에 `EPL`, `Premier League` 같은 상표는 넣지 않는 편이 안전하다.

## 2. GitHub 저장소 만들기

1. GitHub 가입 → **New repository** → 이름 예: `matchday-predictor`, **Public** (무료 Pages 는 공개 저장소에서 동작)
2. [GitHub Desktop](https://desktop.github.com) 설치 → 로그인 → **File → Add local repository** → 이 폴더 선택
   (git 저장소가 아니라고 나오면 "create a repository" 선택)
3. 변경 목록에 **`.env` 가 없는지 확인**한 뒤 Commit → **Publish / Push**

> ⚠️ API 키가 들어 있는 `.env` 는 절대 올리지 않는다. `.gitignore` 로 제외돼 있어 GitHub Desktop 은 자동으로 빼지만,
> GitHub 웹페이지에 파일을 끌어다 올리는 방식은 `.gitignore` 를 무시하므로 쓰지 않는다.
> football-data.org 약관(§6.1)도 공개 저장소에 키를 두는 것을 금지한다.

## 3. 키와 도메인 설정 (저장소 Settings)

**Settings → Secrets and variables → Actions**

| 종류 | 이름 | 값 |
|---|---|---|
| Secret | `FOOTBALL_DATA_API_KEY` | `.env` 의 같은 값 |
| Secret | `API_FOOTBALL_KEY` | `.env` 의 같은 값 |
| Variable | `SITE_DOMAIN` | `matchdaypredictor.com` |

**Settings → Pages → Build and deployment → Source: GitHub Actions**

## 4. 첫 배포

1. **Actions** 탭 → **Build and deploy** → **Run workflow**
2. 로그의 `Check data sources` 단계에서 football-data.org 가 `[OK ]` 인지 확인
   - 클라우드 서버에서 막히는 경우가 있어 첫 배포 때 꼭 확인한다. 막히면 알려주면 우회 방법(내 PC에서 빌드해 올리기)을 적용한다.
3. 끝나면 `https://<GitHub아이디>.github.io/matchday-predictor/` 에서 확인

이후에는 1시간마다(매시 15분) 자동으로 다시 빌드된다. 코드를 push 해도 바로 배포된다.

## 5. 도메인 연결

**GitHub**: Settings → Pages → **Custom domain** 에 `matchdaypredictor.com` 입력 → Save

**도메인 업체 DNS 설정**

| 타입 | 이름 | 값 |
|---|---|---|
| A | `@` | `185.199.108.153` |
| A | `@` | `185.199.109.153` |
| A | `@` | `185.199.110.153` |
| A | `@` | `185.199.111.153` |
| CNAME | `www` | `<GitHub아이디>.github.io` |

- Cloudflare DNS 를 쓰면 처음에는 프록시를 끈 **DNS only(회색 구름)** 로 둔다 (인증서 발급용).
- 반영까지 수 분~몇 시간. 인증서가 발급되면 Pages 설정에서 **Enforce HTTPS** 체크.
- (권장) GitHub 계정 Settings → Pages → **Verified domains** 로 도메인 소유를 인증해 두면 다른 사람이 도메인을 가로채지 못한다.

## 6. 공개 전 확인

- [ ] 사이트 하단에 `Football data provided by the Football-Data.org API` 표기 (자동으로 붙음, football-data.org 약관 §7.1)
- [ ] football-data.org 키는 한 애플리케이션(웹이면 도메인 하나)용 (약관 §2.3)
- [ ] 구단 로고·선수 사진은 쓰지 않음 (권리가 제3자에게 있음, 약관 §9.2) — 현재는 약칭 텍스트만 사용
- [ ] "예측은 통계 모델 결과이며 실제 결과를 보장하지 않습니다" 문구 유지

## 로컬에서 미리 보기

```bash
python main.py build --out site
python -m http.server 8100 --directory site
```

브라우저에서 http://localhost:8100
