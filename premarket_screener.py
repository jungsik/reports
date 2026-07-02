#!/usr/bin/env python3
"""
premarket_screener.py — 미국 프리마켓 동전주 급등 스크리너 (무LLM, 토큰 0).

데이터 소스: TradingView 스캐너 API (비공식, 무료, 키 불필요)
  POST https://scanner.tradingview.com/america/scan

흐름:
  1) TradingView 스캐너에서 프리마켓 등락률 상위 동전주 수집
  2) 숫자 규칙으로 리스크 플래그 자동 판정 (초저유통 / $1미만 / 허수거래량 / 과열)
  3) 콘솔 표 출력 + reports\ 에 CSV 저장 (+ --notify 시 텔레그램 발송)

프리마켓 시간: 미 동부 04:00~09:30 = 한국시간 17:00~22:30 (서머타임) / 18:00~23:30 (겨울)

사용법:
  python premarket_screener.py                     # 기본: $5 미만, PM +10% 이상
  python premarket_screener.py --max-price 1       # $1 미만 초동전주만
  python premarket_screener.py --min-change 30     # +30% 이상 급등만
  python premarket_screener.py --session regular   # 정규장 급등주 모드 (테스트/장중용)
  python premarket_screener.py --notify            # 텔레그램 발송 (환경변수 필요)
  python premarket_screener.py --ci                # GitHub Actions용: 프리마켓 아니면 조용히 종료

텔레그램 발송에 필요한 환경변수: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

주의: 투자 추천이 아닙니다. 프리마켓 동전주 급등은 유상증자(희석) 발표 한 방에
꺼지는 경우가 흔합니다. 진입 전 반드시 뉴스/SEC 공시(424B5, S-1)를 확인하세요.
"""
import argparse
import html
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

# Windows 콘솔(cp949)에서도 이모지/특수문자 출력되도록 UTF-8 고정
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

REPORT_DIR = Path(__file__).resolve().parent / "reports"
REPORT_DIR.mkdir(parents=True, exist_ok=True)

SCAN_URL = "https://scanner.tradingview.com/america/scan"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Content-Type": "application/json",
    "Origin": "https://www.tradingview.com",
    "Referer": "https://www.tradingview.com/",
}

# 요청 컬럼 (응답 d 배열이 이 순서로 옴)
COLUMNS = [
    "name",                        # 티커
    "description",                 # 종목명
    "close",                       # 전일 종가 (프리마켓 중) / 현재가 (정규장)
    "premarket_close",             # 프리마켓 현재가
    "premarket_change",            # 프리마켓 등락률 %
    "premarket_volume",            # 프리마켓 거래량
    "change",                      # 정규장 등락률 %
    "volume",                      # 정규장 거래량
    "average_volume_10d_calc",     # 10일 평균 거래량
    "relative_volume_10d_calc",    # 상대 거래량 (당일/10일평균)
    "float_shares_outstanding",    # 유통주식수 (float)
    "market_cap_basic",            # 시가총액
    "sector",
    "exchange",
]


def us_eastern_now():
    """미 동부 현재시각. zoneinfo 실패 시(윈도우 tzdata 미설치) DST 근사치 폴백."""
    utc = datetime.now(timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        return utc.astimezone(ZoneInfo("America/New_York"))
    except Exception:
        # 3~10월은 EDT(UTC-4), 나머지 EST(UTC-5) 근사 — 상태 표시용이라 충분
        offset = -4 if 3 <= utc.month <= 10 else -5
        return utc.astimezone(timezone(timedelta(hours=offset)))


def market_session(et: datetime) -> str:
    """현재 미국장 세션 판정: premarket / regular / afterhours / closed"""
    if et.weekday() >= 5:
        return "closed"
    hm = et.hour * 60 + et.minute
    if 4 * 60 <= hm < 9 * 60 + 30:
        return "premarket"
    if 9 * 60 + 30 <= hm < 16 * 60:
        return "regular"
    if 16 * 60 <= hm < 20 * 60:
        return "afterhours"
    return "closed"


def build_payload(args, mode: str) -> dict:
    """TradingView 스캔 요청 바디. mode: 'premarket' 또는 'regular'"""
    change_field = "premarket_change" if mode == "premarket" else "change"
    volume_field = "premarket_volume" if mode == "premarket" else "volume"
    filters = [
        {"left": "exchange", "operation": "in_range",
         "right": ["NASDAQ", "NYSE", "AMEX"]},          # OTC 제외
        {"left": "type", "operation": "in_range", "right": ["stock", "dr"]},
        {"left": "is_primary", "operation": "equal", "right": True},
        {"left": "close", "operation": "in_range",
         "right": [args.min_price, args.max_price]},
        {"left": change_field, "operation": "greater", "right": args.min_change},
        {"left": change_field, "operation": "nempty"},
        {"left": volume_field, "operation": "greater", "right": args.min_volume},
    ]
    return {
        "filter": filters,
        "options": {"lang": "en"},
        "markets": ["america"],
        "symbols": {"query": {"types": []}, "tickers": []},
        "columns": COLUMNS,
        "sort": {"sortBy": change_field, "sortOrder": "desc"},
        "range": [0, args.top],
    }


def scan(args, mode: str) -> pd.DataFrame:
    resp = requests.post(SCAN_URL, json=build_payload(args, mode),
                         headers=HEADERS, timeout=20)
    resp.raise_for_status()
    rows = resp.json().get("data") or []
    if not rows:
        return pd.DataFrame(columns=COLUMNS)
    return pd.DataFrame([r["d"] for r in rows], columns=COLUMNS)


# ---------------------------------------------------------------------------
# 리스크 플래그 — 숫자 규칙, LLM 불필요
# ---------------------------------------------------------------------------
def risk_flags(row, mode: str) -> str:
    flags = []
    flt = row["float_shares_outstanding"]
    pm_vol = row["premarket_volume"] if mode == "premarket" else row["volume"]
    change = row["premarket_change"] if mode == "premarket" else row["change"]
    price = row["premarket_close"] if mode == "premarket" else row["close"]
    if price is None or pd.isna(price):
        price = row["close"]

    if pd.isna(flt):
        flags.append("float불명")
    elif flt < 5e6:
        flags.append("초저유통(<5M)")   # 급등도 급락도 극심
    elif flt < 20e6:
        flags.append("저유통(<20M)")

    if price is not None and not pd.isna(price) and price < 1:
        flags.append("$1미만(상폐권)")

    avg_vol = row["average_volume_10d_calc"]
    if not pd.isna(pm_vol) and not pd.isna(avg_vol) and avg_vol > 0:
        if pm_vol < avg_vol * 0.05:
            flags.append("거래량허수")   # 평소 대비 미미 → 호가 몇 개로 만든 등락률

    if not pd.isna(change) and change > 100:
        flags.append("과열(+100%↑)")    # 펌프/희석 발표 최다 구간

    return " ".join(flags) if flags else "-"


def humanize(n):
    """1234567 -> 1.2M 표기"""
    if n is None or pd.isna(n):
        return "-"
    n = float(n)
    for unit, div in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if abs(n) >= div:
            return f"{n / div:.1f}{unit}"
    return f"{n:.0f}"


# ---------------------------------------------------------------------------
# 텔레그램 알림
# ---------------------------------------------------------------------------
def send_telegram(df: pd.DataFrame, mode: str, et: datetime) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("⚠️  TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 환경변수 없음 → 발송 생략")
        return False

    label = "프리마켓" if mode == "premarket" else "정규장"
    kst = datetime.now(timezone(timedelta(hours=9)))
    lines = [f"🚀 <b>{label} 동전주 급등</b>  "
             f"(ET {et:%H:%M} / KST {kst:%H:%M})", ""]
    for _, r in df.head(15).iterrows():
        ticker = html.escape(str(r["name"]))
        price = r["premarket_close"] if mode == "premarket" else r["close"]
        if price is None or pd.isna(price):
            price = r["close"]
        chg = r["premarket_change"] if mode == "premarket" else r["change"]
        vol = r["premarket_volume"] if mode == "premarket" else r["volume"]
        flag = "" if r["flags"] == "-" else f"  ⚠{html.escape(r['flags'])}"
        lines.append(
            f'<a href="https://finviz.com/quote.ashx?t={ticker}">{ticker}</a>'
            f"  ${price:.2f}  +{chg:.1f}%  V:{humanize(vol)}"
            f"  F:{humanize(r['float_shares_outstanding'])}{flag}"
        )
    lines += ["", "⚠️ 투자 추천 아님 · 진입 전 뉴스/공시(424B5) 확인"]

    resp = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": "\n".join(lines),
              "parse_mode": "HTML", "disable_web_page_preview": True},
        timeout=15,
    )
    ok = resp.ok and resp.json().get("ok")
    print("📨 텔레그램 발송 완료" if ok else f"❌ 텔레그램 발송 실패: {resp.text[:200]}")
    return bool(ok)


def main():
    ap = argparse.ArgumentParser(description="미국 프리마켓 동전주 급등 스크리너")
    ap.add_argument("--max-price", type=float, default=5.0, help="최대 가격 $ (기본 5)")
    ap.add_argument("--min-price", type=float, default=0.1, help="최소 가격 $ (기본 0.1)")
    ap.add_argument("--min-change", type=float, default=10.0, help="최소 등락률 %% (기본 10)")
    ap.add_argument("--min-volume", type=float, default=100_000, help="최소 거래량 (기본 10만주)")
    ap.add_argument("--top", type=int, default=50, help="상위 N개 (기본 50)")
    ap.add_argument("--session", choices=["auto", "premarket", "regular"], default="auto",
                    help="auto: 시간 보고 자동 선택 (기본)")
    ap.add_argument("--notify", action="store_true", help="텔레그램 발송")
    ap.add_argument("--ci", action="store_true",
                    help="GitHub Actions용: 프리마켓 시간이 아니면 스캔 없이 조용히 종료")
    args = ap.parse_args()

    et = us_eastern_now()
    sess = market_session(et)
    print(f"🕐 미 동부시간: {et:%Y-%m-%d %H:%M} ({sess})")

    # CI 모드: 크론이 서머타임/겨울 시간대를 넓게 커버하므로 여기서 세션을 거른다
    if args.ci and args.session != "regular" and sess != "premarket":
        print(f"CI: 프리마켓 아님({sess}) → 종료")
        return

    if args.session == "auto":
        mode = "premarket" if sess == "premarket" else "regular"
        if sess != "premarket":
            print(f"⚠️  지금은 프리마켓이 아님({sess}) → 정규장 등락률 기준으로 폴백.")
            print("    프리마켓 스캔은 한국시간 17:00~22:30(서머타임 기준)에 실행하세요.")
    else:
        mode = args.session

    label = "프리마켓" if mode == "premarket" else "정규장"
    print(f"🔎 {label} 급등 스캔: ${args.min_price}~${args.max_price}, "
          f"+{args.min_change}% 이상, 거래량 {humanize(args.min_volume)} 이상\n")

    df = scan(args, mode)
    if df.empty:
        print("결과 없음. --min-change 를 낮추거나 프리마켓 시간에 다시 실행해 보세요.")
        return

    df["flags"] = df.apply(lambda r: risk_flags(r, mode), axis=1)

    if mode == "premarket":
        out = pd.DataFrame({
            "티커": df["name"],
            "종목명": df["description"].str.slice(0, 28),
            "전일종가": df["close"].map(lambda x: f"${x:.2f}" if pd.notna(x) else "-"),
            "PM가격": df["premarket_close"].map(lambda x: f"${x:.2f}" if pd.notna(x) else "-"),
            "PM등락%": df["premarket_change"].map(lambda x: f"+{x:.1f}" if pd.notna(x) else "-"),
            "PM거래량": df["premarket_volume"].map(humanize),
            "Float": df["float_shares_outstanding"].map(humanize),
            "시총": df["market_cap_basic"].map(humanize),
            "리스크": df["flags"],
        })
    else:
        out = pd.DataFrame({
            "티커": df["name"],
            "종목명": df["description"].str.slice(0, 28),
            "현재가": df["close"].map(lambda x: f"${x:.2f}" if pd.notna(x) else "-"),
            "등락%": df["change"].map(lambda x: f"+{x:.1f}" if pd.notna(x) else "-"),
            "거래량": df["volume"].map(humanize),
            "상대거래량": df["relative_volume_10d_calc"].map(
                lambda x: f"{x:.1f}x" if pd.notna(x) else "-"),
            "Float": df["float_shares_outstanding"].map(humanize),
            "시총": df["market_cap_basic"].map(humanize),
            "리스크": df["flags"],
        })

    print(out.to_string(index=False))
    print(f"\n총 {len(out)}개 종목")

    # CSV 저장 (원본 숫자 그대로 — 후처리용). 파일명은 ET 기준.
    csv_path = REPORT_DIR / f"scan_{mode}_{et:%Y%m%d_%H%M}ET.csv"
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    print(f"💾 저장: {csv_path}")

    if args.notify:
        send_telegram(df, mode, et)
    else:
        print("\n📌 뉴스 확인 (급등 이유 없는 종목은 거르세요):")
        for t in df["name"].head(10):
            print(f"   https://finviz.com/quote.ashx?t={t}")


if __name__ == "__main__":
    main()
