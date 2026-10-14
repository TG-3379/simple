"""config.trading.jsonc 의 '실행' 선택(또는 명령행 인자)대로 종목선택 -> 전략 -> 매수 -> 감시/매도 실행.

예)  python main.py --mock
     python main.py --pick "실시간 조회 순위" --strategy "5% 하락" --buy 분할매수 --sell 스탑로스 분할매도
     python main.py --pick 3 --strategy 4 --buy 2 --sell 1 2     # 번호로 선택 (위와 같은 뜻)
     python main.py --live        # 실제 주문 (기본은 dry-run)
"""
import argparse
import json
import logging
import random
import time
from datetime import datetime
from pathlib import Path

import orders
import pickers
import reporter
import quotes
import strategies
from models import Candidate
from trading import Trader

log = logging.getLogger("main")
CONFIG = Path(__file__).with_name("config.trading.jsonc")
REPORTER = None  # 기록/알림 (main() 에서 만든다)


def load_jsonc(text: str) -> dict:
    """// 줄 주석을 걷어내고 JSON으로 읽는다. 문자열 안의 // 는 그대로 둔다."""
    out, in_str, esc, i = [], False, False, 0
    while i < len(text):
        ch = text[i]
        if in_str:
            out.append(ch)
            esc = (not esc) and ch == "\\"
            if ch == '"' and not esc:
                in_str = False
        elif ch == '"':
            in_str = True
            out.append(ch)
        elif text.startswith("//", i):
            while i < len(text) and text[i] != "\n":
                i += 1
            continue
        else:
            out.append(ch)
        i += 1
    return json.loads("".join(out))

MOCK = [  # --mock: 인증 없이 흐름 검증용 가짜 시세
    Candidate("005930", "삼성전자", 71000, -6.2, 72000),
    Candidate("000660", "SK하이닉스", 180000, -5.5, 185000),
    Candidate("035720", "카카오", 50000, 1.0, 49000),
]


def in_trading_time(common: dict) -> bool:
    t = common.get("거래시간", {})
    now = datetime.now().strftime("%H:%M")
    return t.get("시작", "00:00") <= now <= t.get("종료", "23:59")


def resolve(choice, section: dict) -> str:
    """이름("5% 하락") 또는 번호(4 / "4")로 고른 항목의 이름을 돌려준다. 번호는 항목의 "번호" 값."""
    if choice in section:
        return choice
    for name, body in section.items():
        if str(body.get("번호")) == str(choice):
            return name
    raise SystemExit(f"선택 오류: {choice!r} 는 {[(n, b.get('번호')) for n, b in section.items()]} 에 없음")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pick")
    ap.add_argument("--strategy")
    ap.add_argument("--buy")
    ap.add_argument("--sell", nargs="+")
    ap.add_argument("--live", action="store_true", help="실제 주문 전송(기본 dry-run)")
    ap.add_argument("--mock", action="store_true", help="가짜 시세로 흐름만 검증")
    ap.add_argument("--mock-held", type=int, default=0, help="--mock 에서 계좌에 이미 보유 중인 종목 수")
    ap.add_argument("--ticks", type=int, default=0, help="감시 반복 횟수(0=장 마감까지)")
    a = ap.parse_args()

    cfg = load_jsonc(CONFIG.read_text(encoding="utf-8"))
    common, run = cfg["공통"], cfg["실행"]
    pick = resolve(a.pick or run["종목선택"], cfg["종목선택_상세"])
    strat = resolve(a.strategy or run["전략"], cfg["전략"])
    buy_mode = resolve(a.buy or run["매수"], cfg["매수"])
    sell_modes = [resolve(s, cfg["매도"]) for s in (a.sell or run["매도"])]

    if a.live and not common.get("모의투자", True):
        raise SystemExit("실계좌 주문은 막아 둠: 공통.모의투자=false 는 코드에서 지원하지 않음")
    orders.LIVE = a.live
    orders.EXCHANGE = common.get("거래소", "KRX")
    mode = "live" if a.live else "dry-run"
    log.info("선택: 종목선택=%s 전략=%s 매수=%s 매도=%s (%s)", pick, strat, buy_mode, sell_modes,
             "LIVE(모의투자)" if a.live else "dry-run")

    # 매매 내역 CSV 기록 + 텔레그램 알림 (--mock 은 기록도 알림도 하지 않음)
    global REPORTER
    if a.mock:
        REPORTER = reporter.disabled()
    else:
        reporter.load_env()
        rec, alarm = cfg.get("기록", {}), cfg.get("알림", {})
        journal = reporter.Journal(Path(__file__).with_name(rec.get("폴더", "기록")), mode) \
            if rec.get("CSV", True) else None
        tg_on = alarm.get("텔레그램", False) and (a.live or alarm.get("dry-run에서도", True))
        REPORTER = reporter.Reporter(journal, reporter.Telegram(tg_on), mode)
        if journal:
            log.info("매매 내역 CSV: %s", journal.path)
    REPORTER.event(f"시작: {pick} / {strat} / {buy_mode} / {'+'.join(sell_modes)}")

    # 1. 종목선택 -> 2. 전략 필터
    cands = MOCK if a.mock else pickers.pick(pick, cfg["종목선택"][pick], cfg["종목선택_상세"][pick], strat)
    if a.mock:
        quotes.MOCK_PRICES.update({c.code: c.price for c in MOCK})
    chosen = [c for c in cands if strategies.STRATEGIES[strat](c, cfg["전략"][strat])]
    log.info("[종목선택] 후보 %d -> 전략 통과 %d", len(cands), len(chosen))

    # 3. 매수
    held_fn = (lambda: {f"9{i:05d}" for i in range(a.mock_held)}) if a.mock else None  # mock: 가짜 기보유 종목
    trader = Trader(common, {**cfg["매수"], **cfg["매도"]}, buy_mode, sell_modes, held_fn, REPORTER)
    for c in chosen:
        log.info("  통과: %s(%s) %+.1f%%", c.name, c.code, c.change_rate)
        trader.enter(c)

    # 4. 감시 -> 스탑로스/분할매도/추가매수
    tick = 0
    while a.ticks == 0 or tick < a.ticks:
        if not a.mock and not in_trading_time(common):
            log.info("거래시간 종료 -> 감시 종료")
            break
        if not any(p.qty > 0 for p in trader.positions.values()):
            log.info("보유 종목 없음 -> 종료")
            break
        for pos in list(trader.positions.values()):
            if a.mock:
                quotes.MOCK_PRICES[pos.code] *= 1 + random.uniform(-0.015, 0.02)
            q = quotes.quote(pos.code)
            if q:
                trader.watch(pos, q.price)
        tick += 1
        time.sleep(0 if a.mock else common.get("감시주기초", 10))
    log.info("종료: 매매 %d회, 실현손익 %+.0f", trader.trades, trader.realized)
    REPORTER.event(f"종료: 매매 {trader.trades}회, 실현손익 {trader.realized:+,.0f}원")
    REPORTER.flush()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        main()
    except KeyboardInterrupt:
        if REPORTER:
            REPORTER.event("사용자가 중단함 (Ctrl+C)")
            REPORTER.flush()
        raise
    except Exception as e:  # 예상 못한 오류도 알림으로 남긴 뒤 그대로 다시 던진다
        if REPORTER:
            REPORTER.event(f"오류로 중단: {type(e).__name__}: {str(e)[:150]}")
            REPORTER.flush()
        raise
