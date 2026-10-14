"""매매 내역 CSV 기록 + 텔레그램 알림. 기록/알림이 실패해도 매매는 멈추지 않는다.

텔레그램 설정은 .env 에 둔다 (TGi/.env 또는 trader/.env):
    TELEGRAM_BOT_TOKEN=<BotFather 가 준 토큰>
    TELEGRAM_CHAT_ID=<알림 받을 채팅 id>
토큰은 로그나 화면에 출력하지 않는다.
"""
import csv
import logging
import os
import threading
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

log = logging.getLogger("reporter")
HERE = Path(__file__).resolve().parent
FIELDS = ["시각", "모드", "구분", "사유", "종목코드", "종목명", "수량", "가격", "금액",
          "평균가", "실현손익", "누적실현손익", "보유수량"]


def load_env() -> None:
    """trader/.env, TGi/.env 의 KEY=VALUE 를 환경변수로 채운다. 이미 있는 환경변수는 덮어쓰지 않는다."""
    for folder in (HERE, HERE.parent):
        f = folder / ".env"
        if not f.is_file():
            continue
        for line in f.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


class Telegram:
    def __init__(self, enabled: bool, send_fn=None):
        """send_fn(text): 시험용으로 전송 동작을 바꿔 끼울 때만 사용."""
        self.token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.chat = os.environ.get("TELEGRAM_CHAT_ID", "")
        self.send_fn = send_fn
        self.enabled = enabled and (send_fn is not None or bool(self.token and self.chat))
        if enabled and not self.enabled:
            log.warning("텔레그램 알림 꺼짐: .env 에 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 가 없음")
        self._threads: list[threading.Thread] = []

    def send(self, text: str) -> None:
        """백그라운드로 전송해서 매매 흐름을 막지 않는다."""
        if not self.enabled:
            return
        t = threading.Thread(target=self._post, args=(text,), daemon=True)
        t.start()
        self._threads.append(t)

    def _post(self, text: str) -> None:
        try:
            if self.send_fn:
                self.send_fn(text)
                return
            data = urllib.parse.urlencode({"chat_id": self.chat, "text": text[:4000]}).encode()
            req = urllib.request.Request(
                f"https://api.telegram.org/bot{self.token}/sendMessage", data=data)
            with urllib.request.urlopen(req, timeout=5) as r:
                r.read()
        except Exception as e:  # 토큰이 들어간 주소가 노출되지 않도록 종류와 코드만 남긴다
            log.warning("텔레그램 전송 실패: %s %s", type(e).__name__, getattr(e, "code", ""))

    def flush(self, timeout: float = 10) -> None:
        for t in self._threads:
            t.join(timeout)


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
        icon = "🟢" if side == "매수" else "🔴"
        lines = [f"{icon} {side} {self.tag}", f"{name}({code})",
                 f"{qty:,}주 @{price:,.0f}원 (합계 {amount:,.0f}원)", f"사유: {reason}"]
        if pnl is not None:
            lines.append(f"실현손익 {pnl:+,.0f}원 / 누적 {cum_pnl:+,.0f}원")
        self.telegram.send("\n".join(lines))

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
