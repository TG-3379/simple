"""매매 내역 CSV 기록 + 알림 문구 만들기. 기록이 실패해도 매매는 멈추지 않는다.
텔레그램 전송 자체는 telegram_notify.py 에 분리되어 있다."""
import csv
import logging
from datetime import datetime
from pathlib import Path

from telegram_notify import Telegram

log = logging.getLogger("reporter")


def trend(v: float) -> str:
    """한국식 색 이모지: 상승/이익은 빨강 🔴, 하락/손실은 파랑 🔵, 0 은 ⚪."""
    return "🔴" if v > 0 else "🔵" if v < 0 else "⚪"


def won(v: float) -> str:
    """금액에 +/- 부호: +41,570원 / -16,556원 (0 은 부호 없이 0원)"""
    return f"{v:+,.0f}원" if round(v) else "0원"


def pct(v: float) -> str:
    """수익률에 +/- 부호: +4.22% / -16.73% (0 은 부호 없이 0.00%)"""
    return f"{v:+.2f}%" if round(v, 2) else "0.00%"


FIELDS =["시각", "모드", "구분", "사유", "종목코드", "종목명", "수량", "가격", "금액",
          "평균가", "실현손익", "누적실현손익", "보유수량"]


class Journal:
    """하루 단위 CSV 파일에 매매 내역을 한 줄씩 덧붙인다. 파일: 매매내역_<모드>_<날짜>.csv"""

    def __init__(self, folder: Path, mode: str):
        self.folder = folder
        self.path = folder / f"매매내역_{mode}_{datetime.now():%Y%m%d}.csv"

    def write(self, row: dict) -> None:
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            new = not self.path.exists()
            with open(self.path, "a", newline="", encoding="utf-8-sig") as f:  # 엑셀에서 한글이 안 깨짐
                w = csv.DictWriter(f, fieldnames=FIELDS)
                if new:
                    w.writeheader()
                w.writerow(row)
        except OSError as e:
            log.warning("CSV 기록 실패: %s", e)


class Reporter:
    def __init__(self, journal: Journal | None, telegram: Telegram, mode: str):
        self.journal, self.telegram, self.mode = journal, telegram, mode
        self.tag = "[dry-run]" if mode == "dry-run" else "[모의투자]" if mode == "live" else f"[{mode}]"
        self._seen: set[str] = set()

    def trade(self, side: str, reason: str, code: str, name: str, qty: int, price: float,
              avg: float, pnl: float | None, cum_pnl: float, held_after: int) -> None:
        amount = qty * price
        if self.journal:
            self.journal.write({
                "시각": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "모드": self.mode,
                "구분": side, "사유": reason, "종목코드": code, "종목명": name,
                "수량": qty, "가격": f"{price:.0f}", "금액": f"{amount:.0f}", "평균가": f"{avg:.0f}",
                "실현손익": "" if pnl is None else f"{pnl:.0f}",
                "누적실현손익": f"{cum_pnl:.0f}", "보유수량": held_after,
            })
        # 빨강/파랑은 손익 표시에만 쓰고, 매수/매도 구분은 다른 아이콘으로 (색이 겹쳐 헷갈리지 않게)
        icon = "🛒" if side == "매수" else "💵"
        label = "매입금액" if side == "매수" else "매도금액"
        # 체결 알림: 제목 한 줄 + 항목마다 한 줄씩 (단가, 수량, 금액, 사유, 매도면 손익)
        lines = [f"{icon} {side} {self.tag} {name}({code})",
                 f"단가: {price:,.0f}원",
                 f"수량: {qty:,}주",
                 f"{label}: {amount:,.0f}원",
                 f"사유: {reason}"]
        if pnl is not None:
            lines.append(f"손익: {trend(pnl)} {won(pnl)} (누적 {won(cum_pnl)})")
        self.telegram.send("\n".join(lines))

    def stock_list(self, title: str, cands: list, limit: int = 3500) -> None:
        """종목 목록 알림: 종목마다 한 줄 (색 이모지, 이름(코드), 현재가, 등락률).
        텔레그램 글자 수 제한 때문에 길면 여러 메시지로 나눠 보낸다."""
        head = f"📋 {title} {self.tag} - {len(cands)}종목"
        if not cands:
            self.telegram.send(f"{head}\n(편입된 종목 없음)")
            return
        rows = [f"{trend(c.change_rate)} {c.name or c.code}({c.code}) {c.price:,.0f}원 {pct(c.change_rate)}"
                for c in cands]
        chunk, size, part = [], len(head), 1
        for row in rows:
            if chunk and size + len(row) + 1 > limit:
                self.telegram.send(f"{head} ({part})\n" + "\n".join(chunk))
                chunk, size, part = [], len(head), part + 1
            chunk.append(row)
            size += len(row) + 1
        self.telegram.send((f"{head} ({part})\n" if part > 1 else f"{head}\n") + "\n".join(chunk))

    def event(self, text: str, once: bool = False) -> None:
        """시작/종료/경고/오류 알림. once=True 면 같은 문구는 한 번만 보낸다."""
        if once:
            if text in self._seen:
                return
            self._seen.add(text)
        self.telegram.send(f"⚠️ {self.tag} {text}")

    def flush(self) -> None:
        self.telegram.flush()


def disabled() -> Reporter:
    """기록도 알림도 하지 않는 Reporter (--mock 등)."""
    return Reporter(None, Telegram(False), "mock")
