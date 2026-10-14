"""시세 조회 (ka10001 주식기본정보요청). --mock 이면 가짜 시세를 쓴다."""
import logging

from models import Candidate

log = logging.getLogger("quotes")
MOCK_PRICES: dict[str, float] = {}  # --mock 에서 main이 채움


def _num(v) -> float:
    try:
        return float(str(v).replace("+", "").replace(",", ""))
    except ValueError:
        return 0.0


def quote(code: str) -> Candidate | None:
    if MOCK_PRICES:
        return Candidate(code, price=MOCK_PRICES.get(code, 0.0))
    from kiwoom import get_client

    b = get_client().fetch_page(
        api_id="ka10001", path="/api/dostk/stkinfo", body={"stk_cd": code},
        cont_yn=None, next_key=None,
    ).body
    if b.get("return_code") not in (0, None):
        log.warning("시세 조회 실패 %s: %s", code, b.get("return_msg"))
        return None
    return Candidate(code, b.get("stk_nm", ""), abs(_num(b.get("cur_prc"))),
                     _num(b.get("flu_rt")), abs(_num(b.get("open_pric"))))
