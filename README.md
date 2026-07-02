# 미국 프리마켓 동전주 급등 스크리너

TradingView 스캐너 API(무료, 키 불필요)로 프리마켓에 급등 중인 $5 미만 동전주를
찾아 텔레그램으로 알림을 보내고, 결과 CSV를 `reports/`에 커밋한다.

- **자동 실행**: GitHub Actions 크론 — 미국 프리마켓(한국시간 17:00~22:30, 서머타임 기준) 30분 간격
- **리스크 플래그**: 초저유통(<5M) / $1미만(상폐권) / 거래량허수 / 과열(+100%↑) 자동 표시
- **수동 실행**: Actions 탭 → "Premarket penny gapper scan" → Run workflow

## 로컬 실행

```
pip install -r requirements.txt
python premarket_screener.py                   # 기본: $5 미만, PM +10% 이상
python premarket_screener.py --min-change 30   # +30% 이상만
python premarket_screener.py --session regular # 정규장 모드
```

텔레그램 발송(`--notify`)에는 `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` 환경변수 필요.
저장소 Settings → Secrets and variables → Actions 에 같은 이름으로 등록돼 있다.

> ⚠️ 투자 추천이 아님. 프리마켓 동전주 급등은 유상증자(희석) 발표 한 방에 꺼지는
> 경우가 흔하다. 진입 전 뉴스/SEC 공시(424B5, S-1) 확인 필수.
