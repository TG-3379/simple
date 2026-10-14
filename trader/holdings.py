"""계좌에 실제로 보유 중인 종목 조회 (kt00018 계좌평가잔고내역요청). 최대종목수 한도 확인에 쓴다."""


def _num(v) -> float:
    try:
        return float(str(v).replace("+", "").replace(",", ""))
    except ValueError:
        return 0.0


def held_codes(exchange: str = "KRX") -> set[str]:
    """보유수량이 1주 이상인 종목코드 집합. 조회에 실패하면 예외를 던진다(호출한 쪽이 매수를 막아야 함)."""
    from kiwoom import get_client

    client = get_client()
    codes: set[str] = set()
    cont_yn = next_key = None
    for _ in range(20):  # 연속조회
        r = client.fetch_page(
            api_id="kt00018", path="/api/dostk/acnt",
            body={"qry_tp": "1", "dmst_stex_tp": exchange},
            cont_yn=cont_yn, next_key=next_key,
        )
        body = r.body
        if body.get("return_code") not in (None, 0):
            raise RuntimeError(f"보유종목 조회 실패: {body.get('return_msg')}")
        for row in body.get("acnt_evlt_remn_indv_tot", []):
            if _num(row.get("rmnd_qty")) > 0:
                codes.add(str(row["stk_cd"]).lstrip("A"))
        if r.continuation.cont_yn != "Y":
            return codes
        cont_yn, next_key = "Y", r.continuation.next_key
    raise RuntimeError("보유종목 조회 페이지가 너무 많음")
