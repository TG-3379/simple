"""매수(kt10000) / 매도(kt10001). 기본은 로그만 남기는 dry-run, live=True 일 때만 실제 주문."""
import logging

log = logging.getLogger("orders")
LIVE = False
EXCHANGE = "KRX"


def _send(api_id: str, code: str, qty: int, price: int) -> None:
    from kiwoom import get_client

    get_client().fetch_page(
        api_id=api_id, path="/api/dostk/ordr",
        body={"dmst_stex_tp": EXCHANGE, "stk_cd": code, "ord_qty": str(qty),
              "trde_tp": "0" if price else "3",  # 0:보통 3:시장가
              "ord_uv": str(price) if price else ""},
        cont_yn=None, next_key=None,
    )


def buy(code: str, qty: int, price: int = 0) -> None:
    log.info("[%s] 매수 %s %d주 단가=%s", "주문" if LIVE else "dry-run", code, qty, price or "시장가")
    if LIVE:
        _send("kt10000", code, qty, price)


def sell(code: str, qty: int, price: int = 0) -> None:
    log.info("[%s] 매도 %s %d주 단가=%s", "주문" if LIVE else "dry-run", code, qty, price or "시장가")
    if LIVE:
        _send("kt10001", code, qty, price)
