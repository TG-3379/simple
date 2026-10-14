"""텔레그램 메시지 전송. 전송이 실패해도 예외를 밖으로 던지지 않는다(매매를 막지 않기 위해).

설정은 .env 에 둔다 (TGi/.env 또는 trader/.env):
    TELEGRAM_BOT_TOKEN=<BotFather 가 준 토큰>
    TELEGRAM_CHAT_ID=<알림 받을 채팅 id>
토큰은 로그나 화면에 출력하지 않는다.
"""
import logging
import os
import threading
import urllib.parse
import urllib.request
from pathlib import Path

log = logging.getLogger("telegram")
HERE = Path(__file__).resolve().parent


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
        """보내는 중인 메시지가 끝날 때까지 기다린다 (프로그램 종료 직전에 호출)."""
        for t in self._threads:
            t.join(timeout)
